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


BASE_TOML = """
[test]
board_host = "x.local"
I_rated = {i_rated}
I_FS = {i_fs}
kp = 0.5
ki = 15080.0
deadband_counts = 8
zero_win_amps = {zero_win}
zero_holdoff_ticks = 16
deadtime_us = 1.0
settle_us = 100.0
flip_timeout_us = {timeout}
dio_invert = 0
out2_invert = 0
boost_mode = 1
"""


def write_toml(tmp_path, **kw):
    defaults = dict(i_rated=100.0, i_fs=125.0, zero_win=0.5, timeout=20000.0)
    defaults.update(kw)
    p = tmp_path / "channels.toml"
    p.write_text(BASE_TOML.format(**defaults))
    return p


def test_loader_rejects_unsafe_configs(tmp_path):
    # a valid file loads
    load_channel("test", path=write_toml(tmp_path))
    # I_FS below rated: the 100%-of-rated clamp would not be representable
    with pytest.raises(ValueError):
        load_channel("test", path=write_toml(tmp_path, i_fs=80.0))
    # zero window of zero: a flip could never qualify
    with pytest.raises(ValueError):
        load_channel("test", path=write_toml(tmp_path, zero_win=0.0))
    # disabled flip timeout: removes the TIMEOUT_HOLD safety net
    with pytest.raises(ValueError):
        load_channel("test", path=write_toml(tmp_path, timeout=0.0))
