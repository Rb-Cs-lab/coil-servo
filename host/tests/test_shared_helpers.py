"""Helpers shared by the command-line tools and the dashboard: unit
conversion, capture decoding, step-command validation."""

import numpy as np
import pytest

from coil_servo_host.capture import (FS_DEC, decimated_columns, decode_words,
                                     write_csv)
from coil_servo_host.config import (counts_to_amps, i_meas_amps, load_channel,
                                    out1_volts, summary, summary_rows)
from coil_servo_host.step import command_counts


def test_unit_conversions():
    # i_meas is 128 x the mean IN1 code; I_FS = 200 A at 8192 codes
    assert i_meas_amps(8192 * 128, 200.0) == pytest.approx(200.0)
    assert i_meas_amps(-4096 * 128, 200.0) == pytest.approx(-100.0)
    assert counts_to_amps(4096, 200.0) == pytest.approx(100.0)
    model = load_channel("mot")["model"]
    # measured OUT1 scale + resting offset (Test 1, 2026-11-11)
    assert out1_volts(8192, model) == pytest.approx(model.out1_factor
                                                    + model.out1_offset_volts)
    assert out1_volts(0, model) == pytest.approx(model.out1_offset_volts)


def test_decimated_decode_and_columns(tmp_path):
    # two samples: i = +100 and -3 LSB, e = -20 and +5 LSB, packed {e, i}
    i16 = np.array([100, -3])
    e16 = np.array([-20, 5])
    words = ((e16 & 0xFFFF) << 16) | (i16 & 0xFFFF)
    data = decode_words(words.astype(np.uint32), decimated=True)
    assert data.tolist() == [[100, -20], [-3, 5]]
    cols = decimated_columns(data, 200.0)
    lsb = 200.0 / 16384
    assert cols["i_amps"].tolist() == pytest.approx([100 * lsb, -3 * lsb])
    assert cols["setpoint_amps"].tolist() == pytest.approx([80 * lsb, 2 * lsb])
    assert cols["t_s"][1] == pytest.approx(1 / FS_DEC)
    write_csv(tmp_path / "c.csv", cols)
    lines = (tmp_path / "c.csv").read_text().splitlines()
    assert lines[0] == "t_s,i_amps,setpoint_amps" and len(lines) == 3


def test_raw_decode_is_s14():
    words = np.array([(0x2000 << 16) | 0x1FFF], dtype=np.uint32)
    assert decode_words(words, decimated=False).tolist() == [[8191, -8192]]


def test_step_command_validation():
    """command_counts raises ValueError (the dashboard's refusal); the CLI's
    setpoint_counts turns the same cases into SystemExit (test_step.py)."""
    ch = load_channel("mot")
    assert command_counts(ch, 50.0, open_loop=False)[0] == 2048
    with pytest.raises(ValueError, match="rated"):
        command_counts(ch, 100.5, open_loop=False)
    with pytest.raises(ValueError, match="OUT1 only drives up"):
        command_counts(ch, -5.0, open_loop=True)
    with pytest.raises(ValueError, match="open-loop"):
        command_counts(ch, None, open_loop=False, counts=3600)
    assert command_counts(ch, 0.0, open_loop=True)[0] == 0
    assert command_counts(ch, None, open_loop=True, counts=3600)[0] == 3600
    # open loop: above the dead zone, below the clamp for rated current
    c, _ = command_counts(ch, 50.0, open_loop=True)
    assert ch["model"].dead_zone * 8192 < c < ch["cfg"]["out_clamp"]


@pytest.mark.parametrize("name", ["mot", "mot_bench"])
def test_summary_is_built_from_summary_rows(name):
    """The CLI summary and the dashboard show the same rows."""
    ch = load_channel(name)
    lines = summary(ch).splitlines()
    rows = summary_rows(ch)
    assert len(lines) == 1 + len(rows)
    for line, (label, value) in zip(lines[1:], rows):
        assert line == f"  {label:<18s} = {value}"
    labels = [label for label, _ in rows]
    assert ("supply limit" in labels) == (name == "mot_bench")
    assert "board LEDs" in labels and "PI gains" in labels
