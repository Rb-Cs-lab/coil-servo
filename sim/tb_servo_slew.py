"""cocotb bench for servo_slew: bit-exact vs fixed_point.SlewLimiter every
clock (random targets, rates, hold_zero and bypass), plus directed checks
of the physical ramp rates, the frozen-when-unconfigured default, the
zero crossing, and the snap to a target inside the zero window."""

import random

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge

from coil_servo_model import SlewLimiter, slew_rate_code
from tb_util import reset, signed, start_clock

I_FS = 200.0
UP = slew_rate_code(200e3, I_FS)      # 200 A/ms
DOWN = slew_rate_code(50e3, I_FS)     # 50 A/ms
CLK = 8e-9


def counts(amps):
    return round(amps / I_FS * 8192)


def drive(dut, target, up=UP, down=DOWN, hold=0, bypass=0, snap=0):
    dut.target.value = target
    dut.snap_win.value = snap
    dut.rate_up.value = up
    dut.rate_down.value = down
    dut.hold_zero.value = hold
    dut.bypass.value = bypass


async def setup(dut):
    cocotb.start_soon(start_clock(dut))
    drive(dut, 0)
    await reset(dut)


async def clocks_to_reach(dut, target, limit):
    for n in range(1, limit + 1):
        await RisingEdge(dut.aclk)
        if signed(dut.sp_out) == target:
            return n
    raise AssertionError(f"did not reach {target}; at {signed(dut.sp_out)}")


@cocotb.test()
async def bit_exact_against_model(dut):
    """20k clocks of random targets, rates (including 0 and the maximum),
    hold_zero and bypass: the HDL output equals SlewLimiter every clock.
    Inputs change on the falling edge; the output is sampled there too,
    after the rising edge has updated the register."""
    await setup(dut)
    golden = SlewLimiter()
    # reset() ends with one live clock edge (aresetn high, inputs as
    # set by setup): advance the model by that edge so the phases align
    golden.step(0, UP, DOWN)
    await FallingEdge(dut.aclk)
    rng = random.Random(5)
    a = dict(target=0, up=UP, down=DOWN, hold=0, bypass=0, snap=0)
    for n in range(20000):
        r = rng.random()
        if r < 0.002:
            a["target"] = rng.choice([8191, -8192, 0])
        elif r < 0.01:
            a["target"] = rng.randint(-8192, 8191)
        elif r < 0.012:
            a["target"] = rng.randint(-40, 40)        # near the snap window
        if rng.random() < 0.002:
            a["snap"] = rng.choice([0, 20, 21, 1000, 16383])
        if rng.random() < 0.002:
            a["up"] = rng.choice([0, 1, UP, 65535, rng.randint(0, 65535)])
            a["down"] = rng.choice([0, 1, DOWN, 65535, rng.randint(0, 65535)])
        if rng.random() < 0.003:
            a["hold"] ^= 1
        if rng.random() < 0.002:
            a["bypass"] ^= 1
        drive(dut, a["target"], a["up"], a["down"], a["hold"], a["bypass"],
              a["snap"])
        await FallingEdge(dut.aclk)
        exp = golden.step(a["target"], a["up"], a["down"],
                          bool(a["hold"]), bool(a["bypass"]), a["snap"])
        got = signed(dut.sp_out)
        assert got == exp, f"clock {n}: HDL {got} != model {exp} ({a})"


@cocotb.test()
async def ramp_rates_are_physical(dut):
    """0 -> 70 A takes 0.35 ms at 200 A/ms; 70 -> 20 A takes 1.0 ms at
    50 A/ms (within one count of quantization)."""
    await setup(dut)
    drive(dut, counts(70.0))
    n_up = await clocks_to_reach(dut, counts(70.0), 200_000)
    assert abs(n_up * CLK - 0.35e-3) < 0.01e-3, f"up ramp {n_up * CLK * 1e3:.3f} ms"
    drive(dut, counts(20.0))
    n_dn = await clocks_to_reach(dut, counts(20.0), 300_000)
    assert abs(n_dn * CLK - 1.0e-3) < 0.01e-3, f"down ramp {n_dn * CLK * 1e3:.3f} ms"


@cocotb.test()
async def zero_rate_freezes_and_hold_zero_resets(dut):
    await setup(dut)
    drive(dut, counts(50.0), up=0, down=0)
    for _ in range(1000):
        await RisingEdge(dut.aclk)
    assert signed(dut.sp_out) == 0, "unconfigured limiter must not move"

    drive(dut, counts(50.0))
    await clocks_to_reach(dut, counts(50.0), 200_000)
    drive(dut, counts(50.0), hold=1)          # FSM forced zero: at once
    await RisingEdge(dut.aclk)
    await RisingEdge(dut.aclk)
    assert signed(dut.sp_out) == 0
    drive(dut, counts(50.0))                  # released: ramps from zero
    for _ in range(100):
        await RisingEdge(dut.aclk)
    assert 0 < signed(dut.sp_out) < counts(50.0)


@cocotb.test()
async def bypass_passes_through(dut):
    await setup(dut)
    drive(dut, -5000, bypass=1)
    for _ in range(3):                  # input register + acc register
        await RisingEdge(dut.aclk)
    assert signed(dut.sp_out) == -5000


@cocotb.test()
async def crossing_zero_uses_down_then_up(dut):
    """+20 A -> -20 A: 0.4 ms down to zero at 50 A/ms, then 0.1 ms up at
    200 A/ms."""
    await setup(dut)
    drive(dut, counts(20.0))
    await clocks_to_reach(dut, counts(20.0), 200_000)
    drive(dut, counts(-20.0))
    n0 = await clocks_to_reach(dut, 0, 200_000)
    n1 = await clocks_to_reach(dut, counts(-20.0), 200_000)
    assert abs(n0 * CLK - 0.4e-3) < 0.01e-3
    assert abs(n1 * CLK - 0.1e-3) < 0.01e-3


@cocotb.test()
async def target_inside_the_zero_window_snaps(dut):
    """Snap-off: from 70 A a step to zero (inside the 0.5 A zero window) is
    taken in one clock; a target outside the window still ramps."""
    await setup(dut)
    win = counts(0.5)
    drive(dut, counts(70.0), snap=win)
    await clocks_to_reach(dut, counts(70.0), 200_000)
    drive(dut, 0, snap=win)
    n = await clocks_to_reach(dut, 0, 10)
    assert n <= 3, f"snap took {n} clocks"
    drive(dut, counts(70.0), snap=win)
    await clocks_to_reach(dut, counts(70.0), 200_000)
    drive(dut, counts(10.0), snap=win)          # outside the window: ramps
    n = await clocks_to_reach(dut, counts(10.0), 300_000)
    assert abs(n * CLK - 1.2e-3) < 0.01e-3
    drive(dut, counts(0.3), snap=win)           # small setpoint: kept, not 0
    await clocks_to_reach(dut, counts(0.3), 10)
