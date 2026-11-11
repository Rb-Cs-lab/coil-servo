"""Integration bench for coil_servo_top: the full signal path (error ->
decimators -> PI -> mux -> DAC word, flip FSM and over-current trip on the
pins) against the MVP plant (model/coil_servo_model/plant.py), configured
through the real register map (registers.pack_cfg).

Unit benches already prove each core bit-exact; this bench checks the
*integration*: system behavior through the exact Verilog that Vivado will
synthesize, driven and observed only via its external ports.

The bench plays the role of the analog world AND the control computer:
it converts the drive-frame plant current into board-frame ADC codes
(measured current changes sign with bridge polarity), and flips its
setpoint sign when the polarity output changes -- exactly what the real
control system must do.

A pin-level safety monitor runs under every test (MVP safety invariants):
  * OUT2 is exactly 0 and DIO2 (boost) is low unless a test opts in;
  * both DACs are exactly 0 whenever the bridge-enable pin is low;
  * the polarity pin only changes after the bridge has been open for at
    least the dead time, and the bridge stays open for at least the dead
    time after it (the edge sits strictly inside the enable-low window).
"""

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge

from coil_servo_host.config import load_channel
from coil_servo_model import (BENCH_CHANNELS, CHANNELS, Plant, adc_quantize,
                              encode_gain, slew_rate_code)
from coil_servo_model.analysis import overshoot_frac, settling_time, suggest_gains
from coil_servo_model.loop import T_S
from coil_servo_model.registers import MAP_REV, parse_sts, pack_cfg
from tb_util import reset, start_clock

MOT = CHANNELS["mot"]
FLOOR = round(0.9 * MOT.dead_zone * 8192)        # RUN-only floor, counts


def counts(amps: float) -> int:
    """Amps -> counts of I_FS (sensor scale), as host config.py does."""
    return round(amps / MOT.I_FS * 8192)


# flip/stop timing, small for sim speed (clock ticks @ 8 ns)
FSM_CFG = dict(zero_win=41, zero_holdoff=3, deadtime=20, settle=400,
               flip_timeout=0)
TRIP_CFG = dict(trip_level=counts(100.0), trip_ticks=4,
                trip_timeout=375_000)            # 3 ms
SLEW_CFG = dict(slew_up=slew_rate_code(200e3, MOT.I_FS),      # 200 A/ms
                slew_down=slew_rate_code(50e3, MOT.I_FS))     # 50 A/ms


def gains():
    kp, ki_tick = suggest_gains(MOT)
    kp_m, kp_s = encode_gain(kp, 5)
    ki_m, ki_s = encode_gain(ki_tick, 6)
    return dict(kp_mant=kp_m, kp_shift=kp_s, ki_mant=ki_m, ki_shift=ki_s)


def run_cfg(**over):
    base = dict(servo_enable=1, sp_source=1, out_clamp=MOT.clamp_counts,
                deadband=8, u_floor=FLOOR, **gains(), **FSM_CFG, **TRIP_CFG,
                **SLEW_CFG)
    base.update(over)
    return pack_cfg(**base)


# Low-current bench profile: the register values the host derives from
# channels.toml [mot_bench], unchanged (docs/bench_supply_test.md).
BENCH = BENCH_CHANNELS["mot_bench"]
BENCH_REGS = load_channel("mot_bench")["cfg"]


def bench_cfg(**over):
    base = dict(BENCH_REGS, servo_enable=1, sp_source=1)
    base.update(over)
    return pack_cfg(**base)


def sts(dut) -> dict:
    return parse_sts(int(dut.sts_data.value))


def pack_adc(meas_code: int, in2_code: int) -> int:
    return ((in2_code & 0x3FFF) << 16) | (meas_code & 0x3FFF)


def dacs(dut):
    w = int(dut.m_axis_tdata.value)
    o1, o2 = w & 0x3FFF, (w >> 16) & 0x3FFF
    return o1 - 0x4000 * (o1 >> 13), o2 - 0x4000 * (o2 >> 13)


async def safety_monitor(dut, allow):
    """Runs every clock; `allow` is a dict a test may flip ('out2')."""
    deadtime = FSM_CFG["deadtime"]
    prev_pol = int(dut.bridge_polarity_o.value)
    low_for = 10 ** 9           # clocks the bridge has been open
    since_pol_change = 10 ** 9  # clocks since the last polarity edge
    prev_ben = int(dut.bridge_enable_o.value)
    while True:
        await RisingEdge(dut.aclk)
        ben = int(dut.bridge_enable_o.value)
        pol = int(dut.bridge_polarity_o.value)
        o1, o2 = dacs(dut)
        if not allow["out2"]:
            assert o2 == 0, f"OUT2 = {o2} with out2_enable = 0"
        assert int(dut.boost_o.value) == 0, "DIO2 (boost) driven in the MVP"
        if not ben:
            assert (o1, o2) == (0, 0), "DAC driving with the bridge open"
        if pol != prev_pol:
            assert ben == 0, "POLARITY CHANGED WITH THE BRIDGE ENABLED"
            assert low_for >= deadtime, \
                f"polarity edge only {low_for} clocks after the bridge opened"
            since_pol_change = 0
            prev_pol = pol
        if ben and not prev_ben:
            assert since_pol_change >= deadtime, \
                f"bridge re-closed {since_pol_change} clocks after a polarity edge"
        low_for = 0 if ben else low_for + 1
        since_pol_change += 1
        prev_ben = ben


async def setup(dut, cfg: int, arm_at_load: int = 0):
    cocotb.start_soon(start_clock(dut))
    dut.cfg_data.value = cfg
    dut.s_axis_tdata.value = 0
    dut.s_axis_tvalid.value = 1
    dut.flip_req_i.value = 0
    dut.arm_i.value = arm_at_load
    dut.fault_i.value = 0
    await reset(dut)
    allow = {"out2": False}
    cocotb.start_soon(safety_monitor(dut, allow))
    return allow


async def arm(dut):
    """A fresh rising edge on DIO4."""
    dut.arm_i.value = 0
    await ClockCycles(dut.aclk, 5)
    dut.arm_i.value = 1
    await ClockCycles(dut.aclk, 10)


class World:
    """MVP plant + frame conversion + one servo tick = 128 fast samples."""

    def __init__(self, dut, sp_amps: float, cfg_extra=None, average=False,
                 ch=MOT, cfg_fn=None):
        """average=True: apply the mean OUT1 over the tick's 128 clocks
        to the plant (what the gate RC sees with the dither on) instead of
        one sample at the end of the tick. ch / cfg_fn: the plant channel
        and the CFG builder (default: the MOT with run_cfg)."""
        self.dut = dut
        self.average = average
        self.ch = ch
        self.cfg_fn = cfg_fn or run_cfg
        self.plant = Plant(ch)
        self.sp_amps = sp_amps       # magnitude of the requested current
        self.cfg_extra = cfg_extra or {}
        self.t = 0.0

    async def tick(self):
        dut = self.dut
        ch = self.ch
        pol = int(dut.bridge_polarity_o.value)
        sign = -1 if pol else 1
        # board frame: measured current and setpoint change sign with polarity
        meas = adc_quantize(sign * self.plant.I / ch.I_FS)
        sp = adc_quantize(sign * self.sp_amps / ch.I_FS)
        dut.s_axis_tdata.value = pack_adc(meas, sp)
        dut.cfg_data.value = self.cfg_fn(setpoint=sp, **self.cfg_extra)
        if self.average:
            tot = 0
            for _ in range(128):
                await RisingEdge(dut.aclk)
                o1, o2 = dacs(dut)
                assert o2 == 0 and o1 <= ch.clamp_counts
                tot += o1
            o1 = tot / 128
        else:
            await ClockCycles(dut.aclk, 128)
            o1, o2 = dacs(dut)
        assert o2 == 0

        # apply the DAC output to the plant for one tick
        self.plant.bridge_enabled = bool(int(dut.bridge_enable_o.value))
        self.plant.step(max(o1, 0) / 8192.0, 0.0, False, T_S)
        self.t += T_S
        return o1


@cocotb.test()
async def reset_state_is_off(dut):
    await setup(dut, cfg=0)
    assert dacs(dut) == (0, 0), "DACs not zero"
    assert dut.bridge_enable_o.value == 0
    assert dut.bridge_polarity_o.value == 0
    assert dut.boost_o.value == 0
    s = sts(dut)
    assert s["fsm_state_name"] == "IDLE"
    assert s["tripped"] == 0
    assert s["map_rev"] == MAP_REV, "STS map revision != registers.MAP_REV"
    # heartbeat (DIV_LOG2=6 in sim) must toggle regardless of configuration
    v0 = int(dut.heartbeat_o.value)
    await ClockCycles(dut.aclk, 100)
    assert int(dut.heartbeat_o.value) != v0, "heartbeat not toggling"


@cocotb.test()
async def arm_level_high_at_load_does_not_drive(dut):
    """Safety invariant 2 (MVP): fully configured and enabled, arm already
    high at load -> nothing happens until the arm line is re-raised."""
    await setup(dut, run_cfg(setpoint=counts(20.0)), arm_at_load=1)
    await ClockCycles(dut.aclk, 2000)
    assert sts(dut)["fsm_state_name"] == "IDLE"
    assert dut.bridge_enable_o.value == 0 and dacs(dut) == (0, 0)
    await arm(dut)
    assert sts(dut)["fsm_state_name"] == "RUN"
    assert dut.bridge_enable_o.value == 1


@cocotb.test()
async def floor_holds_out1_below_the_dead_zone_in_run_only(dut):
    """Armed with a zero setpoint: OUT1 rests on u_floor (no current flows,
    the plant's dead zone is above it) and STS reports u_floor. After a
    graceful stop the outputs are exactly zero again."""
    await setup(dut, run_cfg(setpoint=0))
    await arm(dut)
    world = World(dut, sp_amps=0.0)
    for _ in range(50):
        o1 = await world.tick()
    assert o1 == FLOOR, f"OUT1 {o1} != floor {FLOOR}"
    assert world.plant.I == 0.0, "floor must sit below the dead zone"
    s = sts(dut)
    assert s["u_floor"] == 1 and s["u14"] == FLOOR

    world.cfg_extra = dict(servo_enable=0)
    for _ in range(50):
        await world.tick()
    assert sts(dut)["fsm_state_name"] == "IDLE"
    assert dacs(dut) == (0, 0)


@cocotb.test()
async def closed_loop_step_settles(dut):
    """Arm, command 50 A via the setpoint register: settles to 0.1 % in
    < 1 ms through the full HDL chain, OUT1 only, no overshoot."""
    await setup(dut, run_cfg(setpoint=0))
    await arm(dut)
    assert sts(dut)["fsm_state_name"] == "RUN"

    world = World(dut, sp_amps=50.0)
    trace_t, trace_i = [], []
    for _ in range(1300):                       # 1.33 ms
        o1 = await world.tick()
        assert FLOOR <= o1 <= MOT.clamp_counts
        trace_t.append(world.t)
        trace_i.append(world.plant.I)

    ts = settling_time(trace_t, trace_i, 50.0)
    assert ts is not None and ts < 1e-3, f"settled in {ts}"
    assert overshoot_frac(trace_i, 50.0) < 0.01
    s = sts(dut)
    assert s["fsm_state_name"] == "RUN"
    assert abs(s["i_meas"] - adc_quantize(50.0 / MOT.I_FS) * 128) <= 256, \
        "STS current readback disagrees with the plant"
    assert s["u14"] > FLOOR and s["out_sat"] == 0 and s["u_floor"] == 0


@cocotb.test()
async def slew_limited_steps_neither_overshoot_nor_undershoot(dut):
    """The control computer STEPS the setpoint 0 -> 70 A, then 70 -> 20 A
    (the cases that overshoot ~20 % / undershoot as raw steps on the MVP
    plant). The firmware limiter turns them into 200 A/ms and 50 A/ms
    ramps: no overshoot, no undershoot, settled ~1 ms after each ramp."""
    await setup(dut, run_cfg(setpoint=0))
    await arm(dut)

    world = World(dut, sp_amps=70.0)
    t0, tr_t, tr_i = world.t, [], []
    for _ in range(1400):                       # 0.35 ms ramp + ~1 ms
        await world.tick()
        tr_t.append(world.t - t0)
        tr_i.append(world.plant.I)
    assert overshoot_frac(tr_i, 70.0) < 0.01, f"overshoot {max(tr_i):.1f} A"
    ts = settling_time(tr_t, tr_i, 70.0)
    assert ts is not None and ts < 0.35e-3 + 1e-3, f"settled in {ts}"

    world.sp_amps = 20.0
    t0, tr_t, tr_i = world.t, [], []
    for _ in range(2100):                       # 1.0 ms ramp + ~1 ms
        await world.tick()
        tr_t.append(world.t - t0)
        tr_i.append(world.plant.I)
    assert min(tr_i) > 0.99 * 20.0, f"undershoot to {min(tr_i):.2f} A"
    # band +-0.1 A (0.1 % of rated): +-0.1 % of 20 A is 20 mA, below one
    # OUT1 count of the gain-8 stage (test_actuator_resolution_dither)
    ts = settling_time(tr_t, tr_i, 20.0, tol_frac=0.1 / 20.0)
    assert ts is not None and ts < 1.0e-3 + 1e-3, f"settled in {ts}"


@cocotb.test()
async def dither_removes_the_one_count_hunting(dut):
    """Steady 30 A hold, OUT1 averaged per clock as the gate does: without
    dither the current hunts between two OUT1 codes (~50 mA p-p, wider
    than +-0.1 %); with dither_enable it holds within one ADC count."""
    ripple = {}
    for dither in (0, 1):
        await setup(dut, run_cfg(setpoint=0, dither_enable=dither))
        await arm(dut)
        world = World(dut, sp_amps=30.0, average=True,
                      cfg_extra=dict(dither_enable=dither))
        tail = []
        for k in range(1400):
            await world.tick()
            if k >= 1100:                       # settled (~0.8 ms) + margin
                tail.append(world.plant.I)
        ripple[dither] = max(tail) - min(tail)
        assert abs(sum(tail) / len(tail) - 30.0) < 2 * MOT.amps_per_lsb
    assert ripple[0] > 0.03, f"expected hunting without dither: {ripple[0]}"
    assert ripple[1] < 0.3 * ripple[0], f"dither ripple {ripple}"
    assert ripple[1] < 1e-3 * 30.0, "dithered hold outside +-0.1 %"


@cocotb.test()
async def snap_off_freewheels_at_full_speed(dut):
    """At 80 A the control computer steps the setpoint to zero: the snap
    window takes it at once (no 50 A/ms ramp, which would need 1.6 ms) and
    the coil freewheels to below the zero window in ~1.1 ms, bridge closed
    throughout."""
    await setup(dut, run_cfg(setpoint=0))
    await arm(dut)
    world = World(dut, sp_amps=80.0)
    for _ in range(1700):
        await world.tick()
    assert abs(world.plant.I - 80.0) < 0.2, f"not at 80 A: {world.plant.I}"

    world.sp_amps = 0.0
    zero_win_amps = FSM_CFG["zero_win"] * MOT.amps_per_lsb
    for k in range(2000):
        await world.tick()
        assert int(dut.bridge_enable_o.value) == 1, "snap-off opened the bridge"
        if world.plant.I < zero_win_amps:
            break
    t_off = (k + 1) * T_S
    assert t_off < 1.3e-3, f"snap-off took {t_off * 1e3:.2f} ms"
    assert sts(dut)["fsm_state_name"] == "RUN"


@cocotb.test()
async def flip_sequence_through_the_pins(dut):
    """Run at 30 A, request a flip on the DIO3 pin, and follow the whole
    sequence via the external pins only: the current freewheels down with
    the bridge closed, the bridge opens only inside the zero window, and
    the loop re-settles at the flipped polarity."""
    await setup(dut, run_cfg(setpoint=0))
    await arm(dut)

    world = World(dut, sp_amps=30.0)
    for _ in range(1000):
        await world.tick()
    assert abs(world.plant.I - 30.0) < 0.1, "did not reach 30 A before flip"

    # flip request on the pin (long enough to survive the 2FF synchronizer)
    dut.flip_req_i.value = 1
    await ClockCycles(dut.aclk, 10)
    dut.flip_req_i.value = 0

    saw_bridge_open = False
    i_at_open = None
    pol_before = int(dut.bridge_polarity_o.value)
    for _ in range(3000):
        await world.tick()
        if not saw_bridge_open and int(dut.bridge_enable_o.value) == 0:
            saw_bridge_open = True
            i_at_open = abs(world.plant.I)
        if saw_bridge_open and sts(dut)["fsm_state_name"] == "RUN":
            break
    else:
        raise AssertionError(f"flip never completed; state {sts(dut)['fsm_state_name']}")

    zero_win_amps = FSM_CFG["zero_win"] * MOT.amps_per_lsb
    assert i_at_open < 2 * zero_win_amps, \
        f"bridge opened at {i_at_open:.2f} A (window {zero_win_amps:.2f} A)"
    assert int(dut.bridge_polarity_o.value) == 1 - pol_before

    # loop must re-settle at the new polarity (drive-frame current -> 30 A)
    for _ in range(1300):
        await world.tick()
    assert abs(world.plant.I - 30.0) < 0.1, \
        f"did not re-settle after flip (I = {world.plant.I:.2f} A)"
    s = sts(dut)
    assert s["fsm_state_name"] == "RUN"
    assert s["sp_sign_mismatch"] == 0, "bench sp sign tracking is wrong"


@cocotb.test()
async def over_current_trip_through_the_pins(dut):
    """Trip level 40 A, command 50 A: the DAC drops to zero within a few
    ticks of crossing 40 A, the bridge stays closed while the coil
    freewheels, opens inside the zero window, the FSM latches in TRIPPED,
    and only trip_ack plus a fresh arm edge bring it back."""
    trip = dict(trip_level=counts(40.0))
    await setup(dut, run_cfg(setpoint=0, **trip))
    await arm(dut)

    world = World(dut, sp_amps=50.0, cfg_extra=trip)
    peak = 0.0
    crossed_at = tripped_at = opened_at = None
    i_at_open = None
    for k in range(3000):
        o1 = await world.tick()
        peak = max(peak, world.plant.I)
        s = sts(dut)
        if crossed_at is None and world.plant.I > 40.0:
            crossed_at = k
        if tripped_at is None and s["tripped"]:
            tripped_at = k
            assert s["fsm_state_name"] == "TRIP"
        if tripped_at is not None:
            assert o1 == 0, "DAC driving after the trip"
        if opened_at is None and tripped_at is not None and \
                not int(dut.bridge_enable_o.value):
            opened_at = k
            i_at_open = world.plant.I
        if s["fsm_state_name"] == "TRIPPED":
            break
    else:
        raise AssertionError(f"never latched; state {sts(dut)['fsm_state_name']}")

    assert crossed_at is not None and tripped_at is not None
    assert tripped_at - crossed_at <= TRIP_CFG["trip_ticks"] + 3, \
        f"trip took {tripped_at - crossed_at} ticks"
    assert peak < 45.0, f"current reached {peak:.1f} A"
    assert opened_at - tripped_at > 100, "bridge opened before the freewheel"
    zero_win_amps = FSM_CFG["zero_win"] * MOT.amps_per_lsb
    assert i_at_open < 2 * zero_win_amps, f"bridge opened at {i_at_open:.2f} A"

    # latched: enabled and armed, still nothing
    for _ in range(50):
        await world.tick()
    assert sts(dut)["fsm_state_name"] == "TRIPPED"
    assert dut.bridge_enable_o.value == 0

    # host acknowledge -> IDLE; arm is still high, so no restart yet
    world.cfg_extra = dict(trip, trip_ack=1)
    await world.tick()
    world.cfg_extra = dict(trip, trip_ack=0)
    world.sp_amps = 20.0
    for _ in range(20):
        await world.tick()
    assert sts(dut)["fsm_state_name"] == "IDLE"
    assert dut.bridge_enable_o.value == 0

    await arm(dut)
    for _ in range(1200):
        await world.tick()
    assert sts(dut)["fsm_state_name"] == "RUN"
    assert abs(world.plant.I - 20.0) < 0.1, "did not resume after re-arm"


@cocotb.test()
async def bench_profile_holds_2a_and_trips_below_the_supply_limit(dut):
    """The mot_bench register values (clamp 4000, trip 2.8 A = 115 counts,
    4 samples) against one MOT coil on a 3 A current-limited supply (no IN1
    offset in the bench): the loop holds 2 A within one ADC count (24 mA;
    dither on, gate-averaged), then a 2.9 A setpoint trips the firmware
    before the supply's limit (~123 counts) is reached, the coil
    freewheels, the bridge opens inside the zero window, and the FSM
    latches in TRIPPED."""
    await setup(dut, bench_cfg(setpoint=0))
    await arm(dut)
    world = World(dut, sp_amps=2.0, ch=BENCH, cfg_fn=bench_cfg)

    # from the 90 % floor a 2 A step takes ~2.2 ms (test_bench.py)
    for _ in range(2400):
        o1 = await world.tick()
        assert BENCH_REGS["u_floor"] <= o1 <= BENCH.clamp_counts
    world.average = True                         # gate-averaged dither
    tail = []
    for k in range(400):
        await world.tick()
        if k >= 200:
            tail.append(world.plant.I)
    assert abs(sum(tail) / len(tail) - 2.0) < BENCH.amps_per_lsb, \
        f"hold at {sum(tail) / len(tail):.4f} A"
    assert max(tail) - min(tail) < 2 * BENCH.amps_per_lsb, "hold ripple"
    assert sts(dut)["fsm_state_name"] == "RUN"

    trip_amps = BENCH_REGS["trip_level"] * BENCH.amps_per_lsb
    world.average = False
    world.sp_amps = 2.9
    peak, i_at_open, tripped = 0.0, None, False
    for _ in range(1000):
        o1 = await world.tick()
        peak = max(peak, world.plant.I)
        s = sts(dut)
        tripped = tripped or bool(s["tripped"])
        if tripped:
            assert o1 == 0, "DAC driving after the trip"
        if i_at_open is None and tripped and not int(dut.bridge_enable_o.value):
            i_at_open = world.plant.I
        if s["fsm_state_name"] == "TRIPPED":
            break
    else:
        raise AssertionError(f"never latched; state {sts(dut)['fsm_state_name']}")
    assert trip_amps < peak < BENCH.I_supply_max - 0.05, \
        f"peak {peak:.3f} A (trip {trip_amps:.3f} A, supply {BENCH.I_supply_max} A)"
    zero_win_amps = BENCH_REGS["zero_win"] * BENCH.amps_per_lsb
    assert i_at_open is not None and i_at_open < 2 * zero_win_amps, \
        f"bridge opened at {i_at_open} A"


@cocotb.test()
async def open_loop_drives_output_through_clamp(dut):
    """open_loop=1: the setpoint register drives the output stage directly,
    still clamped and still gated by the bridge. MVP (out2_enable = 0): a
    negative setpoint drives nothing; the legacy OUT2 branch still works
    when explicitly enabled."""
    allow = await setup(dut, run_cfg(open_loop=1, setpoint=3000))
    await arm(dut)
    await ClockCycles(dut.aclk, 10)

    assert dacs(dut) == (3000, 0)
    dut.cfg_data.value = run_cfg(open_loop=1, setpoint=-3000)
    await ClockCycles(dut.aclk, 10)
    assert dacs(dut) == (0, 0), "OUT2 must stay at 0 with out2_enable = 0"
    allow["out2"] = True
    dut.cfg_data.value = run_cfg(open_loop=1, setpoint=-3000, out2_enable=1)
    await ClockCycles(dut.aclk, 10)
    assert dacs(dut) == (0, 3000)
    dut.cfg_data.value = run_cfg(open_loop=1, setpoint=8000)  # beyond clamp
    await ClockCycles(dut.aclk, 10)
    assert dacs(dut) == (MOT.clamp_counts, 0), "open loop must respect the clamp"
    allow["out2"] = False
    dut.arm_i.value = 0                    # back to IDLE: graceful stop path
    dut.cfg_data.value = run_cfg(open_loop=1, setpoint=3000, servo_enable=0)
    await ClockCycles(dut.aclk, 3000)      # ramp-to-zero + deadtime
    assert dacs(dut) == (0, 0), "bridge open must silence open-loop drive too"


@cocotb.test()
async def capture_sel_streams_decimated_pairs(dut):
    """capture_sel=1: the monitor stream emits one {e[21:6], i[21:6]} word
    per PI tick instead of raw ADC samples."""
    await setup(dut, run_cfg(capture_sel=1, setpoint=2000, slew_up=65535))
    await arm(dut)
    meas = 500
    dut.s_axis_tdata.value = pack_adc(meas, 0)
    await ClockCycles(dut.aclk, 600)     # setpoint ramp (4 counts/clock)

    words = []
    for _ in range(3 * 128 + 10):
        await RisingEdge(dut.aclk)
        if int(dut.m01_axis_tvalid.value):
            words.append(int(dut.m01_axis_tdata.value))
    assert 2 <= len(words) <= 4, f"expected ~3 decimated words, got {len(words)}"
    w = words[-1]
    i16 = w & 0xFFFF
    e16 = (w >> 16) & 0xFFFF
    i16 -= 0x10000 * (i16 >> 15)
    e16 -= 0x10000 * (e16 >> 15)
    assert i16 == (meas * 128) >> 6, f"decimated measurement wrong: {i16}"
    assert e16 == ((2000 - meas) * 128) >> 6, f"decimated error wrong: {e16}"


@cocotb.test()
async def dio_invert_flips_arm_sense(dut):
    """Inverted arm input: physically low = armed. Low at load is a level,
    not an edge; a physical high-then-low arms."""
    await setup(dut, run_cfg(dio_invert=0b010, setpoint=0), arm_at_load=0)
    await ClockCycles(dut.aclk, 20)
    assert sts(dut)["fsm_state_name"] == "IDLE"
    assert sts(dut)["armed"] == 1
    dut.arm_i.value = 1                           # physically high = disarmed
    await ClockCycles(dut.aclk, 5)
    dut.arm_i.value = 0                           # edge into armed
    await ClockCycles(dut.aclk, 10)
    assert sts(dut)["fsm_state_name"] == "RUN"
