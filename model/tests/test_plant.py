"""MVP plant physics: dead zone, current-source behaviour, 5 V compliance,
freewheel turn-off, bridge-open fault path, per-FET sharing
(docs/mvp_architecture.md section 3)."""

import math
from dataclasses import replace

import pytest

from coil_servo_model import CHANNELS, Plant, T_S

MOT = CHANNELS["mot"]


def hold(plant, v1, t):
    """Run the plant for t seconds with OUT1 fixed at v1 (normalized)."""
    for _ in range(int(round(t / T_S))):
        plant.step(v1, 0.0, False, T_S)
    return plant.I


def time_to(plant, v1, cond, t_max):
    """Seconds until cond(I) holds, stepping one PI tick at a time."""
    t = 0.0
    while t < t_max:
        plant.step(v1, 0.0, False, T_S)
        t += T_S
        if cond(plant.I):
            return t
    raise AssertionError(f"condition not reached in {t_max} s (I = {plant.I})")


def test_dead_zone_passes_no_current():
    plant = Plant(MOT)
    assert hold(plant, 0.95 * MOT.dead_zone, 200e-6) == 0.0


@pytest.mark.parametrize("name", ["mot", "x_shim"])
def test_bank_is_a_current_source(name):
    # The steady current is what the gate asks for, whatever the coil's L.
    ch = CHANNELS[name]
    plant = Plant(ch)
    I = hold(plant, ch.out1_for_current(40.0), 1e-3)
    assert I == pytest.approx(40.0, rel=1e-3)


def test_up_step_is_compliance_limited_on_the_5v_rail():
    # Bank asks for rated current from zero: the rail is the only push,
    # L dI/dt = V_rail - I*R_eff with R_eff = R_series + bank fully-on drop.
    R_eff = MOT.R_series + (MOT.R_ds_on + MOT.R_ballast) / len(MOT.V_th)
    t_analytic = (MOT.L / R_eff) * math.log(MOT.V_rail / (MOT.V_rail - 99.0 * R_eff))
    t = time_to(Plant(MOT), MOT.out1_for_current(100.0), lambda I: I >= 99.0, 2e-3)
    assert t == pytest.approx(t_analytic, rel=0.05)
    assert 0.3e-3 < t < 0.6e-3          # ~0.4 ms, mvp_architecture.md 3.4


def test_freewheel_decay_matches_analytic():
    # Bank switched off at 100 A: L dI/dt = -(V_f + I*R_fw).
    tau, i_eq = MOT.L / MOT.R_fw, MOT.V_f / MOT.R_fw
    t_analytic = tau * math.log((100.0 + i_eq) / (0.5 + i_eq))
    t = time_to(Plant(MOT, i0=100.0), 0.0, lambda I: I <= 0.5, 5e-3)
    assert t == pytest.approx(t_analytic, rel=0.03)
    assert 1.0e-3 < t < 1.6e-3          # ~1.3 ms


def test_servoed_down_ramp_runs_at_the_freewheel_rate():
    # Asking for 50 A from 100 A: same decay curve as a full turn-off until
    # the current reaches 50 A, then the bank holds it there.
    tau, i_eq = MOT.L / MOT.R_fw, MOT.V_f / MOT.R_fw
    t_analytic = tau * math.log((100.0 + i_eq) / (50.5 + i_eq))
    plant = Plant(MOT, i0=100.0)
    v50 = MOT.out1_for_current(50.0)
    t = time_to(plant, v50, lambda I: I <= 50.5, 3e-3)
    assert t == pytest.approx(t_analytic, rel=0.05)
    assert hold(plant, v50, 0.5e-3) == pytest.approx(50.0, rel=1e-3)


def test_tvs_turnoff_flag():
    # The rewrite brief's alternative physics: bank voltage clamped by the TVS.
    ch = replace(MOT, turnoff_model="tvs")
    plant = Plant(ch, i0=100.0)
    dt = 2e-6
    drop = 100.0 - hold(plant, 0.0, dt)
    rate = (ch.V_tvs - ch.V_rail + 100.0 * ch.R_series) / ch.L
    assert drop / dt == pytest.approx(rate, rel=0.05)


def test_bridge_open_pushes_current_into_the_supply():
    # Heartbeat-latch trip at current: body diodes return the current to the
    # supply, L dI/dt = -(V_rail + 3 V_f + I*R_series), down to zero and no
    # further.
    plant = Plant(MOT, i0=100.0)
    plant.bridge_enabled = False
    dt = 2e-6
    drop = 100.0 - hold(plant, MOT.clamp_counts / 8192, dt)
    rate = (MOT.V_rail + 3 * MOT.V_f + 100.0 * MOT.R_series) / MOT.L
    assert drop / dt == pytest.approx(rate, rel=0.05)
    assert hold(plant, MOT.clamp_counts / 8192, 1e-3) == 0.0


def test_fet_sharing_at_rated_current_within_12_percent():
    # Installed V_th spread 0.06 V over 0.5 V of ballast drop -> <= 12 %.
    plant = Plant(MOT, i0=100.0)
    hold(plant, MOT.out1_for_current(100.0), 50e-6)
    fets = plant.fet_currents()
    mean = sum(fets) / len(fets)
    assert sum(fets) == pytest.approx(100.0, rel=1e-3)
    assert (max(fets) - min(fets)) / mean <= 0.12


def test_fet_sharing_is_worse_at_low_current():
    # The spread grows as 1/I (ballast drop shrinks); harmless because the
    # per-FET power is small there -- documented, not a limit.
    plant = Plant(MOT, i0=10.0)
    hold(plant, MOT.out1_for_current(10.0), 50e-6)
    fets = plant.fet_currents()
    assert (max(fets) - min(fets)) / (sum(fets) / len(fets)) > 0.12


def test_out2_and_boost_are_not_connected():
    with pytest.raises(AssertionError):
        Plant(MOT).step(0.5, 0.1, False, T_S)
    with pytest.raises(AssertionError):
        Plant(MOT).step(0.5, 0.0, True, T_S)


def test_integration_converges_with_substeps():
    v = MOT.out1_for_current(80.0)
    coarse, fine = Plant(MOT), Plant(MOT)
    for _ in range(300):
        coarse.step(v, 0.0, False, T_S, substeps=8)
        fine.step(v, 0.0, False, T_S, substeps=64)
    assert coarse.I == pytest.approx(fine.I, rel=5e-3)
