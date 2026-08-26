"""cocotb bench for servo_output_mux: bit-exact vs output_mux_fixed, plus
the enable gate and OUT2 inversion."""

import random

import cocotb
from cocotb.triggers import RisingEdge

from coil_servo_model import output_mux_fixed
from tb_util import reset, signed, start_clock


async def apply(dut, u, deadband, enable=1, invert=0):
    dut.u14.value = u
    dut.deadband.value = deadband
    dut.enable.value = enable
    dut.out2_invert.value = invert
    await RisingEdge(dut.aclk)
    await RisingEdge(dut.aclk)
    return signed(dut.out1), signed(dut.out2)


@cocotb.test()
async def matches_model_and_never_both_active(dut):
    cocotb.start_soon(start_clock(dut))
    await reset(dut)
    rng = random.Random(7)

    cases = [(-8192, 0), (-8192, 8), (8191, 8), (0, 8), (8, 8), (9, 8),
             (-8, 8), (-9, 8), (6554, 0), (-6554, 16383)]
    cases += [(rng.randint(-8192, 8191), rng.choice([0, 1, 8, 100, 8191]))
              for _ in range(400)]

    for u, db in cases:
        o1, o2 = await apply(dut, u, db)
        e1, e2 = output_mux_fixed(u, db)
        assert (o1, o2) == (e1, e2), f"u={u} db={db}: ({o1},{o2}) != ({e1},{e2})"
        assert o1 == 0 or o2 == 0, "mutual exclusion violated"
        assert o1 >= 0 and o2 >= 0


@cocotb.test()
async def enable_gate_forces_zero(dut):
    cocotb.start_soon(start_clock(dut))
    await reset(dut)
    o1, o2 = await apply(dut, 5000, 8, enable=0)
    assert (o1, o2) == (0, 0), "disabled outputs must be exactly zero"
    o1, o2 = await apply(dut, -5000, 8, enable=0)
    assert (o1, o2) == (0, 0)


@cocotb.test()
async def out2_invert_flips_clamp_polarity(dut):
    cocotb.start_soon(start_clock(dut))
    await reset(dut)
    _, o2 = await apply(dut, -5000, 8, invert=0)
    assert o2 == 5000
    _, o2i = await apply(dut, -5000, 8, invert=1)
    assert o2i == -5000
    o1, _ = await apply(dut, 5000, 8, invert=1)
    assert o1 == 5000, "invert must not affect OUT1"
