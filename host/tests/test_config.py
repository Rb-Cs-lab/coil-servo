"""channels.toml loader: unit conversions and consistency with the model."""

import pytest

from coil_servo_host.config import load_channel
from coil_servo_model import CHANNELS, decode_gain
from coil_servo_model.loop import T_S


@pytest.mark.parametrize("name", ["mot", "z_shim", "x_shim", "y_shim"])
def test_loads_and_converts(name):
    ch = load_channel(name)
    cfg = ch["cfg"]
    model = CHANNELS[name]

    # scaling rule and clamp: 100% of rated = 6554 counts on every channel
    assert ch["i_fs"] == pytest.approx(model.I_FS)
    assert cfg["out_clamp"] == model.clamp_counts == 6554

    # time conversions: 1 us = 125 ticks of 8 ns
    assert cfg["deadtime"] == 125
    assert cfg["settle"] == 12500
    assert cfg["flip_timeout"] == 2_500_000

    # gain encoding round-trips to a few ppm
    assert decode_gain(cfg["kp_mant"], cfg["kp_shift"]) == pytest.approx(0.5, rel=2**-16)
    ki_tick = decode_gain(cfg["ki_mant"], cfg["ki_shift"])
    assert ki_tick == pytest.approx(15080.0 * T_S, rel=2**-16)

    # enabling must never come from a config file
    assert "servo_enable" not in cfg


def test_zero_window_counts():
    cfg = load_channel("mot")["cfg"]
    assert cfg["zero_win"] == round(0.5 / 125.0 * 8192)   # 33 counts
