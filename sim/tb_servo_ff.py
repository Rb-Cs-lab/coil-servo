"""cocotb bench for servo_ff (setpoint feedforward, register map rev 7).

The contract: bit-exact against model/coil_servo_model/fixed_point.py::
ff_interp for random tables, breakpoint spacings and setpoints (including
negative setpoints, the top of the table and the s14 extremes), a fixed
4-clock latency, and exactly 0 while disabled or in reset.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge

from coil_servo_model import ff_interp
from tb_util import reset, start_clock

LATENCY = 4


def pack(table):
    v = 0
    for k, t in enumerate(table):
        v |= (t & 0x3FFF) << (14 * k)
    return v


async def settle(dut):
    # values read right after an edge are those from before it: one more
    for _ in range(LATENCY + 1):
        await RisingEdge(dut.aclk)
    return int(dut.ff.value)


@cocotb.test()
async def reset_and_disable_give_zero(dut):
    cocotb.start_soon(start_clock(dut))
    dut.enable.value = 1
    dut.sp.value = 1000
    dut.shift.value = 6
    dut.table_flat.value = pack([4000 + 10 * k for k in range(16)])
    await reset(dut)
    assert int(dut.ff.value) == 0
    assert await settle(dut) == ff_interp(1000, [4000 + 10 * k for k in range(16)], 6)
    dut.enable.value = 0
    assert await settle(dut) == 0


@cocotb.test()
async def bit_exact_against_model(dut):
    """3000 random (table, shift, setpoint) cases, each checked after the
    pipeline latency; a monotone 'real' table and fully random ones."""
    cocotb.start_soon(start_clock(dut))
    dut.enable.value = 1
    dut.sp.value = 0
    dut.shift.value = 0
    dut.table_flat.value = 0
    await reset(dut)
    rng = random.Random(7)
    for n in range(3000):
        if n % 3 == 0:      # bench-like: dead zone then a square root
            t0 = rng.randint(3000, 4000)
            table = [min(t0 + int(rng.uniform(20, 200) * k ** 0.5), 16383)
                     for k in range(16)]
        else:
            table = [rng.randint(0, 16383) for _ in range(16)]
        shift = rng.randint(0, 15)
        q = rng.random()
        sp = (rng.choice([-8192, 8191, 0, -1]) if q < 0.1 else
              rng.randint(-50, 15 << shift) if q < 0.6 else
              rng.randint(-8192, 8191))
        sp = max(-8192, min(8191, sp))
        dut.table_flat.value = pack(table)
        dut.shift.value = shift
        dut.sp.value = sp
        got = await settle(dut)
        exp = ff_interp(sp, table, shift)
        assert got == exp, f"case {n}: sp {sp} shift {shift}: HDL {got} != model {exp}"


@cocotb.test()
async def latency_is_four_clocks(dut):
    cocotb.start_soon(start_clock(dut))
    table = [100 * k for k in range(16)]
    dut.enable.value = 1
    dut.shift.value = 4
    dut.table_flat.value = pack(table)
    dut.sp.value = 0
    await reset(dut)
    for _ in range(6):
        await RisingEdge(dut.aclk)
    dut.sp.value = 40          # -> 250
    seen = []
    for _ in range(6):
        await RisingEdge(dut.aclk)
        seen.append(int(dut.ff.value))
    assert seen[LATENCY] == 250 and seen[LATENCY - 1] == 0, seen
