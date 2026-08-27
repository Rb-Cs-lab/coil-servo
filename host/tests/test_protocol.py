"""End-to-end protocol test: real Board client against the real server
running in --mock mode on localhost (no hardware)."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from coil_servo_host.board import Board
from coil_servo_model.registers import CFG_BASE, cfg_words

SERVER = Path(__file__).resolve().parents[1] / "board" / "coil_servo_server.py"


@pytest.fixture
def server():
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--mock", "--port", "0"],
        stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline().strip()
    assert line.startswith("LISTENING")
    port = int(line.split()[1])
    yield port
    proc.terminate()
    proc.wait(timeout=5)


def test_word_write_read_roundtrip(server):
    with Board("127.0.0.1", port=server) as b:
        b.write_words(CFG_BASE + 0x40, [0xDEADBEEF, 123, 0])
        assert b.read_words(CFG_BASE + 0x40, 3) == [0xDEADBEEF, 123, 0]


def test_named_cfg_fields_land_on_documented_words(server):
    with Board("127.0.0.1", port=server) as b:
        fields = dict(servo_enable=1, led=0xA5, setpoint=-1000,
                      kp_mant=-1234, ki_shift=28, out_clamp=6554,
                      flip_timeout=125_000_000, dio_invert=0b101)
        b.write_cfg(**fields)
        expected = cfg_words(**fields)
        for word, value in expected.items():
            assert b.read_words(CFG_BASE + 4 * word, 1)[0] == value


def test_cfg_shadow_preserves_other_bits(server):
    with Board("127.0.0.1", port=server) as b:
        b.write_cfg(servo_enable=1, led=0xFF)
        b.write_cfg(led=0)                     # must not clear servo_enable
        assert b.read_words(CFG_BASE, 1)[0] & 1 == 1


def test_pop_reads_same_address(server):
    with Board("127.0.0.1", port=server) as b:
        b.write_words(0x4200_0000, [42])
        popped = b.pop_words(0x4200_0000, 4)
        assert list(popped) == [42, 42, 42, 42]   # mock: same word each pop


def test_out_clamp_guard(server):
    """The host refuses to raise the hard clamp above rated unless the
    guard is explicitly disabled (fabric still clamps either way)."""
    with Board("127.0.0.1", port=server, max_clamp=6554) as b:
        b.write_cfg(out_clamp=6554)                 # 100% of rated: fine
        with pytest.raises(ValueError):
            b.write_cfg(out_clamp=6555)
    with Board("127.0.0.1", port=server, max_clamp=None) as b:
        b.write_cfg(out_clamp=8191)                 # explicit opt-out


def test_out_of_range_refused(server):
    with Board("127.0.0.1", port=server) as b:
        with pytest.raises(IOError):
            b.read_words(0x1000_0000, 1)
        with pytest.raises(IOError):
            b.read_words(0x4800_0000 - 4, 2)      # crosses the window end
