"""Channel table integrity: values must match BOOTSTRAP.md and design.md."""

import pytest

from coil_servo_model import CHANNELS


def test_bootstrap_plant_table():
    assert CHANNELS["mot"].L == 16e-6
    assert CHANNELS["mot"].R_coil == 6.4e-3
    assert CHANNELS["mot"].I_rated == 100.0
    assert CHANNELS["z_shim"].L == 29e-6
    assert CHANNELS["z_shim"].R_coil == 11.6e-3
    assert CHANNELS["x_shim"].L == 57e-6
    assert CHANNELS["x_shim"].R_coil == 14.2e-3
    assert CHANNELS["y_shim"].L == CHANNELS["x_shim"].L
    for ch in CHANNELS.values():
        if ch.name != "mot":
            assert ch.I_rated == 60.0


def test_scaling_rule():
    # DECIDED: I_FS = 1.25 * rated, so rated = 80% of full scale and the
    # 100%-of-rated clamp is 6554 counts on every channel.
    for ch in CHANNELS.values():
        assert ch.I_FS == pytest.approx(1.25 * ch.I_rated)
        assert ch.clamp_counts == 6554


def test_mot_pole_near_100hz():
    # BOOTSTRAP: "The open-loop pole for the MOT channel is therefore
    # around 100 Hz" (with the provisional 12 mOhm loop resistance).
    assert 80 < CHANNELS["mot"].f_pole < 160
