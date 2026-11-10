"""Channel table integrity: coil values and MVP derived quantities."""

import pytest

from coil_servo_model import CHANNELS
from coil_servo_model.channels import VTH_INSTALLED

MOT = CHANNELS["mot"]


def test_coil_table():
    for table in (CHANNELS,):
        assert table["mot"].L == 16e-6
        assert table["mot"].R_coil == 6.4e-3
        assert table["mot"].I_rated == 100.0
        assert table["z_shim"].L == 29e-6
        assert table["z_shim"].R_coil == 11.6e-3
        assert table["x_shim"].L == 57e-6
        assert table["x_shim"].R_coil == 14.2e-3
        assert table["y_shim"].L == table["x_shim"].L
        for name in ("z_shim", "x_shim", "y_shim"):
            assert table[name].I_rated == 60.0


def test_mvp_sensing_scale():
    # LEM 2000:1 -> 10 ohm burden -> in-amp gain 1: +1 V on IN1 = 200 A,
    # 24.4 mA per ADC code (mvp_architecture.md 6).
    assert MOT.I_FS == pytest.approx(200.0)
    assert MOT.amps_per_lsb == pytest.approx(0.0244, rel=1e-3)


def test_mvp_measured_thresholds_and_dead_zone():
    assert MOT.V_th == VTH_INSTALLED == (3.52, 3.53, 3.52, 3.56, 3.50, 3.52)
    # gain-8 op-amp, terminated OUT1: no FET conducts below ~0.44 of full scale
    assert MOT.dead_zone == pytest.approx(3.50 / 7.98, rel=1e-6)


def test_mvp_command_clamp_asks_for_rated_current():
    u = MOT.clamp_counts / 8192
    assert MOT.dead_zone < u < 1.0
    gate = MOT.gate_command(u)
    assert MOT.bank_current(gate) == pytest.approx(MOT.I_rated, rel=0.01)


def test_mvp_rail_limits():
    # fixed requirement: MOT rail <= 6 V, shim rails <= 10 V
    assert MOT.V_rail <= 6.0
    for name in ("z_shim", "x_shim", "y_shim"):
        assert CHANNELS[name].V_rail <= 10.0


def test_mvp_rail_can_reach_rated_current():
    # with the bank fully on, the 5 V rail must push well past 100 A
    for ch in CHANNELS.values():
        assert ch.max_current > 1.5 * ch.I_rated


def test_mvp_has_no_clamp_or_boost():
    for ch in CHANNELS.values():
        assert ch.out2_enable is False


def test_freewheel_turnoff_times():
    # mvp_architecture.md 3.3: ~1.3 ms MOT, ~1.5 ms Z, ~2.7 ms X/Y from rated
    assert CHANNELS["mot"].freewheel_time == pytest.approx(1.28e-3, rel=0.05)
    assert CHANNELS["z_shim"].freewheel_time == pytest.approx(1.44e-3, rel=0.05)
    assert CHANNELS["x_shim"].freewheel_time == pytest.approx(2.70e-3, rel=0.05)
