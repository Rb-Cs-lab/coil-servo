"""cocotb bench for servo_decimator: bit-exact against the Python Decimator."""

import random

import cocotb
from cocotb.triggers import RisingEdge

from coil_servo_model import Decimator
from tb_util import reset, signed, start_clock


@cocotb.test()
async def decimator_matches_python_model(dut):
    cocotb.start_soon(start_clock(dut))
    dut.in_valid.value = 0
    dut.e_fast.value = 0
    await reset(dut)

    assert signed(dut.e_out) == 0, "reset state: e_out must be 0"

    golden = Decimator()
    rng = random.Random(1234)
    outputs_seen = 0

    # 20 full decimation frames of adversarial samples: rails, zeros, random.
    n_frames = 20
    for k in range(n_frames * 128):
        choice = rng.random()
        if choice < 0.1:
            e = 16383
        elif choice < 0.2:
            e = -16384
        else:
            e = rng.randint(-16384, 16383)

        dut.e_fast.value = e
        dut.in_valid.value = 1
        expected = golden.push(e)
        await RisingEdge(dut.aclk)
        dut.in_valid.value = 0
        await RisingEdge(dut.aclk)   # out_valid/e_out registered

        if expected is not None:
            assert dut.out_valid.value == 1, f"sample {k}: out_valid missing"
            got = signed(dut.e_out)
            assert got == expected, f"sample {k}: HDL {got} != model {expected}"
            outputs_seen += 1
        else:
            assert dut.out_valid.value == 0, f"sample {k}: spurious out_valid"

    assert outputs_seen == n_frames


@cocotb.test()
async def decimator_gates_on_in_valid(dut):
    """Samples only accumulate when in_valid is high."""
    cocotb.start_soon(start_clock(dut))
    dut.in_valid.value = 0
    dut.e_fast.value = 1000
    await reset(dut)

    # 300 idle cycles with a nonzero input: nothing may come out.
    for _ in range(300):
        await RisingEdge(dut.aclk)
        assert dut.out_valid.value == 0

    # Now 128 valid samples of 1 -> exactly one output of 128.
    dut.e_fast.value = 1
    for _ in range(128):
        dut.in_valid.value = 1
        await RisingEdge(dut.aclk)
    dut.in_valid.value = 0
    await RisingEdge(dut.aclk)
    assert dut.out_valid.value == 1
    assert signed(dut.e_out) == 128
