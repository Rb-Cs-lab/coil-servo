"""channels.toml loader: physics consistency with the model, derived
register values, unit conversions, and refusal of unsafe files."""

import re
from dataclasses import replace

import pytest

from coil_servo_host.config import (BENCH_LED, CONFIG_PATH, banner,
                                    channel_table, identify_profile,
                                    load_channel, profile_report, summary)
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
    # nominal chain 200 A at 1 V on IN1, times the board's measured IN1 scale
    assert ch["i_fs"] == pytest.approx(200.0 * m.in1_fs_volts) == m.I_FS

    # derived values: clamp asks for rated current, floor at 90 % of dead zone
    assert cfg["out_clamp"] == m.clamp_counts
    assert cfg["u_floor"] == round(0.9 * m.dead_zone * 8192)
    assert 0 < cfg["u_floor"] < cfg["out_clamp"]

    # protection
    i_fs = ch["i_fs"]
    assert cfg["trip_level"] == round(m.I_rated / i_fs * 8192)
    assert cfg["trip_level"] * m.amps_per_lsb <= m.I_rated + m.amps_per_lsb / 2
    assert cfg["slew_up"] == slew_rate_code(200e3, i_fs)
    assert cfg["slew_down"] == slew_rate_code(50e3, i_fs)
    if m.in1_fs_volts == 1.0:
        assert (cfg["slew_up"], cfg["slew_down"]) == (1074, 268)

    # MVP: nothing on OUT2 / boost; dither on
    assert cfg["out2_enable"] == 0 and cfg["boost_mode"] == 0
    assert cfg["dither_enable"] == 1

    # gains are the commissioned ones rescaled by the plant gain (at the
    # IN1 scale in use, in1_fs_volts included)
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
    ch = load_channel("mot")
    cfg, i_fs = ch["cfg"], ch["i_fs"]
    # OUT1 measured 2026-11-11 (Test 1): 142.6 uV/count terminated,
    # -41.3 mV at code 0, op-amp gain 7.93
    assert cfg["out_clamp"] == 4144
    assert cfg["u_floor"] == 3047
    # 100 A (4096 counts at the nominal I_FS 200 A, 3724 at 220 A)
    assert cfg["trip_level"] == round(100.0 / i_fs * 8192)
    assert cfg["trip_level"] * i_fs / 8192 == pytest.approx(100.0, abs=0.02)
    assert cfg["trip_timeout"] == 375_000          # 3 ms
    assert cfg["zero_win"] == round(0.5 / i_fs * 8192)    # ~20 counts


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


def bench_with_gain(tmp_path, g_inamp: float):
    """The real channels.toml with only [mot_bench]'s G_inamp line changed
    -- the one-line switch for the optional in-amp gain of 10."""
    text = CONFIG_PATH.read_text()
    head, sec = text.split("[mot_bench]", 1)
    body, rest = sec.split("\n[", 1)
    body, n = re.subn(r"(?m)^G_inamp = .*$", f"G_inamp = {g_inamp}", body)
    assert n == 1
    p = tmp_path / "channels.toml"
    p.write_text(head + "[mot_bench]" + body + "\n[" + rest)
    return p


@pytest.mark.parametrize("g_inamp", [1.0, 10.0])
def test_bench_profile_loads_and_matches_the_model(tmp_path, g_inamp):
    path = bench_with_gain(tmp_path, g_inamp)
    ch = load_channel("mot_bench", path=path)
    cfg, m = ch["cfg"], ch["model"]
    table = channel_table("mot_bench", path)
    # physics mirror the model; the clamp override lives in channels.toml
    # only (build_model), so compare it against the file
    assert m == replace(BENCH_CHANNELS["mot_bench"], G_inamp=g_inamp,
                        out_clamp_counts=m.out_clamp_counts)
    assert m.out_clamp_counts == table.get("out_clamp_counts")
    mot = load_channel("mot")
    assert ch["host"] == mot["host"]                       # same board
    assert m.in1_fs_volts == mot["model"].in1_fs_volts     # same board
    i_fs = 200.0 * m.in1_fs_volts / g_inamp                # LEM: one pass
    assert ch["i_fs"] == pytest.approx(i_fs)
    assert m.lem_turns == 1 and m.I_supply_max == 3.0
    assert ch["bench"] is True and mot["bench"] is False

    # current ordering: rated < trip < supply limit < I_FS
    trip_amps = cfg["trip_level"] * m.amps_per_lsb
    assert m.I_rated < trip_amps < m.I_supply_max < ch["i_fs"]
    assert cfg["trip_level"] == round(2.8 / i_fs * 8192)   # 104 / 1042 at 1.10 V
    # the trip fires when the 1 us-averaged reading exceeds the level; the
    # supply limit is 0.19-0.2 A higher (~8 / ~82 counts). The 4-sample
    # persistence cannot carry the current across that (closed-loop rise
    # near 2.8 A ~1 mA/us), so the margin is what the IN1 offset must not
    # eat (bench doc 4.1)
    margin_amps = m.I_supply_max - cfg["trip_level"] * m.amps_per_lsb
    assert 0.15 < margin_amps < 0.21
    assert margin_amps / m.amps_per_lsb > 7                # whole counts

    # OUT1-side values do not depend on the sensor: clamp set directly in
    # the file, floor measured (u_floor_counts) or 90 % of the dead zone
    assert cfg["out_clamp"] == m.clamp_counts == table["out_clamp_counts"]
    assert cfg["u_floor"] == table.get(
        "u_floor_counts", round(0.9 * m.dead_zone * 8192))
    assert m.dead_zone * 8192 * 0.8 < cfg["u_floor"] < cfg["out_clamp"]
    assert cfg["out_clamp"] > m.dead_zone * 8192

    # amps-denominated values follow the scale automatically
    assert cfg["slew_up"] == slew_rate_code(200e3, i_fs)
    assert cfg["slew_down"] == slew_rate_code(50e3, i_fs)
    assert cfg["zero_win"] == round(0.5 / i_fs * 8192)     # 19 / 186 at 1.10 V
    assert cfg["trip_timeout"] == 375_000

    # gains in counts per amp (tuned on the bench, channels.toml) -> the
    # normalized gains follow I_FS: kp = counts/A x I_FS / 8192
    kp = table["kp_counts_per_amp"] * i_fs / 8192
    ki_tick = table["ki_counts_per_amp_ms"] * 1e3 * i_fs / 8192 * T_S
    assert decode_gain(cfg["kp_mant"], cfg["kp_shift"]) == pytest.approx(kp, rel=1e-3)
    assert decode_gain(cfg["ki_mant"], cfg["ki_shift"]) == pytest.approx(ki_tick, rel=1e-3)

    # the bench marker: LED 7 on the board, banner in every tool
    assert cfg["led"] == BENCH_LED == 0x80 and mot["cfg"]["led"] == 0
    assert "NEVER use with the SE-600-5" in banner(ch)
    assert banner(mot) == ""

    assert cfg["dither_enable"] == 1
    assert cfg["out2_enable"] == 0 and cfg["boost_mode"] == 0
    assert "servo_enable" not in cfg
    text = summary(ch)
    assert "supply limit" in text and "LED 7 lit" in text


def test_board_profile_identification():
    mot, bench = load_channel("mot"), load_channel("mot_bench")
    assert identify_profile(mot["cfg"], mot["host"]) == ["mot"]
    assert identify_profile(bench["cfg"], bench["host"]) == ["mot_bench"]
    assert identify_profile({}, mot["host"]) == []          # not configured

    assert profile_report(bench["cfg"], bench)[0] is True
    ok, text = profile_report(bench["cfg"], mot)            # wrong name
    assert not ok and "'mot_bench'" in text and "WARNING" in text
    ok, text = profile_report(mot["cfg"], bench)
    assert not ok and "'mot'" in text
    mixed = dict(mot["cfg"], trip_level=bench["cfg"]["trip_level"])
    ok, text = profile_report(mixed, mot)
    assert not ok and "UNKNOWN" in text


BENCH_EXTRA = "bench = true\nI_supply_max = {sup}\nlem_turns = {turns}\n"


def bench_toml(tmp_path, sup=3.0, turns=1, i_rated=2.5, trip=2.8, **kw):
    extra = BENCH_EXTRA.format(sup=sup, turns=turns) + kw.pop("extra", "")
    return write_toml(tmp_path, name="mot_bench", i_rated=i_rated, trip=trip,
                      extra=extra, **kw)


def test_loader_accepts_a_valid_bench_file(tmp_path):
    ch = load_channel("mot_bench", path=bench_toml(tmp_path))
    # no in1_fs_volts in this file -> the model's (the board's measured) scale
    m = ch["model"]
    assert m.in1_fs_volts == BENCH_CHANNELS["mot_bench"].in1_fs_volts
    assert ch["i_fs"] == pytest.approx(200.0 * m.in1_fs_volts)
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


def test_bench_flag_and_supply_limit_go_together(tmp_path):
    # supply limit without bench = true
    p = write_toml(tmp_path, name="mot_bench", i_rated=2.5, trip=2.8,
                   extra="I_supply_max = 3.0\n")
    with pytest.raises(ValueError):
        load_channel("mot_bench", path=p)
    # bench = true without a supply limit
    p = write_toml(tmp_path, extra="bench = true\n")
    with pytest.raises(ValueError):
        load_channel("mot", path=p)


def test_gains_in_counts_per_amp(tmp_path):
    # the I_FS-independent form: same physical loop at any sensor scale
    base = BASE_TOML.replace("kp = 0.1044\nki = 3149.0\n",
                             "kp_counts_per_amp = 3.6852\n"
                             "ki_counts_per_amp_ms = 111.14\n")
    for g in (1.0, 2.0):
        p = tmp_path / f"g{g}.toml"
        p.write_text(base.format(name="mot", i_rated=90.0, v_rail=5.0,
                                 g_inamp=g, trip=90.0, slew_up=200.0,
                                 zero_win=0.5, timeout=20000.0, out2=0,
                                 extra=""))
        ch = load_channel("mot", path=p)
        kp, ki_tick = suggest_gains(ch["model"])
        c = ch["cfg"]
        assert decode_gain(c["kp_mant"], c["kp_shift"]) == pytest.approx(kp, rel=1e-3)
        assert decode_gain(c["ki_mant"], c["ki_shift"]) == pytest.approx(ki_tick, rel=1e-3)
    # both forms at once is refused
    p = write_toml(tmp_path, extra="kp_counts_per_amp = 3.6852\n"
                                   "ki_counts_per_amp_ms = 111.14\n")
    with pytest.raises(ValueError):
        load_channel("mot", path=p)


def test_lem_turns_change_the_scale(tmp_path):
    # kept as a parameter (not used on the bench as built)
    ch = load_channel("mot_bench", path=bench_toml(tmp_path, turns=5))
    i_fs = 200.0 * ch["model"].in1_fs_volts / 5            # 40 A nominal
    assert ch["i_fs"] == pytest.approx(i_fs)
    assert ch["cfg"]["trip_level"] == round(2.8 / i_fs * 8192)


def test_in_amp_gain_changes_the_scale(tmp_path):
    ch = load_channel("mot", path=write_toml(tmp_path, g_inamp=2.0, trip=90.0,
                                             i_rated=90.0))
    i_fs = 200.0 * ch["model"].in1_fs_volts / 2            # 100 A nominal
    assert ch["i_fs"] == pytest.approx(i_fs)
    assert ch["cfg"]["trip_level"] == round(90.0 / i_fs * 8192)



def test_sensor_offsets_convert_and_are_bounded(tmp_path):
    import re
    from coil_servo_host.config import CONFIG_PATH
    # replace the first ([mot]) in1_offset_amps line, whatever is stored
    text = re.sub(r"(?m)^in1_offset_amps = .*$", "in1_offset_amps = -5.8",
                  CONFIG_PATH.read_text(encoding="utf-8"), count=1)
    p = tmp_path / "channels.toml"
    p.write_text(text, encoding="utf-8")
    ch = load_channel("mot", p)
    assert ch["cfg"]["in1_offset"] == round(-5.8 / ch["i_fs"] * 8192)  # -216
    bad = text.replace("in1_offset_amps = -5.8", "in1_offset_amps = 30.0", 1)
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="10 %"):
        load_channel("mot", p)
