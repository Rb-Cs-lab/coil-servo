"""Channel table integrity: coil values and MVP derived quantities."""

from dataclasses import replace

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
    nominal = replace(MOT, in1_fs_volts=1.0)
    assert nominal.I_FS == pytest.approx(200.0)
    assert nominal.amps_per_lsb == pytest.approx(0.0244, rel=1e-3)
    # the MOT board's IN1 reads ~10 % low (measured 2026-11-11): full-scale
    # code at ~1.10 V, so the scale in use is 200 A x in1_fs_volts
    assert MOT.in1_fs_volts == pytest.approx(1.10)
    assert MOT.I_FS == pytest.approx(200.0 * MOT.in1_fs_volts)
    assert MOT.amps_per_lsb == pytest.approx(MOT.I_FS / 8192)


def test_in1_fs_volts_scales_i_fs_linearly():
    base = replace(MOT, in1_fs_volts=1.0).I_FS
    for v in (0.9, 1.0, 1.07, 1.10, 1.13, 2.0):
        ch = replace(MOT, in1_fs_volts=v)
        assert ch.I_FS == pytest.approx(base * v)
        assert ch.amps_per_lsb == pytest.approx(base * v / 8192)


def test_mvp_measured_thresholds_and_dead_zone():
    assert MOT.V_th == VTH_INSTALLED == (3.52, 3.53, 3.52, 3.56, 3.50, 3.52)
    # gain-7.93 op-amp, terminated OUT1 measured at 1.168 V full scale and
    # -41.3 mV at code 0 (2026-11-11): no FET conducts below ~0.41 of
    # full scale (3385 counts)
    assert MOT.dead_zone == pytest.approx((3.50 / 7.93 + 0.0413) / 1.168, rel=1e-6)
    assert round(MOT.dead_zone * 8192) == 3385


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
