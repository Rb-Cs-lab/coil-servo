"""Dashboard backend: polling, units, access control, map-revision refusal,
read-only monitoring, logging, capture -- against the model-driven
SimBoard and against the real Board talking to the mock register server.
No browser involved."""

import csv
import json
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")      # starlette's httpx deprecation note
    from starlette.testclient import TestClient

from coil_servo_host.board import FIFO_ADDR, Board
from coil_servo_host.config import CONFIG_PATH, load_channel
from coil_servo_host.dashboard import build_channels
from coil_servo_host.dashboard.access import AccessPolicy
from coil_servo_host.dashboard.app import create_app
from coil_servo_host.dashboard.logs import STATUS_COLUMNS, ActionLog
from coil_servo_host.dashboard.servo_channel import CoilServoChannel
from coil_servo_model.registers import MAP_REV, STS_BASE

SERVER = Path(__file__).resolve().parents[1] / "board" / "coil_servo_server.py"
LOCAL = ("127.0.0.1", 50000)
REMOTE = ("192.168.0.20", 50000)
HDR = {"X-Coil-Servo": "1"}


def wait_for(cond, timeout=5.0):
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


@pytest.fixture
def mock_server():
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--mock", "--port", "0"],
        stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline().strip()
    assert line.startswith("LISTENING")
    yield int(line.split()[1])
    proc.terminate()
    proc.wait(timeout=5)


def make_app(tmp_path, *, sim_old=(), policy=None, **kw):
    log = ActionLog(tmp_path)
    devices = build_channels(["mot"], simulate=["*"], sim_old=sim_old,
                             logdir=tmp_path, action_log=log,
                             poll_interval=0.02, **kw)
    return create_app(devices, policy or AccessPolicy(), log), devices


@pytest.fixture
def sim(tmp_path):
    """(local client, devices, tmp_path), the simulated MOT online."""
    app, devices = make_app(tmp_path)
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: c.get("/api/devices/mot").json().get("state"))
        yield c, devices, tmp_path


def enable_and_arm(c):
    assert act(c, "apply_profile", confirm=True).status_code == 200
    assert act(c, "enable", confirm=True).status_code == 200
    assert act(c, "sim_arm", level=True).status_code == 200
    wait_for(lambda: state(c)["state"] == "RUN")


def act(c, action, **params):
    return c.post(f"/api/devices/mot/actions/{action}", json=params,
                  headers=HDR)


def state(c):
    return c.get("/api/devices/mot").json()


# ---- access policy ---------------------------------------------------------

def test_access_policy_defaults_to_localhost_only():
    p = AccessPolicy()
    assert p.is_local("127.0.0.1") and p.is_local("::1")
    assert p.is_local("::ffff:127.0.0.1")
    assert not p.is_local("192.168.0.20")
    assert not p.is_local("testclient") and not p.is_local(None)
    assert p.allowed("monitor", "192.168.0.20")
    assert not p.allowed("control", "192.168.0.20")
    assert not p.allowed("stop", "192.168.0.20")
    lan = AccessPolicy(allow=("127.0.0.1", "192.168.0.0/24"),
                       remote_stop=True)
    assert lan.is_local("192.168.0.20") and not lan.is_local("10.0.0.1")
    assert lan.allowed("stop", "10.0.0.1")
    assert not lan.allowed("control", "10.0.0.1")


def test_remote_viewer_can_watch_but_not_control(tmp_path):
    app, devices = make_app(tmp_path)
    with TestClient(app, client=REMOTE, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("state"))
        assert c.get("/api/whoami").json()["control"] is False
        assert c.get("/api/devices").status_code == 200
        for action, params in [("stop", {}), ("enable", {"confirm": True}),
                               ("capture", {}), ("apply_profile",
                                                {"confirm": True})]:
            r = act(c, action, **params)
            assert r.status_code == 403, action
            assert "lab PC" in r.json()["error"]
    assert devices["mot"].board is None or \
        devices["mot"].board.cfg_writes == []
    rows = list(csv.reader(open(next(tmp_path.glob("actions_*.csv")))))
    assert any(r[3] == "192.168.0.20" and r[6].startswith("refused")
               for r in rows[1:])


def test_control_needs_header_and_confirmation(sim):
    c, devices, _ = sim
    r = c.post("/api/devices/mot/actions/stop", json={})    # no header
    assert r.status_code == 400 and "X-Coil-Servo" in r.json()["error"]
    r = act(c, "enable")                                     # no confirm
    assert r.status_code == 400 and "confirm" in r.json()["error"]
    r = c.post("/api/devices/mot/actions/stop", json={},
               headers=dict(HDR, Host="evil.example:8080"))
    assert r.status_code == 400                              # rebinding
    assert act(c, "stop").status_code == 200                 # no confirm needed


# ---- polling and units -------------------------------------------------------

def test_status_polling_and_unit_conversion(mock_server, tmp_path):
    """Real Board against the mock server: the dashboard converts the STS
    words exactly like watch.py."""
    ch = load_channel("mot")
    with Board("127.0.0.1", port=mock_server) as b:
        # RUN, armed, bridge on; i_meas = 2048 codes x 128 (= 50 A at
        # I_FS 200 A); sp_active = -1024 counts; u14 = 4096 counts
        b.write_words(STS_BASE, [(MAP_REV << 24) | (1 << 7) | (1 << 5) | 1,
                                 2048 * 128, (-1024) & 0x3FFF, 4096])
    dev = CoilServoChannel(
        "mot", table={"board_host": "127.0.0.1"}, profile=ch,
        profile_error=None, poll_interval=0.02, logdir=tmp_path,
        connect=lambda: Board("127.0.0.1", port=mock_server))
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("state") == "RUN")
        s = dev.snapshot()
        assert s["connection"] == "online" and s["map_ok"]
        assert s["i_amps"] == pytest.approx(50.0)
        assert s["sp_amps"] == pytest.approx(-25.0)
        assert s["u14"] == 4096
        assert s["out1_volts"] == pytest.approx(0.5 * ch["model"].out1_factor)
        assert s["flags"]["armed"] and s["flags"]["bridge_en"]
        # the mock board was never configured: only the profile alarm
        assert [a.split(":")[0] for a in s["alarms"]] == ["PROFILE MISMATCH"]
        h = dev.history(60)
        assert len(h["t"]) >= 1 and h["i_amps"][-1] == pytest.approx(50.0)
    finally:
        dev.close()


def test_simulated_run_through_the_api(sim):
    c, devices, _ = sim
    assert state(c)["state"] == "IDLE"
    r = act(c, "enable", source="register", confirm=True)
    assert r.status_code == 409 and "apply the profile" in r.json()["error"]
    assert act(c, "apply_profile", confirm=True).status_code == 200
    r = act(c, "enable", source="register", confirm=True)
    assert r.status_code == 200, r.text
    s = state(c)
    assert s["pending"] and "arm" in s["pending"]["message"]
    assert act(c, "sim_arm", level=True).status_code == 200
    wait_for(lambda: state(c)["state"] == "RUN")
    wait_for(lambda: state(c)["pending"] is None)
    assert act(c, "setpoint", amps=40, confirm=True).status_code == 200
    wait_for(lambda: abs(state(c)["i_amps"] - 40) < 0.2)
    s = state(c)
    assert s["sp_amps"] == pytest.approx(40, abs=0.05)
    assert s["out1_volts"] == pytest.approx(s["u14"] / 8192 *
                                            devices["mot"].model.out1_factor,
                                            abs=1e-4)
    r = act(c, "setpoint", amps=100.5, confirm=True)       # above rated
    assert r.status_code == 400 and "rated" in r.json()["error"]
    assert act(c, "stop").status_code == 200
    wait_for(lambda: state(c)["state"] == "IDLE")


def test_enable_without_arm_edge_times_out_and_disables(tmp_path):
    app, devices = make_app(tmp_path, enable_timeout=0.2)
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("state"))
        assert act(c, "apply_profile", confirm=True).status_code == 200
        assert act(c, "enable", confirm=True).status_code == 200
        wait_for(lambda: state(c)["pending"] is None)
        assert "did not reach RUN" in state(c)["notice"]["text"]
        assert devices["mot"].board.read_cfg()["servo_enable"] == 0


def test_trip_and_acknowledge(sim):
    c, devices, _ = sim
    enable_and_arm(c)
    act(c, "setpoint", amps=20, confirm=True)
    act(c, "sim_trip")
    wait_for(lambda: state(c)["state"] == "TRIPPED")
    assert any("TRIPPED" in a for a in state(c)["alarms"])
    assert act(c, "enable", confirm=True).status_code == 409   # not IDLE
    assert act(c, "trip_ack", confirm=True).status_code == 200
    wait_for(lambda: state(c)["state"] == "IDLE")
    assert act(c, "flip_fault_ack", confirm=True).status_code == 409


# ---- old bitstream ------------------------------------------------------------

def test_old_bitstream_is_monitoring_only(tmp_path):
    app, devices = make_app(tmp_path, sim_old=["mot"])
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("state"))
        s = state(c)
        assert s["map_ok"] is False and s["map_rev"] == 0
        assert any("OLD BITSTREAM" in a for a in s["alarms"])
        assert "old bitstream" in s["control_block"]
        for action, params in [("enable", {"confirm": True}), ("stop", {}),
                               ("capture", {}), ("setpoint",
                                                 {"amps": 1, "confirm": True})]:
            r = act(c, action, **params)
            assert r.status_code == 409, action
            assert "revision 0" in r.json()["error"]
        assert devices["mot"].board.cfg_writes == []


def test_old_bitstream_on_the_mock_server(mock_server, tmp_path):
    with Board("127.0.0.1", port=mock_server) as b:
        b.write_words(STS_BASE, [0])                 # map_rev 0
    dev = CoilServoChannel(
        "mot", table={}, profile=load_channel("mot"), profile_error=None,
        poll_interval=0.02, connect=lambda: Board("127.0.0.1",
                                                  port=mock_server))
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("map_ok") is False)
        with pytest.raises(Exception, match="revision 0"):
            dev.perform("enable", {"confirm": True}, "127.0.0.1")
    finally:
        dev.close()


# ---- monitoring is read-only ----------------------------------------------------

def test_monitoring_never_writes_cfg_sim(sim):
    c, devices, _ = sim
    time.sleep(0.3)                                  # ~15 polls
    for url in ["/api/devices", "/api/devices/mot", "/api/whoami",
                "/api/devices/mot/history?span=10", "/api/devices/mot/info",
                "/api/stream?max_events=2", "/"]:
        assert c.get(url).status_code == 200, url
    assert c.get("/api/devices/mot/capture").status_code == 404
    assert devices["mot"].board.cfg_writes == []


def test_monitoring_never_writes_cfg_real_board(mock_server, monkeypatch):
    writes = []
    orig = Board.write_words
    monkeypatch.setattr(Board, "write_words",
                        lambda self, a, w: (writes.append(a), orig(self, a, w)))
    dev = CoilServoChannel(
        "mot", table={}, profile=load_channel("mot"), profile_error=None,
        poll_interval=0.01, cfg_refresh_s=0.05,
        connect=lambda: Board("127.0.0.1", port=mock_server))
    dev.start()
    try:
        wait_for(lambda: dev._n_polls >= 20)
        dev.snapshot(), dev.info(), dev.history(60)
    finally:
        dev.close()
    assert writes == []


# ---- logging -----------------------------------------------------------------

def test_status_and_action_logs(sim):
    c, devices, tmp = sim
    time.sleep(0.2)
    act(c, "apply_profile", confirm=True)
    act(c, "enable", source="in2", confirm=True)
    act(c, "stop")
    devices["mot"].status_log.flush()
    status = list(tmp.glob("status/mot_*.csv"))
    assert len(status) == 1
    rows = list(csv.reader(open(status[0])))
    assert rows[0] == STATUS_COLUMNS
    assert len(rows) > 5
    assert rows[-1][STATUS_COLUMNS.index("state")] in ("IDLE", "RUN")
    float(rows[-1][STATUS_COLUMNS.index("i_amps")])
    actions = list(csv.reader(open(next(tmp.glob("actions_*.csv")))))
    mine = [r for r in actions[1:] if r[3] == "127.0.0.1"]
    assert [r[4] for r in mine] == ["apply_profile", "enable", "stop"]
    assert json.loads(mine[1][5]) == {"source": "in2"}
    assert all(r[6].startswith("ok") for r in mine)


# ---- capture -----------------------------------------------------------------

def test_capture_endpoint_sim(sim):
    c, devices, tmp = sim
    enable_and_arm(c)
    act(c, "setpoint", amps=30, confirm=True)
    wait_for(lambda: abs(state(c)["i_amps"] - 30) < 0.2)
    r = act(c, "capture")
    assert r.status_code == 200, r.text
    cap = c.get("/api/devices/mot/capture").json()
    assert cap["n"] == 16384 and len(cap["i_amps"]) == 16384
    assert cap["dt_s"] == pytest.approx(1.024e-6)
    assert sum(cap["i_amps"]) / cap["n"] == pytest.approx(30, abs=0.3)
    assert sum(cap["sp_amps"]) / cap["n"] == pytest.approx(30, abs=0.3)
    saved = Path(cap["file"])
    assert saved.parent == tmp / "captures" and saved.exists()
    assert state(c)["capture_seq"] == cap["seq"]
    # the capture wrote only the two capture bits (word 0), never the loop
    assert act(c, "auto_capture", interval_s=0.5).status_code == 400


def test_capture_via_real_board(mock_server, tmp_path):
    with Board("127.0.0.1", port=mock_server) as b:
        b.write_words(STS_BASE + 20, [16384])            # FIFO full
        b.write_words(FIFO_ADDR, [(0xFFF6 << 16) | 200])  # e=-10, i=200
    dev = CoilServoChannel(
        "mot", table={}, profile=load_channel("mot"), profile_error=None,
        poll_interval=0.02, logdir=tmp_path,
        connect=lambda: Board("127.0.0.1", port=mock_server))
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("state"))
        dev.perform("capture", {}, "127.0.0.1")
        cap = dev.latest_capture()
        lsb = 200.0 / 16384
        assert cap["i_amps"][0] == pytest.approx(200 * lsb, abs=1e-4)
        assert cap["sp_amps"][0] == pytest.approx(190 * lsb, abs=1e-4)
        assert Path(cap["file"]).exists()
    finally:
        dev.close()


def test_step_job_in_simulation(sim):
    c, devices, _ = sim
    r = act(c, "step", amps=20, confirm=True)
    assert r.status_code == 200, r.text
    wait_for(lambda: (state(c)["job"] or {}).get("phase", "")
             .startswith("waiting"))
    act(c, "sim_arm", level=True)
    wait_for(lambda: state(c)["job"] is None, timeout=10)
    cap = c.get("/api/devices/mot/capture").json()
    assert cap["kind"] == "step" and cap["meta"]["amps"] == 20
    assert max(cap["i_amps"]) == pytest.approx(20, abs=1.0)
    assert cap["i_amps"][0] == pytest.approx(0, abs=0.2)
    wait_for(lambda: state(c)["state"] == "IDLE")


def test_stream_sends_all_devices(sim):
    c, _, _ = sim
    with c.stream("GET", "/api/stream?max_events=2") as r:
        lines = [ln for ln in r.iter_lines() if ln.startswith("data:")]
    assert len(lines) == 2
    assert "mot" in json.loads(lines[0][5:])


# ---- channels: offline, bench, unknown keys -------------------------------------

def test_offline_channel_does_not_block(tmp_path):
    def unreachable():
        raise ConnectionError("no register server answering")
    dev = CoilServoChannel(
        "z_shim", table={"board_host": "rp-zshim.local"},
        profile=load_channel("z_shim"), profile_error=None,
        connect=unreachable, reconnect_interval=0.05)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot()["connection"] == "offline")
        assert "offline" in dev.control_block()
        with pytest.raises(Exception, match="offline"):
            dev.perform("stop", {}, "127.0.0.1")
    finally:
        dev.close()


def test_channel_list_includes_the_bench_profile():
    devices = build_channels(simulate=["*"], logdir=None)
    assert "mot" in devices and "mot_bench" in devices
    bench = devices["mot_bench"].snapshot()
    assert bench["bench"] is True and bench["i_supply_max"] == 3.0
    assert devices["mot"].snapshot()["bench"] is False
    assert devices["mot"].snapshot()["i_supply_max"] is None
    rows = dict(devices["mot_bench"].info()["summary"])
    assert "supply limit" in rows


def test_unknown_keys_are_tolerated(tmp_path):
    text = CONFIG_PATH.read_text()
    mot = text[text.index("[mot]"):text.index("[mot_bench]")]
    cfg = tmp_path / "channels.toml"
    cfg.write_text(mot.replace("[mot]", "[mot]\nsome_future_key = 1"))
    devices = build_channels(config_path=cfg, simulate=["*"], logdir=None)
    assert list(devices) == ["mot"] and devices["mot"].profile is not None


def test_bench_profile_match_and_led(tmp_path):
    """Apply profile writes the bench LED pattern; the board's profile is
    identified, and a board holding other settings is flagged loudly."""
    log = ActionLog(tmp_path)
    devices = build_channels(["mot", "mot_bench"], simulate=["*"],
                             logdir=tmp_path, action_log=log,
                             poll_interval=0.02, cfg_refresh_s=0.05)
    app = create_app(devices, AccessPolicy(), log)
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        def st(name):
            return c.get(f"/api/devices/{name}").json()
        wait_for(lambda: st("mot_bench").get("profile_ok") is not None)
        s = st("mot_bench")
        assert s["profile_ok"] is False and "UNKNOWN" in s["profile_text"]
        assert any("PROFILE MISMATCH" in a for a in s["alarms"])
        r = c.post("/api/devices/mot_bench/actions/apply_profile",
                   json={"confirm": True}, headers=HDR)
        assert r.status_code == 200, r.text
        assert devices["mot_bench"].board.read_cfg()["led"] == 0x80
        s = st("mot_bench")
        assert s["profile_ok"] is True
        assert not any("PROFILE" in a for a in s["alarms"])
        # the simulated mot board holds the bench settings: wrong channel
        devices["mot"].board.apply_config(devices["mot_bench"].profile["cfg"])
        wait_for(lambda: "mot_bench" in (st("mot").get("profile_text") or ""))
        assert st("mot")["profile_ok"] is False
        r = c.post("/api/devices/mot/actions/enable",
                   json={"confirm": True}, headers=HDR)
        assert r.status_code == 409 and "differs" in r.json()["error"]


def test_step_by_counts_in_simulation(sim):
    c, devices, _ = sim
    r = act(c, "step", counts=3600, open_loop=False, confirm=True)
    assert r.status_code == 400 and "open-loop" in r.json()["error"]
    r = act(c, "step", counts=99999, open_loop=True, confirm=True)
    assert r.status_code == 400 and "clamp" in r.json()["error"]


def test_refused_profile_is_shown_not_fatal(tmp_path):
    text = CONFIG_PATH.read_text()
    mot = text[text.index("[mot]"):text.index("[mot_bench]")]
    cfg = tmp_path / "channels.toml"
    cfg.write_text(mot.replace("V_rail = 5.0", "V_rail = 9.0", 1))
    devices = build_channels(config_path=cfg, simulate=["*"], logdir=None)
    dev = devices["mot"]
    assert dev.profile is None and "V_rail" in dev.profile_error
    assert "profile error" in dev.control_block()
