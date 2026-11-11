"""cocotb bench for servo_dither: bit-exact vs fixed_point.SigmaDelta every
clock, plus directed checks: the 128-clock mean carries the residual, the
output never leaves [lo, clamp], and enable = 0 is an exact pass-through."""

import random

import cocotb
from cocotb.triggers import FallingEdge

from coil_servo_model import SigmaDelta
from tb_util import reset, signed, start_clock

CLAMP = 4474
FLOOR = 3234


def drive(dut, u, resid, lo=FLOOR, clamp=CLAMP, enable=1):
    dut.u14.value = u
    dut.resid.value = resid
    dut.lo.value = lo
    dut.clamp.value = clamp
    dut.enable.value = enable


async def setup(dut):
    cocotb.start_soon(start_clock(dut))
    drive(dut, 0, 0, enable=0)
    await reset(dut)
    await FallingEdge(dut.aclk)


async def sample(dut):
    """Advance one clock and read the registered output, which was computed
    at that rising edge from the inputs just driven and the previous
    sigma-delta state -- what SigmaDelta.step() returns."""
    await FallingEdge(dut.aclk)
    return signed(dut.u_out)


@cocotb.test()
async def bit_exact_against_model(dut):
    await setup(dut)
    rng = random.Random(11)
    golden = SigmaDelta()
    a = dict(u=4000, resid=0, lo=FLOOR, clamp=CLAMP, enable=1)
    for n in range(20000):
        if rng.random() < 0.01:          # a new PI tick
            a["u"] = rng.choice([CLAMP, FLOOR, -CLAMP, rng.randint(-8192, 8191)])
            a["resid"] = rng.randint(-64, 63)
        if rng.random() < 0.002:
            a["lo"] = rng.choice([FLOOR, 0, -CLAMP, 8000])
            a["clamp"] = rng.choice([CLAMP, 8191, 0])
        if rng.random() < 0.002:
            a["enable"] ^= 1
        drive(dut, a["u"], a["resid"], a["lo"], a["clamp"], a["enable"])
        got = await sample(dut)
        exp = golden.step(a["u"], a["resid"], a["lo"], a["clamp"],
                          bool(a["enable"]))
        assert got == exp, f"clock {n}: HDL {got} != model {exp} ({a})"
        if a["enable"]:
            assert got <= a["clamp"], "dither exceeded the clamp"


@cocotb.test()
async def mean_over_a_tick_carries_the_residual(dut):
    await setup(dut)
    for resid in (-64, -37, -1, 0, 1, 17, 63):
        drive(dut, 4000, resid)
        vals = [await sample(dut) for _ in range(128 * 4)]
        assert set(vals) <= {3999, 4000, 4001}
        assert not ({3999, 4001} <= set(vals)), "dithers both ways"
        mean = sum(vals[128:]) / len(vals[128:])
        assert abs(mean - (4000 + resid / 128)) <= 1 / 128 + 1e-9, \
            f"resid {resid}: mean {mean}"


@cocotb.test()
async def never_outside_floor_and_clamp(dut):
    await setup(dut)
    for u, resid in ((CLAMP, 63), (FLOOR, -64)):
        drive(dut, u, resid)
        for _ in range(512):
            assert FLOOR <= await sample(dut) <= CLAMP


@cocotb.test()
async def disabled_is_exact_passthrough(dut):
    await setup(dut)
    drive(dut, 4000, 63, enable=0)
    for _ in range(256):
        assert await sample(dut) == 4000
