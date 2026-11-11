"""One coil-servo BOARD as a dashboard device.

A board can hold several channels.toml profiles (mot and mot_bench both
address the MOT board): the device owns the board, the profiles are the
settings it may be given. The overview's cards (cards.py) are views of a
device with one profile each.

A background thread per board owns ONE connection to the board's register
server and polls STS at a fixed rate (default 10 Hz); every browser viewer
is served from the latest poll and an in-memory history, never from its own
connection. Monitoring only reads: the poll loop reads STS, and every few
seconds the CFG block to tell which profile the board holds. CFG is written
only by an explicit action (control, stop, capture) -- never periodically,
except the opt-in repeating capture, which writes only the two capture bits
(capture_sel, fifo_rst) that do not touch the loop.

Long actions (step, sweep) run as a "job" in their own thread and reuse the
measurement steps of step.py and sweep.py. Board access from the poll loop,
actions and jobs is serialized by one lock (the register protocol is one
request at a time per connection).
"""

import csv
import json
import threading
import time
from collections import deque

from coil_servo_model.registers import CFG_FIELDS, MAP_REV

from .. import step as step_tool
from .. import sweep as sweep_tool
from ..capture import FS_DEC, decimated_columns, decode_words, write_csv
from ..config import (CONFIG_PATH, PROFILE_MATCH_KEYS, counts_to_amps,
                      i_meas_amps, out1_volts, summary_header, summary_rows)
from .device import ActionError, ActionSpec, Device
from .logs import StatusLog, capture_path, prune_auto_captures

FLAG_NAMES = ("armed", "bridge_en", "polarity", "tripped", "out_sat",
              "u_floor", "int_railed", "sp_sign_mismatch", "timeout_hold",
              "fault")
# CFG fields that change in normal operation (not part of the profile)
LIVE_FIELDS = ("servo_enable", "sp_source", "open_loop", "setpoint",
               "capture_sel", "int_clear", "fifo_rst", "trip_ack",
               "flip_fault_ack", "boost_manual")

# "possible latch trip" guess (amber): RUN, bridge enabled, an active
# setpoint above LATCH_SP_FRAC of rated, but the measured current below
# max(LATCH_I_FRAC of rated, LATCH_I_COUNTS ADC counts) for LATCH_HOLD_S
LATCH_SP_FRAC = 0.05
LATCH_I_FRAC = 0.01
LATCH_I_COUNTS = 2
LATCH_HOLD_S = 0.2

# actions still accepted while the control computer owns the coil
SEQUENCE_OK = ("stop", "capture", "auto_capture")

PROFILE_PARAM = {"profile": "channels.toml profile to act as (default: the "
                            "one the board holds)"}

ACTIONS = {a.name: a for a in [
    ActionSpec("stop", "Stop", "stop", False,
               "servo_enable = 0: the firmware ramps the current to zero "
               "at the configured rate and returns to IDLE. Always safe; "
               "also ends a running step or sweep."),
    ActionSpec("apply_profile", "Apply profile", "control", True,
               "Write one of this board's channels.toml profiles (gains, "
               "clamp, floor, trip, ramps, dither, board LEDs). Only in "
               "IDLE.", {"profile": "which profile (required)"}),
    ActionSpec("enable", "Enable", "control", True,
               "servo_enable = 1 with the chosen setpoint source; the servo "
               "then waits in IDLE for a rising edge on arm (DIO4). Refused "
               "unless the board holds the chosen profile.",
               dict(PROFILE_PARAM,
                    source="'register' (setpoint from this page, starts at "
                           "0 A) or 'in2' (analog setpoint from the control "
                           "computer)")),
    ActionSpec("setpoint", "Set current", "control", True,
               "Set the setpoint register (only with source = register). "
               "Refused above the profile's rated current.",
               dict(PROFILE_PARAM, amps="target current in amps")),
    ActionSpec("trip_ack", "Acknowledge trip", "control", True,
               "Pulse trip_ack: TRIPPED -> IDLE. Find the cause first. "
               "Running again needs enable and a fresh arm edge."),
    ActionSpec("flip_fault_ack", "Acknowledge flip timeout", "control", True,
               "Pulse flip_fault_ack: TIMEOUT_HOLD -> RUN."),
    ActionSpec("step", "Step response", "control", True,
               "As `python -m coil_servo_host.step`: apply the profile, "
               "enable (raise arm when asked), step the setpoint 2 ms into "
               "a 16.8 ms capture, ramp back to zero and disable. "
               "FIRST RUNS: DUMMY RESISTIVE LOAD ONLY.",
               dict(PROFILE_PARAM,
                    amps="step target in amps",
                    counts="instead of amps, open loop only: the OUT1 "
                           "command in DAC counts (bypasses the model)",
                    open_loop="true: drive OUT1 directly with the command "
                              "the model predicts for that current")),
    ActionSpec("sweep", "Swept-sine transfer function", "control", True,
               "As `python -m coil_servo_host.sweep`: open loop, IN2 drives "
               "OUT1. Needs a function generator on IN2 with its offset "
               "above the dead zone. Ends with Stop.",
               dict(PROFILE_PARAM,
                    min_stim="ignore stimulus below this many amps "
                             "(default 0.05)")),
    ActionSpec("capture", "Capture now", "capture", False,
               "Record 16.8 ms of measured current and setpoint "
               "(976.6 kS/s) and save it as CSV. Does not touch the loop."),
    ActionSpec("auto_capture", "Repeat capture", "capture", False,
               "Capture every N seconds (0 = off). These files are kept "
               "for one hour.",
               {"interval_s": "seconds between captures, >= 1, 0 = off"}),
]}

SIM_ACTIONS = {a.name: a for a in [
    ActionSpec("sim_arm", "Arm (DIO4)", "control", False,
               "SIMULATION ONLY: set the simulated arm line.",
               {"level": "true = high, false = low"}),
    ActionSpec("sim_trip", "Inject trip", "control", False,
               "SIMULATION ONLY: pretend the current crossed trip_level."),
    ActionSpec("sim_power", "Power", "control", False,
               "SIMULATION ONLY: take the drive away behind the board's "
               "back (latch tripped / supply off) or give it back.",
               {"on": "true = drive available, false = cut"}),
]}


def _f(x, nd=4):
    return round(float(x), nd)


class CoilServoChannel(Device):
    """One board. profiles: name -> load_channel() result, or None for a
    profile the loader refused (its message in profile_errors). models:
    name -> model Channel, for every profile whose physics could be built
    (used for the current scale even when the loader refused it)."""

    kind = "coil_servo"

    def __init__(self, name: str, *, host: str | None, profiles: dict,
                 profile_errors: dict | None = None, models: dict | None = None,
                 connect, simulated: bool = False,
                 poll_interval: float = 0.1, history_s: float = 600.0,
                 logdir=None, action_log=None, enable_timeout: float = 30.0,
                 log_every: int = 1, reconnect_interval: float = 5.0,
                 cfg_refresh_s: float = 2.0, config_path=CONFIG_PATH):
        super().__init__(name)
        self.host = host
        self.order = list(profiles)                    # file order
        self.profiles = {k: v for k, v in profiles.items() if v is not None}
        self.profile_errors = dict(profile_errors or {})
        self.models = dict(models or {})
        for k, v in self.profiles.items():
            self.models.setdefault(k, v["model"])
        self.primary = next((k for k in self.order if k in self.models), None)
        self.connect = connect
        self.simulated = simulated
        self.poll_interval = poll_interval
        self.history_s = history_s
        self.logdir = logdir
        self.action_log = action_log
        self.enable_timeout = enable_timeout
        self.log_every = max(1, int(log_every))
        self.reconnect_interval = reconnect_interval
        self.cfg_refresh_s = cfg_refresh_s
        self.config_path = config_path
        self.actions = dict(ACTIONS, **(SIM_ACTIONS if simulated else {}))

        self._io = threading.RLock()          # board access
        self._lock = threading.Lock()         # state below
        self._stop = threading.Event()
        self._thread = None
        self.board = None
        self.connection = "connecting"
        self.error = None
        self.sts = None
        self.t_sts = None                     # wall clock of the last poll
        self.m_sts = None                     # monotonic time of the last poll
        self.readback = None
        self.t_readback = 0.0
        self.held = []                        # profiles the board holds
        self.hist = deque(maxlen=int(history_s / poll_interval) + 10)
        self.pending = None
        self.notice = None
        self.job = None
        self.last_job = None                  # the finished one, for viewers
        self._job_cancel = threading.Event()
        self._capture_lock = threading.Lock()
        self.capture = None
        self.capture_seq = 0
        self.auto_capture_s = 0.0
        self._next_auto = 0.0
        self._hb = None                       # (heartbeat, time it changed)
        self._low_since = None                # latch guess: start of "low"
        self._n_polls = 0
        self.status_log = StatusLog(logdir, name) if logdir else None

    # ---- profiles and scale ----------------------------------------------------
    def scale_profile(self) -> str | None:
        """The profile whose physics converts counts to amps: the one the
        board holds, else the first in channels.toml."""
        with self._lock:
            held = list(self.held)
        for k in held:
            if k in self.models:
                return k
        return self.primary

    def _model(self):
        k = self.scale_profile()
        return self.models.get(k) if k else None

    def _i_fs(self):
        m = self._model()
        return m.I_FS if m else None

    def _rated(self, name: str | None) -> float | None:
        if name in self.profiles:
            return self.profiles[name]["i_rated"]
        m = self.models.get(name)
        return m.I_rated if m else None

    def _match(self, rb: dict) -> list:
        """Profiles whose settings the CFG read-back holds (the same test as
        config.identify_profile, on PROFILE_MATCH_KEYS)."""
        return [k for k in self.order if k in self.profiles
                and all(rb.get(f) == self.profiles[k]["cfg"][f]
                        for f in PROFILE_MATCH_KEYS)]

    def _scale_note(self) -> str | None:
        fs = {round(m.I_FS, 6) for m in self.models.values()}
        if len(fs) > 1 and not self.held:
            return (f"the board's settings match no profile and the profiles "
                    f"use different sensor scales: amps shown with the "
                    f"[{self.primary}] scale")
        return None

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self.primary is None:
            self.connection = "no profile"
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"poll-{self.name}")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._job_cancel.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._io:
            if self.board is not None:
                self.board.close()
                self.board = None
        if self.status_log:
            self.status_log.close()

    # ---- poll loop -----------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.board is None and not self._connect():
                    self._stop.wait(self.reconnect_interval)
                    continue
                t_next = time.monotonic() + self.poll_interval
                with self._io:
                    s = self.board.sts()
                    rb = None
                    if time.monotonic() - self.t_readback > self.cfg_refresh_s:
                        rb = self.board.read_cfg()
                self._update(s, rb)
                self._check_pending(s)
                self._maybe_auto_capture()
                self._stop.wait(max(0.0, t_next - time.monotonic()))
            except OSError as exc:            # incl. ConnectionError, timeout
                self._disconnect(f"connection lost: {exc}")
            except Exception as exc:          # noqa: BLE001 -- a bug or a
                # malformed reply must not silently end the polling (the
                # page would keep showing the last values as live)
                self._disconnect(f"poll error ({type(exc).__name__}: {exc});"
                                 f" reconnecting")
                self._stop.wait(self.reconnect_interval)

    def _connect(self) -> bool:
        try:
            b = self.connect()
        except Exception as exc:              # unreachable, refused, DNS ...
            with self._lock:
                was = self.connection
                self.connection = "offline"
                self.error = str(exc)
            if was != "offline":
                self._event("offline", str(exc))
            return False
        with self._io:
            self.board = b
        with self._lock:
            self.connection = "online"
            self.error = None
            self.t_readback = 0.0             # read CFG on the first poll
        self._event("connected", "")
        return True

    def _disconnect(self, why: str) -> None:
        with self._io:
            if self.board is not None:
                try:
                    self.board.close()
                except OSError:
                    pass
            self.board = None
        with self._lock:
            self.connection = "offline"
            self.error = why
            self.pending = None
        self._job_cancel.set()
        self._event("offline", why)

    def _update(self, s: dict, rb: dict | None) -> None:
        t = time.time()
        now = time.monotonic()
        held = self._match(rb) if rb is not None and \
            s["map_rev"] == MAP_REV else None
        with self._lock:
            if rb is not None:
                self.readback = rb
                self.t_readback = now
                self.held = held or []
        i_fs, model = self._i_fs(), self._model()
        i = i_meas_amps(s["i_meas"], i_fs)
        sp = counts_to_amps(s["sp_active"], i_fs)
        v = out1_volts(s["u14"], model)
        rated = self._rated(self.scale_profile()) or 0.0
        low = (s["fsm_state_name"] == "RUN" and s["bridge_en"]
               and abs(sp) > LATCH_SP_FRAC * rated
               and abs(i) < max(LATCH_I_FRAC * rated,
                                LATCH_I_COUNTS * i_fs / 8192))
        with self._lock:
            self.sts = s
            self.t_sts = t
            self.m_sts = now
            if self._hb is None or self._hb[0] != s["heartbeat"]:
                self._hb = (s["heartbeat"], now)
            self._low_since = (self._low_since or now) if low else None
            self.hist.append((t, i, sp, s["u14"], v))
            held_now = list(self.held)
        self._n_polls += 1
        if self.status_log and self._n_polls % self.log_every == 0:
            self.status_log.log(t, s, i, sp, v, "/".join(held_now))

    # ---- state for viewers ---------------------------------------------------
    def _heartbeat_alive(self) -> bool | None:
        if self._hb is None:
            return None
        return time.monotonic() - self._hb[1] < max(1.0, 5 * self.poll_interval)

    def _stale_after(self) -> float:
        return max(1.0, 5 * self.poll_interval)

    def _map_ok(self) -> bool | None:
        return None if self.sts is None else self.sts["map_rev"] == MAP_REV

    def stop_block(self) -> str | None:
        """Why Stop cannot be sent right now (board unreachable, old
        bitstream), or None. Stop never depends on a profile."""
        with self._lock:
            if self.connection != "online" or self.sts is None:
                return f"board {self.connection}: {self.error or 'no status yet'}"
            if self.sts["map_rev"] != MAP_REV:
                return (f"old bitstream: the board reports register-map "
                        f"revision {self.sts['map_rev']}, this host expects "
                        f"{MAP_REV}. Monitoring only -- redeploy the current "
                        f"coil_servo.bit.bin (python -m coil_servo_host."
                        f"deploy {self.name}).")
        return None

    def control_block(self) -> str | None:
        """Why control is refused right now, or None."""
        block = self.stop_block()
        if block:
            return block
        if not self.profiles:
            errs = "; ".join(f"[{k}] {v}" for k, v in self.profile_errors.items())
            return f"no usable profile in channels.toml: {errs}"
        return None

    def sequence_lock(self) -> str | None:
        """Look-only while the control computer owns the coil: servo enabled
        with the setpoint from IN2. Only Stop and captures are accepted."""
        with self._lock:
            rb = self.readback
        if rb and rb["servo_enable"] and not rb["sp_source"]:
            return ("the control computer owns this coil (servo enabled, "
                    "setpoint from IN2): look only. STOP still works; press "
                    "it first to take over by hand.")
        return None

    def _latch_guess(self) -> bool:
        return (self._low_since is not None
                and time.monotonic() - self._low_since > LATCH_HOLD_S)

    def _alarms(self) -> tuple[list, list]:
        """(red alarms, amber warnings) as short strings. Red = a person
        must act on the coil now; amber = worth a look."""
        red, amber = [], []
        for k, err in self.profile_errors.items():
            amber.append(f"profile [{k}] refused by the config loader "
                         f"(no control as {k}): {err}")
        s = self.sts
        if s is None or self.connection != "online":
            return red, amber
        if s["map_rev"] != MAP_REV:
            red.append(f"OLD BITSTREAM (map rev {s['map_rev']}, expected "
                       f"{MAP_REV}): monitoring only, flags may be misread")
        if s["fsm_state_name"] == "TRIPPED":
            red.append("TRIPPED: over-current trip, bridge open")
        elif s["fsm_state_name"] == "TRIP":
            red.append("TRIP: over-current, current freewheeling down")
        if s["timeout_hold"]:
            red.append("TIMEOUT_HOLD: a flip never reached zero current")
        if s["fault"]:
            red.append("FAULT input (DIO5) high -- not wired in the MVP")
        if s["sp_sign_mismatch"]:
            red.append("SIGN MISMATCH: setpoint sign vs bridge polarity, "
                       "holding zero")
        if self._heartbeat_alive() is False:
            red.append("HEARTBEAT STUCK: fabric not running?")
        if self._latch_guess():
            amber.append("current ~0 A although the setpoint is not and the "
                         "bridge is enabled: possibly the logic-board latch "
                         "tripped (red FAULT LED) -- or the supply is off or "
                         "tripped, or the circuit is open. Look at the logic "
                         "board and the supply.")
        if self.readback is not None and s["map_rev"] == MAP_REV \
                and not self.held:
            amber.append("board settings match no profile (not configured "
                         "since the bitstream was loaded, or changed by "
                         "hand): apply a profile")
        note = self._scale_note()
        if note:
            amber.append(note)
        if s["out_sat"]:
            amber.append("OUT1 at the command clamp")
        if s["int_railed"]:
            amber.append("integrator railed")
        return red, amber

    def snapshot(self) -> dict:
        scale = self.scale_profile()
        model = self.models.get(scale) if scale else None
        i_fs = model.I_FS if model else None
        with self._lock:
            s = self.sts
            age = (time.monotonic() - self.m_sts) if self.m_sts else None
            out = {
                "name": self.name, "kind": self.kind,
                "host": self.host,
                "simulated": self.simulated,
                "connection": self.connection,
                "error": self.error,
                "t": self.t_sts,
                "age_s": _f(age, 2) if age is not None else None,
                "stale": bool(self.connection == "online" and age is not None
                              and age > self._stale_after()),
                "profiles": [{
                    "name": k,
                    "bench": bool(self.profiles[k]["bench"])
                    if k in self.profiles else None,
                    "i_rated": self._rated(k),
                    "i_supply_max": (self.models[k].I_supply_max
                                     if k in self.models else None),
                    "error": self.profile_errors.get(k)} for k in self.order],
                "held": list(self.held),
                "scale_profile": scale,
                "i_rated": self._rated(scale),
                "i_supply_max": model.I_supply_max if model else None,
                "i_fs": i_fs,
                "map_rev_expected": MAP_REV,
                "pending": None, "job": None, "last_job": self.last_job,
                "notice": self.notice,
                "capture_seq": self.capture_seq,
                "auto_capture_s": self.auto_capture_s,
            }
            if self.pending:
                left = self.pending["deadline"] - time.monotonic()
                out["pending"] = dict(self.pending, deadline=None,
                                      seconds_left=_f(max(left, 0), 1))
            if self.job:
                # copy: the job thread keeps appending points
                out["job"] = dict(self.job, points=list(self.job["points"]))
            if s is not None and i_fs:
                out.update({
                    "state": s["fsm_state_name"],
                    "flags": {k: bool(s[k]) for k in FLAG_NAMES},
                    "map_rev": s["map_rev"],
                    "map_ok": s["map_rev"] == MAP_REV,
                    "heartbeat_alive": self._heartbeat_alive(),
                    "i_amps": _f(i_meas_amps(s["i_meas"], i_fs)),
                    "sp_amps": _f(counts_to_amps(s["sp_active"], i_fs)),
                    "u14": s["u14"],
                    "out1_volts": _f(out1_volts(s["u14"], model), 5),
                })
            rb = self.readback
            if rb is not None and i_fs:
                out["mode"] = {
                    "servo_enable": rb["servo_enable"],
                    "sp_source": "register" if rb["sp_source"] else "in2",
                    "open_loop": rb["open_loop"],
                    "setpoint_amps": _f(counts_to_amps(rb["setpoint"], i_fs)),
                }
        red, amber = self._alarms()
        out["alarms"], out["warnings"] = red, amber
        out["control_block"] = self.control_block()
        out["stop_block"] = self.stop_block()
        out["sequence_lock"] = self.sequence_lock()
        return out

    def history(self, span_s: float) -> dict:
        t_min = time.time() - span_s
        with self._lock:
            rows = [r for r in self.hist if r[0] >= t_min]
        cols = list(zip(*rows)) if rows else [[]] * 5
        return {"t": [_f(x, 3) for x in cols[0]],
                "i_amps": [_f(x) for x in cols[1]],
                "sp_amps": [_f(x) for x in cols[2]],
                "u14": [int(x) for x in cols[3]],
                "out1_volts": [_f(x, 5) for x in cols[4]]}

    def info(self, profile: str | None = None) -> dict:
        """One profile's derived values and the board's CFG compared with
        it (default: the profile the board holds, else the first)."""
        name = profile if profile in self.order else self.scale_profile()
        ch = self.profiles.get(name)
        out = {"name": name, "board": self.name,
               "config_file": str(self.config_path),
               "bench": bool(ch["bench"]) if ch else False,
               "host": self.host,
               "profile_error": self.profile_errors.get(name),
               "summary": [], "compare": [], "live": {}}
        if ch:
            out["header"] = summary_header(ch)
            out["summary"] = summary_rows(ch)
        with self._lock:
            rb = self.readback
            out["readback_age_s"] = (
                _f(time.monotonic() - self.t_readback, 1) if rb else None)
            out["map_ok"] = self._map_ok()
            out["held"] = list(self.held)
        if rb is not None:
            cfg = ch["cfg"] if ch else {}
            for k in cfg:
                out["compare"].append({"field": k, "profile": cfg[k],
                                       "board": rb.get(k),
                                       "match": rb.get(k) == cfg[k]})
            out["live"] = {k: rb[k] for k in LIVE_FIELDS if k in rb}
            out["other"] = {k: rb[k] for k in CFG_FIELDS
                            if k not in cfg and k not in LIVE_FIELDS}
        return out

    def latest_capture(self) -> dict | None:
        with self._lock:
            return self.capture

    # ---- actions -------------------------------------------------------------
    def perform(self, action: str, params: dict, client: str) -> dict:
        if action not in self.actions:
            raise ActionError(f"{self.name}: no action {action!r}", 404)
        shown = json.dumps({k: v for k, v in params.items() if k != "confirm"})
        try:
            block = self.stop_block() if action == "stop" \
                else self.control_block()
            if block:
                raise ActionError(block)
            lock = self.sequence_lock()
            if lock and action not in SEQUENCE_OK \
                    and not action.startswith("sim_"):
                raise ActionError(lock)
            message = getattr(self, "_act_" + action)(params)
        except ActionError as exc:
            self._log(client, action, shown, f"refused: {exc}")
            raise
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            self._log(client, action, shown, f"error: {exc}")
            raise ActionError(f"{action} failed: {exc}") from exc
        self._log(client, action, shown, f"ok: {message}")
        self._set_notice("info", message)
        return {"ok": True, "message": message}

    def _profile(self, params, *, must_be_held: bool) -> tuple[str, dict]:
        """The profile an action acts as: params["profile"], else the one
        the board holds. Refused if unknown, refused by the loader, or (when
        must_be_held) not the settings the board holds."""
        with self._lock:
            held = list(self.held)
        name = params.get("profile")
        if name is None:
            if len(held) != 1:
                raise ActionError("say which profile (the board's settings "
                                  "match none, or several)", 400)
            name = held[0]
        if name not in self.order:
            raise ActionError(f"[{name}] is not a profile of this board "
                              f"({', '.join(self.order)})", 400)
        if name not in self.profiles:
            raise ActionError(f"profile [{name}] was refused by the config "
                              f"loader: {self.profile_errors.get(name)}")
        if must_be_held and name not in held:
            now = " / ".join(held) if held else "no profile (unknown settings)"
            raise ActionError(
                f"the board holds {now}, not [{name}]: apply profile "
                f"[{name}] first (the dashboard only drives a board whose "
                f"configuration it knows)")
        return name, self.profiles[name]

    def _write(self, **fields) -> None:
        """CFG write after re-syncing the shadow (another tool may have
        written since; without the re-sync we would put its word back)."""
        with self._io:
            self.board.sync_shadow()
            self.board.write_cfg(**fields)

    def _readback_now(self) -> dict:
        with self._io:
            rb = self.board.read_cfg()
        held = self._match(rb)
        with self._lock:
            self.readback = rb
            self.t_readback = time.monotonic()
            self.held = held
        return rb

    def _state(self) -> str:
        with self._lock:
            return self.sts["fsm_state_name"] if self.sts else "?"

    def _need_idle(self, what: str) -> None:
        st = self._state()
        if st != "IDLE":
            raise ActionError(f"{what} only in IDLE (state is {st}); stop "
                              f"first")
        if self.job:
            raise ActionError(f"a {self.job['name']} is running; stop it "
                              f"first")
        if self.pending:
            raise ActionError("an enable is waiting for the arm edge; stop "
                              "first")

    def _act_stop(self, params) -> str:
        self._job_cancel.set()
        with self._lock:
            self.pending = None
        self._write(servo_enable=0, open_loop=0)
        try:
            self._readback_now()              # lifts the sequence lock now
        except OSError:
            pass
        return "servo_enable = 0: graceful ramp to zero, then IDLE"

    def _act_apply_profile(self, params) -> str:
        if params.get("profile") is None:
            raise ActionError("apply_profile needs \"profile\"", 400)
        name, ch = self._profile(params, must_be_held=False)
        self._need_idle("apply profile")
        with self._io:
            self.board.sync_shadow()
            self.board.apply_config(ch["cfg"])
        rb = self._readback_now()
        bad = [k for k, v in ch["cfg"].items() if rb[k] != v]
        if bad:
            raise ActionError(f"profile written but read back different "
                              f"in {bad} -- stale bitstream?")
        return (f"applied profile [{name}] from {self.config_path.name}"
                f" ({len(ch['cfg'])} fields, read back OK)")

    def _act_enable(self, params) -> str:
        source = params.get("source", "register")
        if source not in ("register", "in2"):
            raise ActionError("source must be 'register' or 'in2'", 400)
        self._need_idle("enable")
        self._readback_now()
        name, ch = self._profile(params, must_be_held=True)
        rb = self.readback
        bad = [k for k, v in ch["cfg"].items() if rb[k] != v]
        if bad:
            raise ActionError(
                f"the board's configuration differs from profile [{name}] "
                f"in {', '.join(bad)}: apply the profile first (the "
                f"dashboard only enables a board it knows the "
                f"configuration of)")
        if source == "register":
            self._write(setpoint=0)
        self._write(sp_source=1 if source == "register" else 0, open_loop=0,
                    servo_enable=1)
        self._readback_now()
        with self._lock:
            armed = bool(self.sts and self.sts["armed"])
            self.pending = {"action": "enable", "source": source,
                            "profile": name,
                            "deadline": time.monotonic() + self.enable_timeout,
                            "message": "raise arm (DIO4) now -- needs a "
                                       "rising edge"
                                       + (" (arm is already high: drop it "
                                          "and raise it again)" if armed
                                          else "")}
        return (f"servo enabled as [{name}] (setpoint from "
                f"{'this page, 0 A' if source == 'register' else 'IN2'}), "
                f"waiting in IDLE for an arm edge")

    def _check_pending(self, s: dict) -> None:
        with self._lock:
            p = self.pending
        if not p:
            return
        if s["fsm_state_name"] == "RUN":
            with self._lock:
                self.pending = None
            self._set_notice("info", "RUN reached")
            self._event("run", "arm edge seen, RUN")
        elif s["tripped"] or time.monotonic() > p["deadline"]:
            with self._lock:
                self.pending = None
            try:
                self._write(servo_enable=0)
            except (OSError, RuntimeError):
                pass
            msg = (f"FSM did not reach RUN (state {s['fsm_state_name']}); "
                   f"servo_enable set back to 0")
            self._set_notice("error", msg)
            self._event("enable_timeout", msg)

    def _act_setpoint(self, params) -> str:
        try:
            amps = float(params["amps"])
        except (KeyError, TypeError, ValueError):
            raise ActionError("amps: a number is required", 400) from None
        if self.job:
            raise ActionError(f"a {self.job['name']} is running")
        with self._io:
            self.board.sync_shadow()
        rb = self._readback_now()
        name, ch = self._profile(params, must_be_held=True)
        if not rb["sp_source"]:
            raise ActionError("the setpoint comes from IN2 (the control "
                              "computer); enable with source 'register' to "
                              "set it here")
        if rb["open_loop"]:
            raise ActionError("the board is in open loop (another tool?); "
                              "stop first")
        try:
            counts, _ = step_tool.command_counts(ch, amps, False)
        except ValueError as exc:
            raise ActionError(str(exc), 400) from None
        self._write(setpoint=counts)
        return f"setpoint {amps:g} A ({counts} counts, as [{name}])"

    def _act_trip_ack(self, params) -> str:
        if self._state() != "TRIPPED":
            raise ActionError(f"nothing to acknowledge (state "
                              f"{self._state()}, not TRIPPED)")
        with self._io:
            self.board.sync_shadow()
            self.board.pulse("trip_ack")
        return ("trip acknowledged: TRIPPED -> IDLE. To run again: enable, "
                "then a fresh arm edge")

    def _act_flip_fault_ack(self, params) -> str:
        if self._state() != "TIMEOUT_HOLD":
            raise ActionError(f"nothing to acknowledge (state "
                              f"{self._state()}, not TIMEOUT_HOLD)")
        with self._io:
            self.board.sync_shadow()
            self.board.pulse("flip_fault_ack")
        return "flip timeout acknowledged: TIMEOUT_HOLD -> RUN"

    # ---- capture -------------------------------------------------------------
    def _act_capture(self, params) -> str:
        if self.job:
            raise ActionError(f"a {self.job['name']} is using the capture "
                              f"FIFO")
        cap = self.do_capture()
        return f"captured {cap['n']} samples -> {cap['file']}"

    def _act_auto_capture(self, params) -> str:
        try:
            dt = float(params.get("interval_s", 0))
        except (TypeError, ValueError):
            raise ActionError("interval_s: a number is required", 400) from None
        if dt != 0 and dt < 1:
            raise ActionError("interval_s must be 0 (off) or >= 1 s", 400)
        with self._lock:
            self.auto_capture_s = dt
            self._next_auto = time.monotonic()
        return (f"repeating capture every {dt:g} s (files kept one hour)"
                if dt else "repeating capture off")

    def _maybe_auto_capture(self) -> None:
        if not self.auto_capture_s or time.monotonic() < self._next_auto:
            return
        self._next_auto = time.monotonic() + self.auto_capture_s
        if self.job or self.control_block() or self._capture_lock.locked():
            return

        def run():
            try:
                self.do_capture("auto")
                if self.logdir:
                    prune_auto_captures(self.logdir)
            except Exception as exc:          # noqa: BLE001 -- board gone ...
                self._set_notice("error", f"repeating capture: {exc}")
        threading.Thread(target=run, daemon=True).start()

    def do_capture(self, kind: str = "capture") -> dict:
        if not self._capture_lock.acquire(blocking=False):
            raise ActionError("a capture is already running")
        try:
            with self._io:
                if self.board is None:
                    raise ActionError("board offline")
                self.board.sync_shadow()
                data = self.board.capture(decimated=True)
            cols = decimated_columns(data, self._i_fs())
            return self._store_capture(cols, kind, {})
        finally:
            self._capture_lock.release()

    def _store_capture(self, cols: dict, kind: str, meta: dict) -> dict:
        t = time.time()
        path = None
        if self.logdir:
            path = capture_path(self.logdir, self.name, kind, t)
            write_csv(path, cols)
        with self._lock:
            self.capture_seq += 1
            self.capture = {
                "seq": self.capture_seq, "t": t, "kind": kind,
                "file": str(path) if path else None,
                "dt_s": 1 / FS_DEC, "n": len(cols["t_s"]),
                "i_amps": [_f(x) for x in cols["i_amps"]],
                "sp_amps": [_f(x) for x in cols["setpoint_amps"]],
                "meta": meta,
            }
            return self.capture

    # ---- jobs: step and sweep --------------------------------------------------
    def _start_job(self, name: str, target, *args) -> None:
        self._job_cancel.clear()
        with self._lock:
            self.job = {"name": name, "phase": "starting",
                        "started": time.time(), "done": False,
                        "error": None, "points": []}
        threading.Thread(target=self._job_wrapper, args=(target, *args),
                         daemon=True, name=f"{name}-{self.name}").start()

    def _job_wrapper(self, target, *args) -> None:
        name = self.job["name"]
        try:
            target(*args)
        except Exception as exc:              # board lost, refused write ...
            self._job_phase(f"failed: {exc}", error=str(exc))
            self._event(f"{name}_failed", str(exc))
        finally:
            try:
                if self.board is not None:
                    self._write(servo_enable=0, open_loop=0)
            except (OSError, RuntimeError):
                pass
            with self._lock:
                job, self.job = self.job, None
                self.last_job = dict(job, done=True) if job else None
            if job and not job.get("error"):
                self._set_notice("info", f"{name}: {job['phase']}")

    def _job_phase(self, phase: str, **extra) -> None:
        with self._lock:
            if self.job is not None:
                self.job["phase"] = phase
                self.job.update(extra)

    def _job_wait_run(self) -> bool:
        self._job_phase("waiting: raise arm (DIO4) now -- needs a rising "
                        "edge")
        deadline = time.monotonic() + self.enable_timeout
        while not self._job_cancel.is_set():
            with self._lock:
                s = self.sts
            if s and s["fsm_state_name"] == "RUN":
                return True
            if (s and s["tripped"]) or time.monotonic() > deadline:
                self._job_phase(
                    f"did not reach RUN (state "
                    f"{s['fsm_state_name'] if s else '?'}); servo disabled",
                    error="no RUN")
                return False
            time.sleep(0.05)
        self._job_phase("stopped")
        return False

    def _act_step(self, params) -> str:
        open_loop = bool(params.get("open_loop", False))
        amps = counts = None
        try:
            if params.get("counts") is not None:
                counts = int(params["counts"])
            else:
                amps = float(params["amps"])
        except (KeyError, TypeError, ValueError):
            raise ActionError("amps (or, open loop, counts): a number is "
                              "required", 400) from None
        self._need_idle("a step")
        name, ch = self._profile(params, must_be_held=True)
        try:
            counts, what = step_tool.command_counts(ch, amps, open_loop,
                                                    counts)
        except ValueError as exc:
            raise ActionError(str(exc), 400) from None
        self._start_job("step", self._job_step, ch, amps, open_loop, counts)
        return f"step started as [{name}] ({what}): raise arm (DIO4) when asked"

    def _job_step(self, ch: dict, amps, open_loop: bool, counts: int) -> None:
        self._job_phase("preparing: apply profile, enable")
        with self._io:
            self.board.sync_shadow()
            step_tool.prepare(self.board, ch, open_loop)
        if not self._job_wait_run():
            return
        time.sleep(0.05)
        self._job_phase("capturing the step")
        with self._io:
            raw = step_tool.capture_step(self.board, counts)
            step_tool.finish(self.board)
        cols = decimated_columns(decode_words(raw, decimated=True),
                                 ch["i_fs"])
        cap = self._store_capture(cols, "step", {
            "amps": amps, "open_loop": open_loop, "command_counts": counts,
            "profile": ch["name"]})
        self._job_phase(f"done: {cap['file'] or 'not saved'}")
        self._event("step_done", json.dumps({"amps": amps,
                                             "open_loop": open_loop}))

    def _act_sweep(self, params) -> str:
        try:
            min_stim = float(params.get("min_stim", 0.05))
        except (TypeError, ValueError):
            raise ActionError("min_stim: a number is required", 400) from None
        self._need_idle("a sweep")
        name, ch = self._profile(params, must_be_held=True)
        self._start_job("sweep", self._job_sweep, ch, min_stim)
        return (f"sweep started as [{name}]: raise arm (DIO4) when asked, "
                f"then step the function generator through the frequencies")

    def _job_sweep(self, ch: dict, min_stim: float) -> None:
        self._job_phase("preparing: apply profile, open loop, enable")
        with self._io:
            self.board.sync_shadow()
            sweep_tool.prepare(self.board, ch)
        if not self._job_wait_run():
            return
        path = None
        f = writer = None
        if self.logdir:
            path = capture_path(self.logdir, self.name, "sweep", time.time())
            f = open(path, "w", newline="")
            writer = csv.writer(f)
            writer.writerow(["f_hz", "mag", "phase_deg", "stim_amps"])
        self._job_phase("sweeping: step the function generator through the "
                        "frequencies; Stop ends the sweep",
                        file=str(path) if path else None)
        last_f = None
        try:
            while not self._job_cancel.is_set():
                if self._state() != "RUN":
                    self._job_phase(f"ended: state {self._state()}")
                    return
                with self._io:
                    data = self.board.capture(decimated=True)
                point = sweep_tool.analyse(data, ch["i_fs"], min_stim, last_f)
                if point is not None:
                    last_f = point[0]
                    row = [_f(x, 6) for x in point]
                    with self._lock:
                        self.job["points"].append(row)
                    if writer:
                        writer.writerow(row)
                        f.flush()
                self._job_cancel.wait(0.2)
            self._job_phase(f"stopped after {len(self.job['points'])} points")
        finally:
            if f:
                f.close()

    # ---- simulation-only inputs ---------------------------------------------
    def _act_sim_arm(self, params) -> str:
        level = bool(params.get("level", True))
        with self._io:
            self.board.set_arm(level)
        return f"simulated arm line {'high' if level else 'low'}"

    def _act_sim_trip(self, params) -> str:
        with self._io:
            self.board.inject_trip()
        return "simulated over-current trip"

    def _act_sim_power(self, params) -> str:
        on = bool(params.get("on", True))
        with self._io:
            self.board.set_power(on)
        return ("simulated drive restored" if on else
                "simulated drive cut (latch tripped / supply off)")

    # ---- notices and logs ------------------------------------------------------
    def _set_notice(self, level: str, text: str) -> None:
        with self._lock:
            self.notice = {"t": time.time(), "level": level, "text": text}

    def _log(self, client: str, action: str, params: str, result: str):
        if self.action_log:
            self.action_log.log(self.name, client, action, params, result)

    def _event(self, what: str, detail: str) -> None:
        self._log("dashboard", what, "", detail)
