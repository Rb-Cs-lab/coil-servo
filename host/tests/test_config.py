"""channels.toml loader: physics consistency with the model, derived
register values, unit conversions, and refusal of unsafe files."""

import pytest

from coil_servo_host.config import load_channel, summary
from coil_servo_model import (BENCH_CHANNELS, CHANNELS, decode_gain,
                              slew_rate_code)
from coil_servo_model.analysis import suggest_gains
from coil_servo_model.channels import VTH_INSTALLED
from coil_servo_model.loop import T_S


@pytest.mark.parametrize("name", ["mot", "z_shim", "x_shim", "y_shim"])
def test_loads_and_derives_from_the_model(name):
    ch = load_channel(name)
    cfg, m = ch["cfg"], ch["model"]

    # channels.toml physics == the model's (one source of numbers)
    assert m == CHANNELS[name]
    assert m.V_th == VTH_INSTALLED
    assert ch["i_fs"] == pytest.approx(200.0)

    # derived values: clamp asks for rated current, floor at 90 % of dead zone
    assert cfg["out_clamp"] == m.clamp_counts
    assert cfg["u_floor"] == round(0.9 * m.dead_zone * 8192)
    assert 0 < cfg["u_floor"] < cfg["out_clamp"]

    # protection
    assert cfg["trip_level"] == round(m.I_rated / 200.0 * 8192)
    assert cfg["slew_up"] == slew_rate_code(200e3, 200.0) == 1074
    assert cfg["slew_down"] == slew_rate_code(50e3, 200.0) == 268

    # MVP: nothing on OUT2 / boost; dither on
    assert cfg["out2_enable"] == 0 and cfg["boost_mode"] == 0
    assert cfg["dither_enable"] == 1

    # gains are the commissioned ones rescaled by the plant gain
    kp, ki_tick = suggest_gains(m)
    assert decode_gain(cfg["kp_mant"], cfg["kp_shift"]) == pytest.approx(kp, rel=1e-3)
    assert decode_gain(cfg["ki_mant"], cfg["ki_shift"]) == pytest.approx(ki_tick, rel=1e-3)

    # time conversions: 1 us = 125 ticks of 8 ns
    assert cfg["deadtime"] == 125
    assert cfg["settle"] == 12500
    assert cfg["flip_timeout"] == 2_500_000

    # enabling must never come from a config file
    assert "servo_enable" not in cfg
    assert "G_pass" in summary(ch)


def test_mot_values():
    cfg = load_channel("mot")["cfg"]
    assert cfg["out_clamp"] == 4474
    assert cfg["u_floor"] == 3234
    assert cfg["trip_level"] == 4096               # 100 A
    assert cfg["trip_timeout"] == 375_000          # 3 ms
    assert cfg["zero_win"] == round(0.5 / 200.0 * 8192)   # 20 counts


BASE_TOML = """
[{name}]
board_host = "x.local"
I_rated = {i_rated}
V_rail = {v_rail}
G_inamp = {g_inamp}
floor_frac = 0.9
kp = 0.1044
ki = 3149.0
deadband_counts = 8
dither_enable = 1
trip_amps = {trip}
trip_ticks = 4
trip_timeout_us = 3000.0
slew_up_amps_per_ms = {slew_up}
slew_down_amps_per_ms = 50.0
zero_win_amps = {zero_win}
zero_holdoff_ticks = 16
deadtime_us = 1.0
settle_us = 100.0
flip_timeout_us = {timeout}
dio_invert = 0
out2_enable = {out2}
out2_invert = 0
boost_mode = 0
{extra}
"""


def write_toml(tmp_path, **kw):
    d = dict(name="mot", i_rated=100.0, v_rail=5.0, g_inamp=1.0, trip=100.0,
             slew_up=200.0, zero_win=0.5, timeout=20000.0, out2=0, extra="")
    d.update(kw)
    p = tmp_path / "channels.toml"
    p.write_text(BASE_TOML.format(**d))
    return p


def test_loader_accepts_a_valid_file(tmp_path):
    ch = load_channel("mot", path=write_toml(tmp_path))
    assert ch["cfg"]["out_clamp"] == CHANNELS["mot"].clamp_counts


@pytest.mark.parametrize("kw", [
    dict(v_rail=6.5),                          # MOT rail above 6 V
    dict(i_rated=250.0),                       # rated beyond the sensor scale
    dict(g_inamp=2.0, trip=120.0, i_rated=90.0),   # trip beyond I_FS = 100 A
    dict(slew_up=0.0),                         # would freeze the setpoint
    dict(zero_win=0.0),                        # a flip could never qualify
    dict(timeout=0.0),                         # removes the TIMEOUT_HOLD net
    dict(out2=1),                              # OUT2 is not fitted
    dict(extra="u_floor_counts = 5000"),       # floor above the clamp
    dict(extra="out_clamp_counts = 9000"),     # clamp outside the DAC range
])
def test_loader_rejects_unsafe_configs(tmp_path, kw):
    with pytest.raises(ValueError):
        load_channel("mot", path=write_toml(tmp_path, **kw))


def test_shim_rail_limit_is_10_volts(tmp_path):
    load_channel("z", path=write_toml(tmp_path, name="z", v_rail=9.0, i_rated=60.0, trip=60.0))
    with pytest.raises(ValueError):
        load_channel("z", path=write_toml(tmp_path, name="z", v_rail=11.0,
                                          i_rated=60.0, trip=60.0))


def test_measured_overrides_win(tmp_path):
    extra = "out_clamp_counts = 4300\nu_floor_counts = 3100\nV_th = [3.4, 3.4]"
    ch = load_channel("mot", path=write_toml(tmp_path, extra=extra))
    assert ch["cfg"]["out_clamp"] == 4300
    assert ch["cfg"]["u_floor"] == 3100
    assert ch["model"].V_th == (3.4, 3.4)


def test_bench_profile_loads_and_matches_the_model():
    ch = load_channel("mot_bench")
    cfg, m = ch["cfg"], ch["model"]
    assert m == BENCH_CHANNELS["mot_bench"]
    assert ch["host"] == load_channel("mot")["host"]      # same board
    assert ch["i_fs"] == pytest.approx(20.0)               # 10 LEM turns
    assert m.lem_turns == 10 and m.I_supply_max == 3.0

    # current ordering: rated < trip < supply limit < I_FS
    trip_amps = cfg["trip_level"] * m.amps_per_lsb
    assert m.I_rated < trip_amps < m.I_supply_max < ch["i_fs"]
    assert cfg["trip_level"] == round(2.8 / 20.0 * 8192) == 1147

    # clamp set directly (4000), floor still 90 % of the dead zone
    assert cfg["out_clamp"] == 4000 == m.clamp_counts
    assert cfg["u_floor"] == round(0.9 * m.dead_zone * 8192) == 3234

    # same physical ramp rates, so 10x the codes at I_FS = 20 A
    assert cfg["slew_up"] == slew_rate_code(200e3, 20.0) == 10737
    assert cfg["slew_down"] == slew_rate_code(50e3, 20.0) == 2684
    assert cfg["zero_win"] == round(0.1 / 20.0 * 8192) == 41
    assert cfg["trip_timeout"] == 375_000

    # gains: suggest_gains on the bench plant (plant gain ~48)
    kp, ki_tick = suggest_gains(m)
    assert decode_gain(cfg["kp_mant"], cfg["kp_shift"]) == pytest.approx(kp, rel=1e-3)
    assert decode_gain(cfg["ki_mant"], cfg["ki_shift"]) == pytest.approx(ki_tick, rel=1e-3)

    assert cfg["dither_enable"] == 1
    assert cfg["out2_enable"] == 0 and cfg["boost_mode"] == 0
    assert "servo_enable" not in cfg
    text = summary(ch)
    assert "supply limit" in text and "LEM 10 primary turns" in text


BENCH_EXTRA = "I_supply_max = {sup}\nlem_turns = {turns}\n"


def bench_toml(tmp_path, sup=3.0, turns=10, i_rated=2.5, trip=2.8, **kw):
    extra = BENCH_EXTRA.format(sup=sup, turns=turns) + kw.pop("extra", "")
    return write_toml(tmp_path, name="mot_bench", i_rated=i_rated, trip=trip,
                      extra=extra, **kw)


def test_loader_accepts_a_valid_bench_file(tmp_path):
    ch = load_channel("mot_bench", path=bench_toml(tmp_path))
    assert ch["i_fs"] == pytest.approx(20.0)
    # no out_clamp_counts in this file -> the clamp is derived from I_rated
    assert ch["cfg"]["out_clamp"] == ch["model"].clamp_counts
    assert ch["model"].out_clamp_counts is None


@pytest.mark.parametrize("kw", [
    dict(trip=3.0),                    # trip at the supply limit: never fires
    dict(trip=3.2),                    # trip above the supply limit
    dict(i_rated=3.0, trip=2.9),       # rated at the supply limit
    dict(sup=0.0, i_rated=2.5, trip=2.8),   # nonsense limit
    dict(turns=0),                     # no turn through the LEM
    dict(turns=2.5),                   # turns must be whole
    dict(v_rail=6.5),                  # the MOT coil's 6 V rail limit
])
def test_loader_rejects_unsafe_bench_configs(tmp_path, kw):
    with pytest.raises(ValueError):
        load_channel("mot_bench", path=bench_toml(tmp_path, **kw))


def test_lem_turns_change_the_scale(tmp_path):
    ch = load_channel("mot_bench", path=bench_toml(tmp_path, turns=5))
    assert ch["i_fs"] == pytest.approx(40.0)
    assert ch["cfg"]["trip_level"] == round(2.8 / 40.0 * 8192)


def test_in_amp_gain_changes_the_scale(tmp_path):
    ch = load_channel("mot", path=write_toml(tmp_path, g_inamp=2.0, trip=90.0,
                                             i_rated=90.0))
    assert ch["i_fs"] == pytest.approx(100.0)
    assert ch["cfg"]["trip_level"] == round(90.0 / 100.0 * 8192)
