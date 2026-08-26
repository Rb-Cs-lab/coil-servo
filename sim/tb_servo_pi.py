"""cocotb bench for servo_pi.

The contract: servo_pi must be bit-exact against
model/coil_servo_model/fixed_point.py::FixedPI -- output, saturation flag,
and integrator state, every tick. On top of that, directed tests assert the
BOOTSTRAP requirements: reset state off, no overflow at rail-to-rail input,
anti-windup at the clamp, and the settling spec in a closed loop against the
same plant model the float reference uses.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge

from coil_servo_model import CHANNELS, FixedPI, adc_quantize, encode_gain, output_mux_fixed
from coil_servo_model.analysis import settling_time, suggest_gains
from coil_servo_model.loop import T_S
from coil_servo_model.plant import Plant
from tb_util import reset, signed, start_clock

MOT = CHANNELS["mot"]
CLAMP = MOT.clamp_counts        # 6554 = 100% of rated


def apply_gains(dut, kp_m, kp_s, ki_m, ki_s, clamp=CLAMP):
    dut.kp_mant.value = kp_m
    dut.kp_shift.value = kp_s
    dut.ki_mant.value = ki_m
    dut.ki_shift.value = ki_s
    dut.clamp.value = clamp


async def tick(dut, e, hold=False, clear=False):
    """Run one PI tick and return u14 after the pipeline completes."""
    dut.int_hold.value = 1 if hold else 0
    dut.int_clear.value = 1 if clear else 0
    dut.e.value = e
    dut.tick.value = 1
    await RisingEdge(dut.aclk)
    dut.tick.value = 0
    for _ in range(6):          # MUL, SHIFT, SUM, OUT + settle margin
        await RisingEdge(dut.aclk)
    return signed(dut.u14)


def suggested():
    kp, ki_tick = suggest_gains(MOT)
    kp_m, kp_s = encode_gain(kp, shift_bits=5)
    ki_m, ki_s = encode_gain(ki_tick, shift_bits=6)
    return kp_m, kp_s, ki_m, ki_s


@cocotb.test()
async def reset_state_is_off(dut):
    """Safety invariant 2: output exactly zero out of reset."""
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    dut.int_hold.value = 0
    dut.int_clear.value = 0
    apply_gains(dut, *suggested())
    await reset(dut)
    assert signed(dut.u14) == 0
    assert signed(dut.acc_mon) == 0
    assert dut.out_sat.value == 0


@cocotb.test()
async def bit_exact_against_model(dut):
    """3 gain sets x 800 random ticks with random hold/clear events:
    u14, out_sat, and the accumulator must match FixedPI exactly."""
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    rng = random.Random(42)

    gain_sets = [
        suggested(),
        (2**17 - 1, 0, 2**17 - 1, 0),        # most violent representable gains
        (-1234, 3, 517, 20),                 # negative P, odd shifts
    ]
    for kp_m, kp_s, ki_m, ki_s in gain_sets:
        apply_gains(dut, kp_m, kp_s, ki_m, ki_s)
        dut.int_hold.value = 0
        dut.int_clear.value = 0
        await reset(dut)
        golden = FixedPI(kp_m, kp_s, ki_m, ki_s, CLAMP)

        for n in range(800):
            r = rng.random()
            if r < 0.05:
                e = 2**21 - 1
            elif r < 0.10:
                e = -(2**21)
            elif r < 0.15:
                e = 0
            else:
                e = rng.randint(-(2**21), 2**21 - 1)
            hold = rng.random() < 0.05
            clear = rng.random() < 0.02

            # HDL semantics: while int_clear is asserted the accumulator is
            # pinned at zero (clear wins over accumulate). Mirror that as
            # clear() + a held step.
            if clear:
                golden.clear()
            exp = golden.step(e, hold=hold or clear)
            got = await tick(dut, e, hold=hold, clear=clear)

            assert got == exp, f"tick {n}: u14 HDL {got} != model {exp}"
            assert -CLAMP <= got <= CLAMP, f"tick {n}: clamp violated"
            assert signed(dut.acc_mon) == golden.acc, \
                f"tick {n}: acc HDL {signed(dut.acc_mon)} != model {golden.acc}"
            assert int(dut.out_sat.value) == int(exp != (golden.u24 + 64) >> 7), \
                f"tick {n}: out_sat mismatch"


@cocotb.test()
async def anti_windup_stops_integration_at_clamp(dut):
    """Sustained large error: output rides the clamp, accumulator must
    plateau (mechanism 1), and must unwind promptly when the error flips."""
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    apply_gains(dut, *suggested())
    dut.int_hold.value = 0
    dut.int_clear.value = 0
    await reset(dut)

    e_big = 2**20            # half full scale, drives u well past the clamp
    for _ in range(200):
        u = await tick(dut, e_big)
    assert u == CLAMP, "output must sit exactly at the clamp"
    acc_at_clamp = signed(dut.acc_mon)
    for _ in range(50):
        await tick(dut, e_big)
    assert signed(dut.acc_mon) == acc_at_clamp, "integrator kept winding up"

    # error reverses: the very next ticks must integrate downward again
    await tick(dut, -e_big)
    assert signed(dut.acc_mon) < acc_at_clamp, "integrator failed to unwind"


@cocotb.test()
async def hold_and_clear(dut):
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    apply_gains(dut, *suggested())
    dut.int_hold.value = 0
    dut.int_clear.value = 0
    await reset(dut)

    for _ in range(20):
        await tick(dut, 100000)
    acc0 = signed(dut.acc_mon)
    assert acc0 != 0
    for _ in range(20):
        await tick(dut, 100000, hold=True)
    assert signed(dut.acc_mon) == acc0, "hold must freeze the accumulator"
    await tick(dut, 100000, clear=True)
    assert signed(dut.acc_mon) == 0, "clear must zero the accumulator"


@cocotb.test()
async def closed_loop_step_meets_settling_spec(dut):
    """MOT channel, 0 -> 100 A step with boost for the ramp: the HDL-in-the-
    loop response must match the FixedPI reference exactly AND settle to
    0.1% in under 1 ms (BOOTSTRAP loop target)."""
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    gains = suggested()
    apply_gains(dut, *gains)
    dut.int_hold.value = 0
    dut.int_clear.value = 0
    await reset(dut)

    golden = FixedPI(*gains, CLAMP)
    plant_hdl = Plant(MOT)
    plant_ref = Plant(MOT)
    deadband = 8
    target = 100.0
    n_ticks = 1500           # 1.536 ms of loop time
    t_axis, i_trace = [], []

    for k in range(n_ticks):
        t = k * T_S
        boost = t < 0.5e-3

        # identical quantization path for both loops
        sp_code = adc_quantize(target / MOT.I_FS)

        e_hdl = (sp_code - adc_quantize(plant_hdl.I / MOT.I_FS)) << 7
        u_hdl = await tick(dut, e_hdl)
        o1, o2 = output_mux_fixed(u_hdl, deadband)
        plant_hdl.step(o1 / 8192.0, o2 / 8192.0, boost, T_S)

        e_ref = (sp_code - adc_quantize(plant_ref.I / MOT.I_FS)) << 7
        u_ref = golden.step(e_ref)
        r1, r2 = output_mux_fixed(u_ref, deadband)
        plant_ref.step(r1 / 8192.0, r2 / 8192.0, boost, T_S)

        assert u_hdl == u_ref, f"tick {k}: closed-loop divergence {u_hdl} != {u_ref}"

        t_axis.append(t)
        i_trace.append(plant_hdl.I)

    ts = settling_time(t_axis, i_trace, target)
    assert ts is not None and ts < 1e-3, f"settling time {ts} s violates spec"
