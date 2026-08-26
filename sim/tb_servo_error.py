"""cocotb bench for servo_error: bit-exact vs drive_frame_error across the
setpoint mux, polarity rotation, and sign-mismatch flag."""

import random

import cocotb
from cocotb.triggers import RisingEdge

from coil_servo_model import drive_frame_error
from tb_util import reset, signed, start_clock


@cocotb.test()
async def matches_model(dut):
    cocotb.start_soon(start_clock(dut))
    await reset(dut)
    rng = random.Random(99)

    cases = [
        # rails and near-threshold setpoints in every mux configuration
        (8191, -8192, 0, 0, 0, 0), (-8192, 8191, 0, 0, 0, 1),
        (0, -65, 0, 0, 0, 0), (0, -64, 0, 0, 0, 0),
        (0, 65, 0, 0, 0, 1), (0, 64, 0, 0, 0, 1),
        (100, 5000, -3000, 1, 0, 0), (100, 5000, -3000, 1, 0, 1),
        (100, 5000, -3000, 0, 1, 0), (100, 5000, -3000, 1, 1, 1),
    ]
    cases += [(rng.randint(-8192, 8191), rng.randint(-8192, 8191),
               rng.randint(-8192, 8191), rng.randint(0, 1),
               rng.randint(0, 1), rng.randint(0, 1)) for _ in range(500)]

    for meas, in2, sp_reg, src, force, pol in cases:
        dut.meas.value = meas
        dut.in2.value = in2
        dut.sp_reg.value = sp_reg
        dut.sp_source.value = src
        dut.sp_force_zero.value = force
        dut.polarity.value = pol
        await RisingEdge(dut.aclk)
        await RisingEdge(dut.aclk)

        e_exp, sp_exp, mm_exp = drive_frame_error(meas, in2, sp_reg,
                                                  src, force, pol)
        got = (signed(dut.e_fast), signed(dut.sp_active),
               int(dut.sp_sign_mismatch.value))
        assert got == (e_exp, sp_exp, mm_exp), \
            f"{(meas, in2, sp_reg, src, force, pol)}: {got} != {(e_exp, sp_exp, mm_exp)}"
