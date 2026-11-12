"""End-to-end protocol test: real Board client against the real server
running in --mock mode on localhost (no hardware)."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from coil_servo_host.board import Board
from coil_servo_host.deploy import refuse_if_running
from coil_servo_model.registers import CFG_BASE, STS_BASE, cfg_words

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


def test_cfg_shadow_syncs_from_hardware_on_connect(server):
    """A FRESH connection must inherit the live config, not assume zeros --
    the zeros assumption cleared servo_enable live on the bench."""
    with Board("127.0.0.1", port=server) as b:
        b.write_cfg(servo_enable=1, setpoint=-1000, kp_mant=-1234)
    with Board("127.0.0.1", port=server) as b2:   # new session, new shadow
        b2.write_cfg(open_loop=1)                 # shares word 0 with enable
        w0 = b2.read_words(CFG_BASE, 1)[0]
        assert w0 & 1 == 1, "servo_enable clobbered by a fresh connection"
        assert w0 & (1 << 16), "open_loop not set"
        expected = cfg_words(servo_enable=1, setpoint=-1000, kp_mant=-1234)
        assert b2.read_words(CFG_BASE + 4, 1)[0] == expected[1]
        assert b2.read_words(CFG_BASE + 8, 1)[0] == expected[2]


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


def test_two_clients_concurrently(server):
    """watch holds one connection while another tool works -- the server
    must service both (single-client serving deadlocked this live)."""
    with Board("127.0.0.1", port=server) as watcher:
        watcher.read_words(CFG_BASE, 1)          # watcher connection active
        with Board("127.0.0.1", port=server) as worker:
            worker.write_words(CFG_BASE + 8, [77])
            assert worker.read_words(CFG_BASE + 8, 1) == [77]
        assert watcher.read_words(CFG_BASE + 8, 1) == [77]


def test_deploy_refuses_while_running(server):
    """The deploy guard: refuse to reload the FPGA while the bridge is
    enabled or current flows; allow when quiet, with --force, or when no
    server answers (fresh board)."""
    with Board("127.0.0.1", port=server) as b:
        # stage an ACTIVE board in the mock: bridge_en (bit 5 of flags)
        # set, current flowing (word 1)
        b.write_words(STS_BASE, [1 << 5])
        b.write_words(STS_BASE + 4, [50 * 128])
    with pytest.raises(SystemExit):
        refuse_if_running("127.0.0.1", server, force=False)
    refuse_if_running("127.0.0.1", server, force=True)   # override allowed

    with Board("127.0.0.1", port=server) as b:           # quiet board: fine
        b.write_words(STS_BASE, [0])
        b.write_words(STS_BASE + 4, [0])
    refuse_if_running("127.0.0.1", server, force=False)

    # nothing listening = fresh board: fine
    refuse_if_running("127.0.0.1", 1, force=False)


def test_out_of_range_refused(server):
    with Board("127.0.0.1", port=server) as b:
        with pytest.raises(IOError):
            b.read_words(0x1000_0000, 1)
        with pytest.raises(IOError):
            b.read_words(0x4800_0000 - 4, 2)      # crosses the window end


def test_new_mvp_fields_round_trip_through_read_cfg(server):
    """Words 15-20 (slew, floor, trip) and the new ctrl bits land where the
    register map says and come back through Board.read_cfg."""
    from coil_servo_host.config import load_channel
    cfg = load_channel("mot")["cfg"]
    with Board("127.0.0.1", port=server) as b:
        b.apply_config(cfg)
        rb = b.read_cfg()
    assert {k: rb[k] for k in cfg} == cfg
    assert rb["servo_enable"] == 0


def test_no_server_gives_a_deploy_hint():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                   # nothing listening there
    with pytest.raises(ConnectionError, match="coil_servo_host.deploy"):
        Board("127.0.0.1", port=port, timeout=1.0)


def test_wait_for_run_times_out_and_disables(server):
    """Mock board never leaves IDLE: wait_for_run must give up and leave
    servo_enable at 0, so a later arm edge cannot start the servo."""
    with Board("127.0.0.1", port=server) as b:
        b.write_cfg(servo_enable=1)
        with pytest.raises(SystemExit):
            b.wait_for_run(timeout=0.3)
        assert b.read_cfg()["servo_enable"] == 0


def test_refuses_writes_to_an_old_register_map(server):
    """A bitstream reporting another map revision (the 2026-08-27 image
    reads 0) must not receive CFG writes: its 16-word block would alias
    words 16-20 onto ctrl/setpoint/gains."""
    with Board("127.0.0.1", port=server) as b:
        b.write_words(STS_BASE, [0])             # pretend: old bitstream
    with Board("127.0.0.1", port=server) as b:
        with pytest.raises(RuntimeError, match="register-map revision 0"):
            b.write_cfg(servo_enable=1)
        assert b.read_words(CFG_BASE, 1) == [0]
    with Board("127.0.0.1", port=server, check_map=False) as b:
        b.write_cfg(led=1)                       # explicit diagnostic opt-out


def test_mock_server_map_rev_matches_the_register_map():
    import importlib.util
    from coil_servo_model.registers import MAP_REV
    spec = importlib.util.spec_from_file_location("srv", SERVER)
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    assert srv.MockMem.MAP_REV == MAP_REV


def test_deploy_post_check(server):
    from coil_servo_host.deploy import verify_loaded_safe
    verify_loaded_safe("127.0.0.1", server)          # mock: IDLE, all zero
    with Board("127.0.0.1", port=server) as b:
        b.write_words(STS_BASE, [(3 << 24) | (1 << 7)])   # armed high
    with pytest.raises(SystemExit, match="arm"):
        verify_loaded_safe("127.0.0.1", server)
    with Board("127.0.0.1", port=server) as b:
        b.write_words(STS_BASE, [0])                  # old bitstream
    with pytest.raises(SystemExit, match="revision 0"):
        verify_loaded_safe("127.0.0.1", server)


def test_sync_shadow_picks_up_another_clients_write(server):
    """A long-lived connection (the dashboard) re-syncs before writing, so
    it cannot put back a word another tool changed meanwhile."""
    with Board("127.0.0.1", port=server) as old, \
            Board("127.0.0.1", port=server) as other:
        other.write_cfg(servo_enable=1)          # old's shadow still has 0
        old.sync_shadow()
        old.write_cfg(led=3)                     # shares word 0
        assert old.read_cfg()["servo_enable"] == 1


def test_leaving_open_loop_only_from_idle(server):
    """Open-loop wind-up guard (found on the loopback, 2026-11-11): while
    the FSM runs, open_loop 1 -> 0 is refused, or deferred when it comes
    with a stop; from IDLE it is applied."""
    from coil_servo_model.registers import MAP_REV
    with Board("127.0.0.1", port=server) as b:
        b.write_cfg(open_loop=1, servo_enable=1)
        b.write_words(STS_BASE, [(MAP_REV << 24) | 1])      # state RUN
        with pytest.raises(RuntimeError, match="open loop"):
            b.write_cfg(open_loop=0)
        b.write_cfg(servo_enable=0, open_loop=0)            # a stop
        rb = b.read_cfg()
        assert rb["servo_enable"] == 0 and rb["open_loop"] == 1
        b.write_words(STS_BASE, [MAP_REV << 24])            # state IDLE
        assert b.stop() == "IDLE"
        assert b.read_cfg()["open_loop"] == 0



def test_zero_calibration_measures_raw_codes(server, tmp_path):
    """coil_servo_host.zero: mean raw IN1/IN2 codes with the servo stopped
    (mock FIFO returns one word every pop), written into channels.toml."""
    import shutil
    from coil_servo_host import zero
    from coil_servo_host.config import CONFIG_PATH, load_channel
    from coil_servo_model.registers import MAP_REV
    raw_in1, raw_in2 = -238, 7                  # a -5.8 A LEM, IN2 ~ 0
    i_fs = load_channel("mot")["i_fs"]          # the scale the tool uses
    word = ((raw_in2 & 0x3FFF) << 16) | (raw_in1 & 0x3FFF)
    with Board("127.0.0.1", port=server) as b:
        b.write_words(0x4200_0000, [word])               # FIFO data
        b.write_words(STS_BASE + 4 * 5, [16384])          # FIFO full
        r = zero.measure_offsets(b, i_fs, records=2, spacing_s=0.0)
        assert r["in1_counts"] == raw_in1 and r["in2_counts"] == raw_in2
        assert abs(r["in1_amps"] - raw_in1 * i_fs / 8192) < 1e-9
        # refused while the servo runs
        b.write_words(STS_BASE, [(MAP_REV << 24) | 1 | (1 << 5)])
        with pytest.raises(RuntimeError, match="stopped"):
            zero.measure_offsets(b, i_fs, records=1)
    cfg_path = tmp_path / "channels.toml"
    shutil.copy(CONFIG_PATH, cfg_path)
    zero.write_offsets("mot", r["in1_amps"], r["in2_amps"], cfg_path)
    ch = load_channel("mot", cfg_path)
    assert ch["cfg"]["in1_offset"] == raw_in1
    assert ch["cfg"]["in2_offset"] == raw_in2
    # other sections untouched
    assert (load_channel("mot_bench", cfg_path)["cfg"]["in1_offset"]
            == load_channel("mot_bench")["cfg"]["in1_offset"])
