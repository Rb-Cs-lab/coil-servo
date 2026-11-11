"""Files written by the dashboard.

  <logdir>/status/<board>_<YYYYMMDD-HHMM>.csv   every polled status, in
                                                10-minute segments; only
                                                the last hour is kept
  <logdir>/actions_<YYYY-MM-DD>.csv             control actions + events,
                                                one file per day, kept in
                                                full (the audit trail)
  <logdir>/captures/<board>_<YYYYMMDD-HHMMSS>_<kind>.csv
                                                captures; kind "auto"
                                                (repeating capture) is
                                                pruned to the last hour,
                                                manual ones are kept

Times are local. Files are opened for append, so a dashboard restart
continues the current segment / day.
"""

import csv
import re
import threading
import time
from datetime import datetime
from pathlib import Path

STATUS_COLUMNS = ["time", "unix_s", "state", "armed", "bridge_en",
                  "polarity", "tripped", "out_sat", "u_floor",
                  "sp_sign_mismatch", "timeout_hold", "fault", "map_rev",
                  "i_amps", "sp_amps", "u14", "out1_volts", "profile"]
ACTION_COLUMNS = ["time", "unix_s", "channel", "client", "action",
                  "params", "result"]

SEGMENT_S = 600.0              # status file length
STATUS_KEEP_S = 3600.0         # status files wholly older than this go
AUTO_CAPTURE_KEEP_S = 3600.0   # repeating captures older than this go


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


class _CsvFile:
    """Append rows to a file chosen by key(t); a new key opens a new file."""

    def __init__(self, columns: list, flush_interval: float = 1.0):
        self.columns = columns
        self.flush_interval = flush_interval
        self._f = None
        self._w = None
        self._key = None
        self._last_flush = 0.0
        self._lock = threading.Lock()

    def key(self, t: float):
        raise NotImplementedError

    def path_for(self, t: float) -> Path:
        raise NotImplementedError

    def opened(self, t: float) -> None:
        """Hook: a new file was just opened."""

    def write(self, t: float, row: list) -> None:
        with self._lock:
            k = self.key(t)
            if k != self._key:
                self._open(t)
                self._key = k
                self.opened(t)
            self._w.writerow(row)
            if time.monotonic() - self._last_flush >= self.flush_interval:
                self._f.flush()
                self._last_flush = time.monotonic()

    def _open(self, t: float) -> None:
        if self._f:
            self._f.close()
        path = self.path_for(t)
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        self._f = open(path, "a", newline="")
        self._w = csv.writer(self._f)
        if new:
            self._w.writerow(self.columns)
            self._f.flush()

    def flush(self) -> None:
        with self._lock:
            if self._f:
                self._f.flush()

    def close(self) -> None:
        with self._lock:
            if self._f:
                self._f.close()
                self._f = None
                self._key = None


class DailyCsv(_CsvFile):
    """<dir>/<prefix><YYYY-MM-DD>.csv, rolling over at local midnight."""

    def __init__(self, directory: Path, prefix: str, columns: list,
                 flush_interval: float = 1.0):
        super().__init__(columns, flush_interval)
        self.dir = Path(directory)
        self.prefix = prefix

    def key(self, t):
        return datetime.fromtimestamp(t).date()

    def path_for(self, t: float) -> Path:
        day = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
        return self.dir / f"{self.prefix}{day}.csv"


class SegmentCsv(_CsvFile):
    """<dir>/<prefix><YYYYMMDD-HHMM>.csv, one file per `segment_s` of local
    time; when a new segment starts, segments that ended more than
    `keep_s` ago are deleted."""

    def __init__(self, directory: Path, prefix: str, columns: list,
                 segment_s: float = SEGMENT_S, keep_s: float = STATUS_KEEP_S,
                 flush_interval: float = 1.0):
        super().__init__(columns, flush_interval)
        self.dir = Path(directory)
        self.prefix = prefix
        self.segment_s = segment_s
        self.keep_s = keep_s
        self._re = re.compile(re.escape(prefix) + r"(\d{8}-\d{4})\.csv$")

    def key(self, t):
        return int(t // self.segment_s)

    def path_for(self, t: float) -> Path:
        start = self.key(t) * self.segment_s
        stamp = datetime.fromtimestamp(start).strftime("%Y%m%d-%H%M")
        return self.dir / f"{self.prefix}{stamp}.csv"

    def opened(self, t: float) -> None:
        self.prune(t)

    def prune(self, now: float | None = None) -> list:
        """Delete this prefix's segments that ended before now - keep_s.
        Returns the deleted paths."""
        now = time.time() if now is None else now
        gone = []
        if not self.dir.is_dir():
            return gone
        for p in self.dir.iterdir():
            m = self._re.match(p.name)
            if not m:
                continue
            start = datetime.strptime(m.group(1), "%Y%m%d-%H%M").timestamp()
            if start + self.segment_s < now - self.keep_s:
                try:
                    p.unlink()
                    gone.append(p)
                except OSError:             # open elsewhere: next time
                    pass
        return gone


class StatusLog(SegmentCsv):
    def __init__(self, logdir: Path, board: str, **kw):
        super().__init__(Path(logdir) / "status", f"{board}_", STATUS_COLUMNS,
                         **kw)

    def log(self, t: float, s: dict, i_amps: float, sp_amps: float,
            out1_volts: float, profile: str = "") -> None:
        self.write(t, [_stamp(t), f"{t:.3f}", s["fsm_state_name"],
                       s["armed"], s["bridge_en"], s["polarity"],
                       s["tripped"], s["out_sat"], s["u_floor"],
                       s["sp_sign_mismatch"], s["timeout_hold"], s["fault"],
                       s["map_rev"], f"{i_amps:.4f}", f"{sp_amps:.4f}",
                       s["u14"], f"{out1_volts:.5f}", profile])


class ActionLog(DailyCsv):
    def __init__(self, logdir: Path):
        super().__init__(Path(logdir), "actions_", ACTION_COLUMNS,
                         flush_interval=0.0)       # flush every action

    def log(self, channel: str, client: str, action: str, params: str,
            result: str) -> None:
        t = time.time()
        self.write(t, [_stamp(t), f"{t:.3f}", channel, client, action,
                       params, result])


def capture_path(logdir: Path, channel: str, kind: str, t: float) -> Path:
    d = Path(logdir) / "captures"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(t).strftime("%Y%m%d-%H%M%S")
    path = d / f"{channel}_{stamp}_{kind}.csv"
    k = 1
    while path.exists():                      # two captures in one second
        path = d / f"{channel}_{stamp}-{k}_{kind}.csv"
        k += 1
    return path


def prune_auto_captures(logdir: Path, keep_s: float = AUTO_CAPTURE_KEEP_S,
                        now: float | None = None) -> list:
    """Delete repeating-capture files (kind "auto") older than keep_s.
    Manual captures, steps and sweeps are never touched."""
    now = time.time() if now is None else now
    d = Path(logdir) / "captures"
    gone = []
    if not d.is_dir():
        return gone
    for p in d.glob("*_auto.csv"):
        try:
            if p.stat().st_mtime < now - keep_s:
                p.unlink()
                gone.append(p)
        except OSError:
            pass
    return gone
