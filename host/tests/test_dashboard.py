"""Dashboard backend: polling, units, access control, map-revision refusal,
read-only monitoring, one device per board with several profiles, the
cards, the sequence lock, the latch guess, logging and capture -- against
the model-driven SimBoard and against the real Board talking to the mock
register server. No browser involved."""

import csv
import json
import os
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
from coil_servo_host.dashboard import build_dashboard, load_cards
from coil_servo_host.dashboard.access import AccessPolicy
from coil_servo_host.dashboard.app import create_app
from coil_servo_host.dashboard.logs import (STATUS_COLUMNS, ActionLog,
                                            SegmentCsv, prune_auto_captures)
from coil_servo_host.dashboard.servo_channel import CoilServoChannel
from coil_servo_model.registers import MAP_REV, STS_BASE

SERVER = Path(__file__).resolve().parents[1] / "board" / "coil_servo_server.py"
LOCAL = ("127.0.0.1", 50000)
REMOTE = ("192.168.0.20", 50000)
HDR = {"X-Coil-Servo": "1"}
MOT = {"profile": "mot"}


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


def _sim_config_text() -> str:
    """channels.toml with the measured IN1/IN2 zero offsets set to 0: the
    simulated boards have no sensor offset unless a test adds one
    (sim_sensor_offset), so the real board's calibration must not apply."""
    import re
    text = CONFIG_PATH.read_text(encoding="utf-8")
    return re.sub(r"(?m)^(in[12]_offset_amps) = [^#\n]*", r"\1 = 0.0 ", text)


def make_app(tmp_path, *, only=("mot",), sim_old=(), policy=None, **kw):
    log = ActionLog(tmp_path)
    kw.setdefault("cfg_refresh_s", 0.05)
    if "config_path" not in kw:
        cfg = tmp_path / "channels_sim.toml"
        cfg.write_text(_sim_config_text(), encoding="utf-8")
        kw["config_path"] = cfg
    devices, cards = build_dashboard(list(only) if only else None,
                                     simulate=["*"], sim_old=sim_old,
                                     logdir=tmp_path, action_log=log,
                                     poll_interval=0.02, **kw)
    return create_app(devices, policy or AccessPolicy(), log,
                      cards=cards), devices


@pytest.fixture
def sim(tmp_path):
    """(local client, devices, tmp_path), the simulated MOT board online."""
    app, devices = make_app(tmp_path)
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: c.get("/api/devices/mot").json().get("state"))
        yield c, devices, tmp_path


def act(c, action, **params):
    return c.post(f"/api/devices/mot/actions/{action}", json=params,
                  headers=HDR)


def state(c):
    return c.get("/api/devices/mot").json()


def enable_and_arm(c, profile="mot"):
    assert act(c, "apply_profile", profile=profile,
               confirm=True).status_code == 200
    assert act(c, "enable", profile=profile, confirm=True).status_code == 200
    assert act(c, "sim_arm", level=True).status_code == 200
    wait_for(lambda: state(c)["state"] == "RUN")


def mock_channel(port, **kw):
    """A device on the real Board client talking to the mock server."""
    kw.setdefault("poll_interval", 0.02)
    return CoilServoChannel(
        "mot", host="127.0.0.1", profiles={"mot": load_channel("mot")},
        connect=lambda: Board("127.0.0.1", port=port), **kw)


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
                                                dict(MOT, confirm=True))]:
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
    r = act(c, "enable", **MOT)                              # no confirm
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
        # RUN, armed, bridge on; i_meas = 2048 codes x 128 (= I_FS / 4,
        # 50 A at the nominal 200 A); sp_active = -1024 counts (-I_FS / 8);
        # u14 = 4096 counts
        b.write_words(STS_BASE, [(MAP_REV << 24) | (1 << 7) | (1 << 5) | 1,
                                 2048 * 128, (-1024) & 0x3FFF, 4096])
    dev = mock_channel(mock_server, logdir=tmp_path)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("state") == "RUN")
        s = dev.snapshot()
        assert s["connection"] == "online" and s["map_ok"]
        assert s["i_amps"] == pytest.approx(ch["i_fs"] / 4)
        assert s["sp_amps"] == pytest.approx(-ch["i_fs"] / 8)
        assert s["u14"] == 4096
        assert s["out1_volts"] == pytest.approx(
            0.5 * ch["model"].out1_factor + ch["model"].out1_offset_volts, abs=1e-5)
        assert s["pi_volts"] == s["out1_volts"] and s["out1_mode"] == "closed"
        assert s["flags"]["armed"] and s["flags"]["bridge_en"]
        # the mock board was never configured: amber, not red
        assert s["alarms"] == []
        assert any("match no profile" in w for w in s["warnings"])
        h = dev.history(60)
        assert len(h["t"]) >= 1 and h["i_amps"][-1] == pytest.approx(
            ch["i_fs"] / 4)
    finally:
        dev.close()


def test_simulated_run_through_the_api(sim):
    c, devices, _ = sim
    assert state(c)["state"] == "IDLE"
    r = act(c, "enable", source="register", confirm=True, **MOT)
    assert r.status_code == 409 and "apply profile" in r.json()["error"]
    assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
    r = act(c, "enable", source="register", confirm=True)   # held profile
    assert r.status_code == 200, r.text
    s = state(c)
    assert s["pending"] and "arm" in s["pending"]["message"]
    assert s["pending"]["profile"] == "mot"
    assert act(c, "sim_arm", level=True).status_code == 200
    wait_for(lambda: state(c)["state"] == "RUN")
    wait_for(lambda: state(c)["pending"] is None)
    assert act(c, "setpoint", amps=40, confirm=True, **MOT).status_code == 200
    wait_for(lambda: abs(state(c)["i_amps"] - 40) < 0.2)
    s = state(c)
    assert s["sp_amps"] == pytest.approx(40, abs=0.05)
    mot_model = devices["mot"].models["mot"]
    assert s["pi_volts"] == pytest.approx(
        s["u14"] / 8192 * mot_model.out1_factor + mot_model.out1_offset_volts,
        abs=1e-4)
    assert s["out1_mode"] == "closed" and s["out1_counts"] == s["u14"]
    r = act(c, "setpoint", amps=100.5, confirm=True, **MOT)  # above rated
    assert r.status_code == 400 and "rated" in r.json()["error"]
    assert act(c, "stop").status_code == 200
    wait_for(lambda: state(c)["state"] == "IDLE")
    s = state(c)                     # bridge open: OUT1 is 0 whatever u14 is
    # DAC code 0; the volts show the measured resting offset (-41 mV)
    assert s["out1_mode"] == "off" and s["out1_counts"] == 0
    assert s["out1_volts"] == pytest.approx(mot_model.out1_offset_volts, abs=1e-5)
    assert set(c.get("/api/devices/mot/history?span=60").json()) >= {
        "out1_volts", "pi_volts"}


def test_out1_actual_is_not_the_pi_output():
    """OUT1 per coil_servo_top.v / servo_output_mux.v, not STS u14."""
    from coil_servo_host.dashboard.servo_channel import out1_actual
    run = {"bridge_en": 1, "fsm_state_name": "RUN", "u14": 2263,
           "sp_active": 5000}
    cl = {"open_loop": 0, "out_clamp": 4000, "deadband": 10}
    ol = dict(cl, open_loop=1)
    # IDLE: integrator held at its last value, outputs gated off
    assert out1_actual(dict(run, bridge_en=0, fsm_state_name="IDLE"),
                       cl) == (0, "off")
    for st in ("TRIP", "TRIPPED"):          # out_kill, bridge still closed
        assert out1_actual(dict(run, fsm_state_name=st), cl) == (0, "off")
    assert out1_actual(run, cl) == (2263, "closed")
    assert out1_actual(run, ol) == (4000, "open")       # clamped setpoint
    assert out1_actual(dict(run, sp_active=300), ol) == (300, "open")
    assert out1_actual(dict(run, u14=8), cl) == (0, "closed")  # deadband
    assert out1_actual(dict(run, u14=-50), cl) == (0, "closed")
    assert out1_actual(run, None) == (2263, "closed")   # no read-back yet


def test_enable_without_arm_edge_times_out_and_disables(tmp_path):
    app, devices = make_app(tmp_path, enable_timeout=0.2)
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("state"))
        assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
        assert act(c, "enable", confirm=True, **MOT).status_code == 200
        wait_for(lambda: state(c)["pending"] is None)
        assert "did not reach RUN" in state(c)["notice"]["text"]
        assert devices["mot"].board.read_cfg()["servo_enable"] == 0


def test_trip_and_acknowledge(sim):
    c, devices, _ = sim
    enable_and_arm(c)
    act(c, "setpoint", amps=20, confirm=True, **MOT)
    act(c, "sim_trip")
    wait_for(lambda: state(c)["state"] == "TRIPPED")
    assert any("TRIPPED" in a for a in state(c)["alarms"])
    assert act(c, "stop").status_code == 200
    assert act(c, "enable", confirm=True, **MOT).status_code == 409  # !IDLE
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
        assert "old bitstream" in s["stop_block"]
        for action, params in [("enable", dict(MOT, confirm=True)),
                               ("stop", {}), ("capture", {}),
                               ("setpoint", dict(MOT, amps=1, confirm=True))]:
            r = act(c, action, **params)
            assert r.status_code == 409, action
            assert "revision 0" in r.json()["error"]
        assert devices["mot"].board.cfg_writes == []


def test_old_bitstream_on_the_mock_server(mock_server, tmp_path):
    with Board("127.0.0.1", port=mock_server) as b:
        b.write_words(STS_BASE, [0])                 # map_rev 0
    dev = mock_channel(mock_server)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("map_ok") is False)
        with pytest.raises(Exception, match="revision 0"):
            dev.perform("enable", dict(MOT, confirm=True), "127.0.0.1")
    finally:
        dev.close()


# ---- monitoring is read-only ----------------------------------------------------

def test_monitoring_never_writes_cfg_sim(sim):
    c, devices, _ = sim
    time.sleep(0.3)                                  # ~15 polls
    for url in ["/api/devices", "/api/devices/mot", "/api/whoami",
                "/api/cards", "/api/devices/mot/history?span=10",
                "/api/devices/mot/info", "/api/devices/mot/info?profile="
                "mot_bench", "/api/stream?max_events=2", "/"]:
        assert c.get(url).status_code == 200, url
    assert c.get("/api/devices/mot/capture").status_code == 404
    assert c.get("/api/stream?max_events=x").status_code == 400
    assert devices["mot"].board.cfg_writes == []


def test_monitoring_never_writes_cfg_real_board(mock_server, monkeypatch):
    writes = []
    orig = Board.write_words
    monkeypatch.setattr(Board, "write_words",
                        lambda self, a, w: (writes.append(a), orig(self, a, w)))
    dev = mock_channel(mock_server, poll_interval=0.01, cfg_refresh_s=0.05)
    dev.start()
    try:
        wait_for(lambda: dev._n_polls >= 20)
        dev.snapshot(), dev.info(), dev.history(60)
    finally:
        dev.close()
    assert writes == []


# ---- robustness: stop, stale, poll errors ------------------------------------

def test_stop_works_when_a_profile_is_refused(tmp_path):
    """A channels.toml profile the loader refuses blocks control as that
    profile -- but never Stop."""
    text = _sim_config_text()
    mot = text[text.index("[mot]"):text.index("[mot_bench]")]
    cfg = tmp_path / "channels.toml"
    cfg.write_text(mot.replace("V_rail = 5.0", "V_rail = 9.0", 1))
    devices, cards = build_dashboard(config_path=cfg, cards_path=tmp_path /
                                     "none.toml", simulate=["*"],
                                     logdir=None, poll_interval=0.02)
    dev = devices["mot"]
    assert dev.profiles == {} and "V_rail" in dev.profile_errors["mot"]
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("state"))
        assert "no usable profile" in dev.control_block()
        assert dev.stop_block() is None
        assert dev.perform("stop", {}, "127.0.0.1")["ok"]
        with pytest.raises(Exception, match="no usable profile"):
            dev.perform("enable", dict(MOT, confirm=True), "127.0.0.1")
        s = dev.snapshot()
        assert s["alarms"] == [] and any("refused" in w
                                         for w in s["warnings"])
    finally:
        dev.close()


def test_poll_error_is_reported_not_frozen(mock_server):
    """An unexpected exception in the poll loop takes the board offline with
    a message and reconnects -- the page never shows frozen values as live."""
    dev = mock_channel(mock_server, reconnect_interval=0.05)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("connection") == "online")
        orig = dev.board.sts
        boom = {"n": 0}

        def bad():
            boom["n"] += 1
            raise KeyError("malformed reply")
        dev.board.sts = bad
        wait_for(lambda: "poll error" in (dev.snapshot()["error"] or ""))
        assert dev.snapshot()["connection"] == "offline"
        wait_for(lambda: dev.snapshot()["connection"] == "online")  # new Board
        assert boom["n"] >= 1 and orig
    finally:
        dev.close()


def test_stale_status_is_flagged(mock_server):
    dev = mock_channel(mock_server)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("state"))
        assert dev.snapshot()["stale"] is False
        dev._stop.set()                              # polling stops
        dev._thread.join(timeout=5)
        dev.m_sts -= 5.0
        assert dev.snapshot()["stale"] is True
        assert dev.snapshot()["age_s"] >= 5.0
    finally:
        dev.close()


# ---- sequence lock and the latch guess -----------------------------------------

def test_sequence_lock_while_the_control_computer_owns_the_coil(sim):
    c, devices, _ = sim
    assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
    assert act(c, "enable", source="in2", confirm=True, **MOT).status_code \
        == 200
    s = state(c)
    assert "control computer" in s["sequence_lock"]
    for action, params in [("setpoint", dict(MOT, amps=1, confirm=True)),
                           ("apply_profile", dict(MOT, confirm=True)),
                           ("trip_ack", {"confirm": True}),
                           ("step", dict(MOT, amps=1, confirm=True))]:
        r = act(c, action, **params)
        assert r.status_code == 409 and "look only" in r.json()["error"], \
            action
    assert act(c, "auto_capture", interval_s=0).status_code == 200
    assert act(c, "stop").status_code == 200
    assert state(c)["sequence_lock"] is None


@pytest.mark.parametrize("amps,fires", [(20.0, True), (0.0, False)])
def test_latch_guess(tmp_path, amps, fires):
    """Current ~0 A while RUN, bridge enabled and the setpoint > 5 % of
    rated for > 0.2 s: amber guess. A hold at 0 A must not fire."""
    app, devices = make_app(tmp_path)
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("state"))
        enable_and_arm(c)
        if amps:
            act(c, "setpoint", amps=amps, confirm=True, **MOT)
            wait_for(lambda: abs(state(c)["i_amps"] - amps) < 0.5)
        assert not any("latch" in w for w in state(c)["warnings"])
        assert act(c, "sim_power", on=False).status_code == 200
        time.sleep(0.6)
        s = state(c)
        guess = [w for w in s["warnings"] if "latch" in w]
        assert bool(guess) is fires
        assert not any("latch" in a for a in s["alarms"])     # never red
        if fires:
            assert "supply is off" in guess[0] and "open" in guess[0]
            act(c, "sim_power", on=True)
            wait_for(lambda: not any("latch" in w
                                     for w in state(c)["warnings"]))


# ---- one board, several profiles, cards ------------------------------------------

def test_one_device_per_board_and_cards():
    devices, cards = build_dashboard(simulate=["*"], logdir=None)
    assert list(devices) == ["mot", "z_shim", "x_shim", "y_shim"]
    by_id = {c.id: c for c in cards}
    assert [c.title for c in cards] == ["MOT", "Z shim", "X shim", "Y shim",
                                        "Test setup"]
    assert by_id["mot"].device == by_id["test"].device == "mot"
    assert by_id["test"].profile == "mot_bench"
    assert by_id["test"].style == "bench" and by_id["mot"].style == ""
    assert devices["mot"].order == ["mot", "mot_bench"]
    snap = devices["mot"].snapshot()
    bench = [p for p in snap["profiles"] if p["name"] == "mot_bench"][0]
    assert bench["bench"] is True and bench["i_supply_max"] == 3.0
    assert bench["i_rated"] == 2.5
    rows = dict(devices["mot"].info("mot_bench")["summary"])
    assert "supply limit" in rows
    # the measured gate-stage calibration, read-only (Test 1, 2026-11-11)
    assert "1.168 V" in rows["measured: OUT1 full scale"]
    assert "-41.3 mV" in rows["measured: OUT1 offset"]
    assert "5.18 V" in rows["measured: gate ceiling"]


def test_cards_file_is_checked(tmp_path):
    f = tmp_path / "dashboard.toml"
    f.write_text('[[card]]\nid = "a"\nprofile = "nope"\n')
    with pytest.raises(ValueError, match="nope"):
        load_cards(f)
    f.write_text('[[card]]\nid = "a"\nprofile = "mot"\nstyle = "pink"\n')
    with pytest.raises(ValueError, match="style"):
        load_cards(f)
    # no file: one card per channels.toml table
    specs = load_cards(tmp_path / "missing.toml")
    assert ("mot_bench", "mot_bench", "mot_bench", "bench") in specs


def test_shared_board_profile_switch(tmp_path):
    """MOT and Test setup are views of one board: applying mot_bench makes
    the board hold it, the scale/rating follow, the simulated physics switch
    to the bench supply, and acting as [mot] is refused -- with no red."""
    app, devices = make_app(tmp_path, only=None)
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("held") is not None
                 and state(c).get("state"))
        assert act(c, "apply_profile", confirm=True,
                   profile="mot_bench").status_code == 200
        dev = devices["mot"]
        assert dev.board.read_cfg()["led"] == 0x80
        s = state(c)
        assert s["held"] == ["mot_bench"] and s["i_rated"] == 2.5
        assert s["i_supply_max"] == 3.0 and s["alarms"] == []
        assert dev.board.m.I_supply_max == 3.0           # sim follows
        r = act(c, "enable", confirm=True, **MOT)
        assert r.status_code == 409 and "holds mot_bench" in r.json()["error"]
        r = act(c, "apply_profile", confirm=True)        # which one?
        assert r.status_code == 400
        enable_and_arm(c, "mot_bench")
        r = act(c, "setpoint", amps=3.0, confirm=True, profile="mot_bench")
        assert r.status_code == 400 and "rated" in r.json()["error"]
        assert act(c, "setpoint", amps=2.0, confirm=True,
                   profile="mot_bench").status_code == 200
        wait_for(lambda: abs(state(c)["i_amps"] - 2.0) < 0.1)
        assert act(c, "stop").status_code == 200
        wait_for(lambda: state(c)["state"] == "IDLE")
        assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
        assert state(c)["held"] == ["mot"] and dev.board.m.I_supply_max is None


def test_unknown_keys_are_tolerated(tmp_path):
    text = _sim_config_text()
    mot = text[text.index("[mot]"):text.index("[mot_bench]")]
    cfg = tmp_path / "channels.toml"
    cfg.write_text(mot.replace("[mot]", "[mot]\nsome_future_key = 1"))
    devices, cards = build_dashboard(config_path=cfg, cards_path=tmp_path /
                                     "none.toml", simulate=["*"], logdir=None)
    assert list(devices) == ["mot"] and "mot" in devices["mot"].profiles


def test_offline_channel_does_not_block(tmp_path):
    def unreachable():
        raise ConnectionError("no register server answering")
    dev = CoilServoChannel(
        "z_shim", host="rp-zshim.local",
        profiles={"z_shim": load_channel("z_shim")},
        connect=unreachable, reconnect_interval=0.05)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot()["connection"] == "offline")
        assert "offline" in dev.control_block()
        with pytest.raises(Exception, match="offline"):
            dev.perform("stop", {}, "127.0.0.1")
    finally:
        dev.close()


# ---- logging -----------------------------------------------------------------

def test_status_and_action_logs(sim):
    c, devices, tmp = sim
    time.sleep(0.2)
    act(c, "apply_profile", confirm=True, **MOT)
    act(c, "enable", source="in2", confirm=True, **MOT)
    act(c, "stop")
    time.sleep(0.15)                             # a few polls after that
    devices["mot"].status_log.flush()
    status = list(tmp.glob("status/mot_*.csv"))
    assert len(status) == 1
    rows = list(csv.reader(open(status[0])))
    assert rows[0] == STATUS_COLUMNS
    assert len(rows) > 5
    assert rows[-1][STATUS_COLUMNS.index("state")] in ("IDLE", "RUN",
                                                       "RAMP_DOWN")
    assert rows[-1][STATUS_COLUMNS.index("profile")] == "mot"
    float(rows[-1][STATUS_COLUMNS.index("i_amps")])
    actions = list(csv.reader(open(next(tmp.glob("actions_*.csv")))))
    mine = [r for r in actions[1:] if r[3] == "127.0.0.1"]
    assert [r[4] for r in mine] == ["apply_profile", "enable", "stop"]
    assert json.loads(mine[1][5]) == {"profile": "mot", "source": "in2"}
    assert all(r[6].startswith("ok") for r in mine)


def test_status_segments_keep_one_hour(tmp_path):
    seg = SegmentCsv(tmp_path, "mot_", ["a"], segment_s=600, keep_s=3600)
    now = time.time()
    seg.write(now - 7200, [1])                   # two hours ago
    seg.write(now - 3000, [2])                   # 50 min ago
    seg.write(now, [3])                          # now: prunes
    seg.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 2, names                # the 2 h old one is gone
    assert seg.path_for(now).name in names
    other = tmp_path / "mot_bench_20000101-0000.csv"   # not this prefix
    other.write_text("x")
    seg.prune(now)
    assert other.exists()


def test_auto_captures_pruned_manual_kept(tmp_path):
    d = tmp_path / "captures"
    d.mkdir()
    old = time.time() - 2 * 3600
    for name in ["mot_x_auto.csv", "mot_x_capture.csv", "mot_x_step.csv",
                 "mot_y_auto.csv"]:
        (d / name).write_text("x")
    for name in ["mot_x_auto.csv", "mot_x_capture.csv", "mot_x_step.csv"]:
        os.utime(d / name, (old, old))
    gone = prune_auto_captures(tmp_path)
    assert [p.name for p in gone] == ["mot_x_auto.csv"]
    assert sorted(p.name for p in d.iterdir()) == [
        "mot_x_capture.csv", "mot_x_step.csv", "mot_y_auto.csv"]


# ---- capture -----------------------------------------------------------------

def test_capture_endpoint_sim(sim):
    c, devices, tmp = sim
    enable_and_arm(c)
    act(c, "setpoint", amps=30, confirm=True, **MOT)
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
    assert saved.name.endswith("_capture.csv")
    assert state(c)["capture_seq"] == cap["seq"]
    assert act(c, "auto_capture", interval_s=0.5).status_code == 400


def test_repeating_capture_is_kind_auto(sim):
    c, devices, tmp = sim
    enable_and_arm(c)
    assert act(c, "auto_capture", interval_s=1).status_code == 200
    assert state(c)["auto_capture_s"] == 1
    wait_for(lambda: list((tmp / "captures").glob("*_auto.csv")))
    act(c, "auto_capture", interval_s=0)


def test_capture_via_real_board(mock_server, tmp_path):
    with Board("127.0.0.1", port=mock_server) as b:
        b.write_words(STS_BASE, [MAP_REV << 24])
        b.write_words(STS_BASE + 20, [16384])            # FIFO full
        b.write_words(FIFO_ADDR, [(0xFFF6 << 16) | 200])  # e=-10, i=200
    dev = mock_channel(mock_server, logdir=tmp_path)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("state"))
        dev.perform("capture", {}, "127.0.0.1")
        cap = dev.latest_capture()
        lsb = load_channel("mot")["i_fs"] / 16384
        assert cap["i_amps"][0] == pytest.approx(200 * lsb, abs=1e-4)
        assert cap["sp_amps"][0] == pytest.approx(190 * lsb, abs=1e-4)
        assert Path(cap["file"]).exists()
    finally:
        dev.close()


def test_step_job_in_simulation(sim):
    c, devices, _ = sim
    r = act(c, "step", amps=20, confirm=True, **MOT)
    assert r.status_code == 409 and "apply profile" in r.json()["error"]
    assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
    r = act(c, "step", amps=20, confirm=True, **MOT)
    assert r.status_code == 200, r.text
    wait_for(lambda: (state(c)["job"] or {}).get("phase", "")
             .startswith("waiting"))
    act(c, "sim_arm", level=True)
    wait_for(lambda: state(c)["job"] is None, timeout=10)
    cap = c.get("/api/devices/mot/capture").json()
    assert cap["kind"] == "step" and cap["meta"]["amps"] == 20
    assert cap["meta"]["profile"] == "mot"
    assert max(cap["i_amps"]) == pytest.approx(20, abs=1.0)
    assert cap["i_amps"][0] == pytest.approx(0, abs=0.2)
    wait_for(lambda: state(c)["state"] == "IDLE")


def _run_open_loop(c, dev):
    """Board RUNning in open loop (as a sweep, or another tool, leaves it)."""
    assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
    with dev._io:
        dev.board.sync_shadow()
        dev.board.write_cfg(sp_source=1, setpoint=0, open_loop=1,
                            servo_enable=1)
    assert act(c, "sim_arm", level=True).status_code == 200
    wait_for(lambda: state(c)["state"] == "RUN")


def test_stop_from_open_loop_leaves_it_once_idle(sim):
    """Board.write_cfg defers open_loop 1 -> 0 until IDLE (PI wind-up);
    STOP still works and the poll loop finishes the job afterwards."""
    c, devices, _ = sim
    dev = devices["mot"]
    _run_open_loop(c, dev)
    assert act(c, "stop").status_code == 200
    wait_for(lambda: state(c)["state"] == "IDLE")
    wait_for(lambda: dev.board.read_cfg()["open_loop"] == 0)
    assert dev.board.read_cfg()["servo_enable"] == 0
    assert dev.board.read_cfg()["int_clear"] == 0       # pulsed, not stuck


def test_closed_loop_while_running_open_loop_says_stop_first(sim,
                                                             monkeypatch):
    c, devices, _ = sim
    dev = devices["mot"]
    _run_open_loop(c, dev)
    # enable is normally refused outside IDLE already; take that check away
    # to reach the board client's own guard
    monkeypatch.setattr(dev, "_need_idle", lambda what: None)
    r = act(c, "enable", confirm=True, **MOT)
    assert r.status_code == 409
    assert "press STOP and wait for IDLE" in r.json()["error"]
    rb = dev.board.read_cfg()
    assert rb["open_loop"] == 1 and state(c)["state"] == "RUN"
    assert act(c, "stop").status_code == 200
    wait_for(lambda: dev.board.read_cfg()["open_loop"] == 0)


def test_step_by_counts_in_simulation(sim):
    c, devices, _ = sim
    assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
    r = act(c, "step", counts=3600, open_loop=False, confirm=True, **MOT)
    assert r.status_code == 400 and "open-loop" in r.json()["error"]
    r = act(c, "step", counts=99999, open_loop=True, confirm=True, **MOT)
    assert r.status_code == 400 and "clamp" in r.json()["error"]


def test_stream_sends_all_devices(sim):
    c, _, _ = sim
    with c.stream("GET", "/api/stream?max_events=2") as r:
        lines = [ln for ln in r.iter_lines() if ln.startswith("data:")]
    assert len(lines) == 2
    assert "mot" in json.loads(lines[0][5:])


# ---- start-up helpers ----------------------------------------------------------

# ---- firmware identity and zero offsets (register map rev 4) ------------------

def test_firmware_identity_shown(mock_server):
    with Board("127.0.0.1", port=mock_server) as b:
        b.write_words(STS_BASE, [MAP_REV << 24])
        b.write_words(STS_BASE + 4 * 6, [0x10AA4ED2])   # dirty build
    dev = mock_channel(mock_server)
    dev.start()
    try:
        wait_for(lambda: dev.snapshot().get("build_text"))
        s = dev.snapshot()
        assert s["build_text"] == ("commit 0aa4ed2, built with uncommitted "
                                   "changes")
        assert s["map_ok"] and s["build_id"] == 0x10AA4ED2
    finally:
        dev.close()


def _zero_app(tmp_path, offset):
    cfg = tmp_path / "channels.toml"
    cfg.write_text(_sim_config_text(), encoding="utf-8")
    return make_app(tmp_path, config_path=cfg, sim_sensor_offset=offset), cfg


def test_zero_calibration_measure_load_store(tmp_path):
    (app, devices), cfg = _zero_app(tmp_path, (-0.45, 0.03))
    dev = devices["mot"]
    lsb = load_channel("mot")["i_fs"] / 8192
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("state"))
        assert act(c, "apply_profile", confirm=True, **MOT).status_code == 200
        wait_for(lambda: any("IN1 not calibrated" in w
                             for w in state(c)["warnings"]))
        assert state(c)["i_amps"] == pytest.approx(-0.45, abs=0.05)
        # refused while the servo runs (and without a result, store/load)
        enable_and_arm(c)
        r = act(c, "zero_cal", confirm=True, **MOT)
        assert r.status_code == 409 and "IDLE" in r.json()["error"]
        assert act(c, "zero_store", confirm=True).status_code == 409
        assert act(c, "stop").status_code == 200
        wait_for(lambda: state(c)["state"] == "IDLE")
        assert act(c, "zero_cal", **MOT).status_code == 400   # confirm
        # measure: raw codes, nothing changed yet
        assert act(c, "zero_cal", confirm=True, **MOT).status_code == 200
        wait_for(lambda: state(c)["zero"] is not None, timeout=10)
        z = state(c)["zero"]
        assert z["in1_amps"] == pytest.approx(-0.45, abs=0.03)
        assert z["in2_amps"] == pytest.approx(0.03, abs=0.03)
        assert z["in1_offset"] == round(z["in1_counts"])
        assert "sensor needs attention" in z["warning"] and not z["refused"]
        assert dev.board.read_cfg()["in1_offset"] == 0
        # load: the board subtracts it, readings go to ~0
        assert act(c, "zero_load", confirm=True).status_code == 200
        assert dev.board.read_cfg()["in1_offset"] == z["in1_offset"]
        wait_for(lambda: abs(state(c)["i_amps"]) < 0.05)
        wait_for(lambda: any("differ from channels.toml" in w
                             for w in state(c)["warnings"]))
        # store: channels.toml round-trip, warnings gone
        assert act(c, "zero_store", confirm=True).status_code == 200
        ch = load_channel("mot", cfg)
        assert ch["cfg"]["in1_offset"] == round(z["in1_amps"] / lsb)
        assert ch["cfg"]["in1_offset"] == z["in1_offset"]
        assert load_channel("mot_bench", cfg)["cfg"]["in1_offset"] == 0
        assert "(coil_servo_host.zero)" in cfg.read_text(encoding="utf-8")
        s = state(c)
        assert s["zero"]["stored"] and s["zero"]["loaded"]
        off = next(p for p in s["profiles"] if p["name"] == "mot")["offsets"]
        assert off["in1"] == z["in1_offset"] == s["offsets_board"]["in1"]
        wait_for(lambda: not any("IN1 not calibrated" in w or "differ" in w
                                 for w in state(c)["warnings"]))
        log = tmp_path.glob("actions_*.csv")
        assert any("zero_store" in f.read_text() for f in log)


def test_zero_calibration_refuses_a_broken_sensor(tmp_path):
    (app, devices), cfg = _zero_app(tmp_path, (-25.0, 0.0))  # > 10 % of 200 A
    before = cfg.read_text(encoding="utf-8")
    with TestClient(app, client=LOCAL, base_url="http://localhost") as c:
        wait_for(lambda: state(c).get("state"))
        assert act(c, "zero_cal", confirm=True, **MOT).status_code == 200
        wait_for(lambda: state(c)["zero"] is not None, timeout=10)
        assert "10%" in state(c)["zero"]["refused"]
        for a in ("zero_store", "zero_load"):
            r = act(c, a, confirm=True)
            assert r.status_code == 409 and "full scale" in r.json()["error"]
        assert devices["mot"].board.read_cfg()["in1_offset"] == 0
    assert cfg.read_text(encoding="utf-8") == before


def test_lan_address_and_port_check():
    import socket

    from coil_servo_host.dashboard.__main__ import (DEFAULT_PORT,
                                                    lan_address, port_free)
    assert DEFAULT_PORT != 8080                  # KoheronControl's port
    # names are skipped (no DNS); the first numeric host gives the address
    assert lan_address(["rp-zshim.local", "127.0.0.1"]) == "127.0.0.1"
    assert lan_address(["rp-zshim.local", None]) is None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        assert not port_free("127.0.0.1", s.getsockname()[1])
