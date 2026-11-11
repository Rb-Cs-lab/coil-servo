"""cocotb bench for servo_pi.

The contract: servo_pi must be bit-exact against
model/coil_servo_model/fixed_point.py::FixedPI -- output, saturation flag,
and integrator state, every tick. On top of that, directed tests assert the
BOOTSTRAP requirements: reset state off, no overflow at rail-to-rail input,
anti-windup at the clamp AND at the lower bound (MVP: PI floored at 0 or at
the RUN-only dead-zone floor), and the settling spec in a closed loop
against the MVP plant the float reference uses.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge

from coil_servo_model import (CHANNELS, FixedPI, Plant, adc_quantize,
                              encode_gain, output_mux_fixed)
from coil_servo_model.analysis import overshoot_frac, settling_time, suggest_gains
from coil_servo_model.fixed_point import SigmaDelta
from coil_servo_model.loop import T_S
from tb_util import reset, signed, start_clock

MOT = CHANNELS["mot"]
CLAMP = MOT.clamp_counts                        # OUT1 command clamp
FLOOR = round(0.9 * MOT.dead_zone * 8192)       # RUN-only dead-zone floor


def apply_gains(dut, kp_m, kp_s, ki_m, ki_s, clamp=CLAMP, floor=0):
    dut.kp_mant.value = kp_m
    dut.kp_shift.value = kp_s
    dut.ki_mant.value = ki_m
    dut.ki_shift.value = ki_s
    dut.clamp.value = clamp
    dut.floor.value = floor


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


def dithered_out1(sd, u14, resid, deadband):
    """Mean OUT1 over one tick with the sigma-delta (servo_dither.v) on, as
    the gate sees it -- mirrors run_loop(dither=True)."""
    tot = 0
    for _ in range(128):
        ud = sd.step(u14, resid, FLOOR, CLAMP)
        tot += output_mux_fixed(ud, deadband, out2_enable=False)[0]
    return tot / 128


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
    assert dut.at_floor.value == 0


@cocotb.test()
async def bit_exact_against_model(dut):
    """4 gain/floor sets x 800 random ticks with random hold/clear events
    and the floor switching as the FSM enters/leaves RUN: u14, out_sat,
    at_floor and the accumulator must match FixedPI exactly."""
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    rng = random.Random(42)

    # (gains, floor values the sequence switches between)
    gain_sets = [
        (suggested(), (0, FLOOR)),             # MVP: zero floor / RUN floor
        ((2**17 - 1, 0, 2**17 - 1, 0), (-CLAMP, 0, FLOOR)),  # violent gains
        ((-1234, 3, 517, 20), (-CLAMP, 0)),    # negative P, odd shifts
        ((5000, 10, 3, 40), (FLOOR, 8000)),    # shift overflow; floor > clamp
    ]
    for (kp_m, kp_s, ki_m, ki_s), floors in gain_sets:
        apply_gains(dut, kp_m, kp_s, ki_m, ki_s, floor=floors[0])
        dut.int_hold.value = 0
        dut.int_clear.value = 0
        await reset(dut)
        golden = FixedPI(kp_m, kp_s, ki_m, ki_s, CLAMP, floors[0])

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
            if rng.random() < 0.03:
                golden.floor = rng.choice(floors)
                dut.floor.value = golden.floor

            # HDL semantics: while int_clear is asserted the accumulator is
            # pinned at zero (clear wins over accumulate). Mirror that as
            # clear() + a held step.
            if clear:
                golden.clear()
            exp = golden.step(e, hold=hold or clear)
            got = await tick(dut, e, hold=hold, clear=clear)

            assert got == exp, f"tick {n}: u14 HDL {got} != model {exp}"
            assert -CLAMP <= got <= CLAMP, f"tick {n}: clamp violated"
            assert got >= min(golden.floor, CLAMP), f"tick {n}: below floor"
            assert signed(dut.acc_mon) == golden.acc, \
                f"tick {n}: acc HDL {signed(dut.acc_mon)} != model {golden.acc}"
            assert int(dut.out_sat.value) == int(golden.out_sat), \
                f"tick {n}: out_sat mismatch"
            assert int(dut.at_floor.value) == int(golden.at_floor), \
                f"tick {n}: at_floor mismatch"
            assert signed(dut.resid) == golden.resid, \
                f"tick {n}: resid HDL {signed(dut.resid)} != model {golden.resid}"


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
async def anti_windup_at_the_floor(dut):
    """MVP lower bound: a sustained negative error holds the output on the
    floor, the integrator on floor << (7 + ki_shift) -- never below -- and
    a positive error moves it off the floor on the very next tick."""
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    gains = suggested()
    apply_gains(dut, *gains, floor=FLOOR)
    dut.int_hold.value = 0
    dut.int_clear.value = 0
    await reset(dut)

    for _ in range(300):
        u = await tick(dut, -(2**20))
        assert u == FLOOR
    assert dut.at_floor.value == 1
    acc_floor = FLOOR << (7 + gains[3])
    assert signed(dut.acc_mon) == acc_floor
    await tick(dut, 2**16)
    assert signed(dut.acc_mon) > acc_floor and dut.at_floor.value == 0

    # floor 0 (outside RUN): the integrator unwinds but never goes negative
    dut.floor.value = 0
    for _ in range(3000):
        u = await tick(dut, -(2**20))
        assert u >= 0
        assert signed(dut.acc_mon) >= 0
    assert u == 0


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
    """MOT channel, MVP plant, steps from zero with the RUN floor: the HDL-
    in-the-loop response must match the FixedPI reference exactly, never
    drive OUT2, and settle to 0.1 % in under 1 ms (BOOTSTRAP loop target).
    50 A: plain step. 100 A: compliance-limited, rides the clamp.
    OUT1 goes through the sigma-delta (as on the board): with the measured
    OUT1 scale one count is ~0.14 A, more than the 0.1 % band at 50 A."""
    cocotb.start_soon(start_clock(dut))
    dut.tick.value = 0
    gains = suggested()
    deadband = 8

    for target in (50.0, 100.0):
        apply_gains(dut, *gains, floor=FLOOR)
        dut.int_hold.value = 0
        dut.int_clear.value = 0
        await reset(dut)

        golden = FixedPI(*gains, CLAMP, FLOOR)
        plant_hdl = Plant(MOT)
        plant_ref = Plant(MOT)
        sd_hdl, sd_ref = SigmaDelta(), SigmaDelta()
        n_ticks = 1300           # 1.33 ms of loop time
        t_axis, i_trace = [], []
        sp_code = adc_quantize(target / MOT.I_FS)

        for k in range(n_ticks):
            e_hdl = (sp_code - adc_quantize(plant_hdl.I / MOT.I_FS)) << 7
            u_hdl = await tick(dut, e_hdl)
            o1, o2 = output_mux_fixed(u_hdl, deadband, out2_enable=False)
            assert o2 == 0
            o1 = dithered_out1(sd_hdl, u_hdl, signed(dut.resid), deadband)
            plant_hdl.step(o1 / 8192.0, 0.0, False, T_S)

            e_ref = (sp_code - adc_quantize(plant_ref.I / MOT.I_FS)) << 7
            u_ref = golden.step(e_ref)
            r1 = dithered_out1(sd_ref, u_ref, golden.resid, deadband)
            plant_ref.step(r1 / 8192.0, 0.0, False, T_S)

            assert u_hdl == u_ref, \
                f"{target} A tick {k}: closed-loop divergence {u_hdl} != {u_ref}"
            assert FLOOR <= u_hdl <= CLAMP

            t_axis.append(k * T_S)
            i_trace.append(plant_hdl.I)

        ts = settling_time(t_axis, i_trace, target)
        assert ts is not None and ts < 1e-3, \
            f"{target} A: settling time {ts} s violates spec"
        assert overshoot_frac(i_trace, target) < 0.01
