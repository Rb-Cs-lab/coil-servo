"""Integration bench for coil_servo_top: the full signal path (error ->
decimators -> PI -> mux -> DAC word, flip FSM on the pins) against the
Python plant, configured through the real register map (registers.pack_cfg).

Unit benches already prove each core bit-exact; this bench checks the
*integration*: system behavior through the exact Verilog that Vivado will
synthesize, driven and observed only via its external ports.

The bench plays the role of the analog world AND the control computer:
it converts the drive-frame plant current into board-frame ADC codes
(measured current changes sign with bridge polarity), and flips its
setpoint sign when the polarity output changes -- exactly what the real
control system must do.
"""

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge

from coil_servo_model import CHANNELS, adc_quantize, encode_gain
from coil_servo_model.analysis import settling_time, suggest_gains
from coil_servo_model.loop import T_S
from coil_servo_model.plant import Plant
from coil_servo_model.registers import parse_sts, pack_cfg
from tb_util import reset, start_clock

MOT = CHANNELS["mot"]

# flip/stop timing, small for sim speed (clock ticks @ 8 ns)
FSM_CFG = dict(zero_win=41, zero_holdoff=3, deadtime=20, settle=400,
               flip_timeout=0)


def gains():
    kp, ki_tick = suggest_gains(MOT)
    kp_m, kp_s = encode_gain(kp, 5)
    ki_m, ki_s = encode_gain(ki_tick, 6)
    return dict(kp_mant=kp_m, kp_shift=kp_s, ki_mant=ki_m, ki_shift=ki_s)


def run_cfg(**over):
    base = dict(servo_enable=1, sp_source=1, out_clamp=MOT.clamp_counts,
                deadband=8, **gains(), **FSM_CFG)
    base.update(over)
    return pack_cfg(**base)


def sts(dut) -> dict:
    return parse_sts(int(dut.sts_data.value))


def pack_adc(meas_code: int, in2_code: int) -> int:
    return ((in2_code & 0x3FFF) << 16) | (meas_code & 0x3FFF)


async def setup(dut, cfg: int):
    cocotb.start_soon(start_clock(dut))
    dut.cfg_data.value = cfg
    dut.s_axis_tdata.value = 0
    dut.s_axis_tvalid.value = 1
    dut.flip_req_i.value = 0
    dut.arm_i.value = 0
    dut.fault_i.value = 0
    await reset(dut)


class World:
    """Plant + frame conversion + one servo tick = 128 fast samples."""

    def __init__(self, dut, sp_amps: float, cfg_extra=None):
        self.dut = dut
        self.plant = Plant(MOT)
        self.sp_amps = sp_amps       # magnitude of the requested current
        self.cfg_extra = cfg_extra or {}
        self.t = 0.0

    async def tick(self, boost=None):
        """boost=None: obey the DUT's boost_o pin (tests auto-boost)."""
        dut = self.dut
        if boost is None:
            boost = bool(int(dut.boost_o.value))
        pol = int(dut.bridge_polarity_o.value)
        sign = -1 if pol else 1
        # board frame: measured current and setpoint change sign with polarity
        meas = adc_quantize(sign * self.plant.I / MOT.I_FS)
        sp = adc_quantize(sign * self.sp_amps / MOT.I_FS)
        dut.s_axis_tdata.value = pack_adc(meas, sp)
        dut.cfg_data.value = run_cfg(setpoint=sp, **self.cfg_extra)
        await ClockCycles(dut.aclk, 128)

        # apply the DAC outputs to the plant for one tick
        word = int(dut.m_axis_tdata.value)
        o1 = word & 0x3FFF
        o2 = (word >> 16) & 0x3FFF
        o1 -= 0x4000 * (o1 >> 13)     # sign-extend s14
        o2 -= 0x4000 * (o2 >> 13)
        assert not (o1 != 0 and o2 != 0), "OUT1 and OUT2 both active"
        if int(dut.bridge_enable_o.value):
            self.plant.step(max(o1, 0) / 8192.0, max(o2, 0) / 8192.0,
                            boost, T_S)
        else:
            assert (o1, o2) == (0, 0), "DAC driving with the bridge open"
            self.plant.step(0.0, 0.0, False, T_S)
        self.t += T_S
        return o1, o2


@cocotb.test()
async def reset_state_is_off(dut):
    await setup(dut, cfg=0)
    assert int(dut.m_axis_tdata.value) & 0x3FFF3FFF == 0, "DACs not zero"
    assert dut.bridge_enable_o.value == 0
    assert dut.bridge_polarity_o.value == 0
    assert dut.boost_o.value == 0
    s = sts(dut)
    assert s["fsm_state_name"] == "IDLE"
    # heartbeat (DIV_LOG2=6 in sim) must toggle regardless of configuration
    v0 = int(dut.heartbeat_o.value)
    await ClockCycles(dut.aclk, 100)
    assert int(dut.heartbeat_o.value) != v0, "heartbeat not toggling"


@cocotb.test()
async def closed_loop_step_settles(dut):
    """Arm, command 50 A via the setpoint register, boost during the ramp:
    settles to 0.1% in < 1 ms through the full HDL chain."""
    await setup(dut, run_cfg(setpoint=0))
    dut.arm_i.value = 1
    await ClockCycles(dut.aclk, 10)
    assert sts(dut)["fsm_state_name"] == "RUN"

    world = World(dut, sp_amps=50.0)
    trace_t, trace_i = [], []
    for k in range(1000):                       # 1.024 ms
        await world.tick(boost=world.t < 0.3e-3)
        trace_t.append(world.t)
        trace_i.append(world.plant.I)

    ts = settling_time(trace_t, trace_i, 50.0)
    assert ts is not None and ts < 1e-3, f"settled in {ts}"
    s = sts(dut)
    assert s["fsm_state_name"] == "RUN"
    assert abs(s["i_meas"] - adc_quantize(50.0 / MOT.I_FS) * 128) <= 256, \
        "STS current readback disagrees with the plant"
    assert s["u14"] > 0 and s["out_sat"] == 0


@cocotb.test()
async def flip_sequence_through_the_pins(dut):
    """Run at 30 A, request a flip on the DIO3 pin, and follow the whole
    sequence via the external pins only. Asserts the current was inside the
    zero window when the bridge opened and that the loop re-settles at the
    flipped polarity."""
    await setup(dut, run_cfg(setpoint=0, boost_mode=1))
    dut.arm_i.value = 1
    await ClockCycles(dut.aclk, 10)

    # boost follows the DUT's own boost_o pin: auto-boost must engage for
    # the ramp and drop by ~87.5% of the setpoint
    world = World(dut, sp_amps=30.0, cfg_extra=dict(boost_mode=1))
    saw_boost = False
    for _ in range(600):
        await world.tick()
        saw_boost |= bool(int(dut.boost_o.value))
    assert abs(world.plant.I - 30.0) < 0.1, "did not reach 30 A before flip"
    assert saw_boost, "auto-boost never engaged for the arming ramp"
    assert int(dut.boost_o.value) == 0, "auto-boost stuck on at setpoint"

    # flip request on the pin (long enough to survive the 2FF synchronizer)
    dut.flip_req_i.value = 1
    await ClockCycles(dut.aclk, 10)
    dut.flip_req_i.value = 0

    saw_bridge_open = False
    i_at_open = None
    pol_before = int(dut.bridge_polarity_o.value)
    for _ in range(2500):
        await world.tick()
        if not saw_bridge_open and int(dut.bridge_enable_o.value) == 0:
            saw_bridge_open = True
            i_at_open = abs(world.plant.I)
        if saw_bridge_open and sts(dut)["fsm_state_name"] == "RUN":
            break
    else:
        raise AssertionError(f"flip never completed; state {sts(dut)['fsm_state_name']}")

    assert i_at_open is not None
    zero_win_amps = FSM_CFG["zero_win"] * MOT.amps_per_lsb
    assert i_at_open < 2 * zero_win_amps, \
        f"bridge opened at {i_at_open:.2f} A (window {zero_win_amps:.2f} A)"
    assert int(dut.bridge_polarity_o.value) == 1 - pol_before

    # loop must re-settle at the new polarity (drive-frame current -> 30 A),
    # again with boost from the DUT's own auto-boost
    for _ in range(1000):
        await world.tick()
    assert abs(world.plant.I - 30.0) < 0.1, \
        f"did not re-settle after flip (I = {world.plant.I:.2f} A)"
    s = sts(dut)
    assert s["fsm_state_name"] == "RUN"
    assert s["sp_sign_mismatch"] == 0, "bench sp sign tracking is wrong"


@cocotb.test()
async def dio_invert_flips_arm_sense(dut):
    await setup(dut, run_cfg(dio_invert=0b010))   # invert the arm input
    dut.arm_i.value = 0                           # physically low = armed now
    await ClockCycles(dut.aclk, 10)
    assert sts(dut)["fsm_state_name"] == "RUN"
    assert sts(dut)["armed"] == 1
