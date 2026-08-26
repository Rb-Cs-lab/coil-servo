"""Plant model physics checks."""

import math

import pytest

from coil_servo_model import CHANNELS, Plant, T_S

MOT = CHANNELS["mot"]


def run_plant(plant, v1, v2, boost, t):
    n = int(round(t / T_S))
    for _ in range(n):
        plant.step(v1, v2, boost, T_S)
    return plant.I


def test_passive_decay_time_constant():
    # Both outputs off: current decays with tau = L / R_loop.
    tau = MOT.L / MOT.R_loop
    plant = Plant(MOT, i0=50.0)
    I = run_plant(plant, 0.0, 0.0, False, tau)
    assert I == pytest.approx(50.0 / math.e, rel=0.02)


def test_upward_slew_is_voltage_limited():
    # Commanding far more current than the rail can slew: dI/dt must sit at
    # the compliance limit (V_rail - I*R - V_ds_min)/L, not at the inner
    # loop's tracking rate.
    plant = Plant(MOT, i0=0.0)
    dt = 2e-6
    I = run_plant(plant, 0.8, 0.0, False, dt)   # 0.8 V -> 100 A demanded
    limit = (MOT.V_rail - MOT.V_ds_min) / MOT.L
    assert I / dt == pytest.approx(limit, rel=0.05)


def test_boost_raises_slew_limit():
    dt = 2e-6
    i_hold = run_plant(Plant(MOT), 0.8, 0.0, False, dt)
    i_boost = run_plant(Plant(MOT), 0.8, 0.0, True, dt)
    ratio = (MOT.V_boost - MOT.V_ds_min) / (MOT.V_rail - MOT.V_ds_min)
    assert i_boost / i_hold == pytest.approx(ratio, rel=0.05)


def test_clamp_pulls_down_much_faster_than_passive():
    t = 50e-6
    passive = 50.0 - run_plant(Plant(MOT, i0=50.0), 0.0, 0.0, False, t)
    clamped = 50.0 - run_plant(Plant(MOT, i0=50.0), 0.0, 0.5, False, t)
    assert clamped > 20 * passive


def test_pass_bank_cannot_pull_down_faster_than_passive():
    # Throttling the pass bank back to a lower command: the stage can only
    # source, so the fall rate must not beat the passive decay.
    t = 100e-6
    plant = Plant(MOT, i0=50.0)
    I_throttled = run_plant(plant, 0.01, 0.0, False, t)   # command ~1 A
    I_passive = 50.0 * math.exp(-t / (MOT.L / MOT.R_loop))
    assert I_throttled >= I_passive * 0.98


def test_simultaneous_drive_is_rejected():
    with pytest.raises(AssertionError):
        Plant(MOT).step(0.5, 0.5, False, T_S)
