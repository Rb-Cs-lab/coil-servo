"""CSV logs written by the dashboard, one file per channel per day.

  <logdir>/status/<channel>_<YYYY-MM-DD>.csv   every polled status
  <logdir>/actions_<YYYY-MM-DD>.csv            control actions + events
  <logdir>/captures/<channel>_<YYYYMMDD-HHMMSS>_<kind>.csv

Dates are local time (a new file starts at local midnight). Files are
opened for append, so a dashboard restart continues the day's file.
"""

import csv
import threading
import time
from datetime import datetime
from pathlib import Path

STATUS_COLUMNS = ["time", "unix_s", "state", "armed", "bridge_en",
                  "polarity", "tripped", "out_sat", "u_floor",
                  "sp_sign_mismatch", "timeout_hold", "fault", "map_rev",
                  "i_amps", "sp_amps", "u14", "out1_volts"]
ACTION_COLUMNS = ["time", "unix_s", "channel", "client", "action",
                  "params", "result"]


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


class DailyCsv:
    """Append rows to <dir>/<prefix><date>.csv, rolling over at midnight."""

    def __init__(self, directory: Path, prefix: str, columns: list,
                 flush_interval: float = 1.0):
        self.dir = Path(directory)
        self.prefix = prefix
        self.columns = columns
        self.flush_interval = flush_interval
        self._f = None
        self._w = None
        self._date = None
        self._last_flush = 0.0
        self._lock = threading.Lock()

    def path_for(self, t: float) -> Path:
        day = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
        return self.dir / f"{self.prefix}{day}.csv"

    def write(self, t: float, row: list) -> None:
        with self._lock:
            day = datetime.fromtimestamp(t).date()
            if day != self._date:
                self._open(t)
                self._date = day
            self._w.writerow(row)
            if time.monotonic() - self._last_flush >= self.flush_interval:
                self._f.flush()
                self._last_flush = time.monotonic()

    def _open(self, t: float) -> None:
        if self._f:
            self._f.close()
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(t)
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
                self._date = None


class StatusLog(DailyCsv):
    def __init__(self, logdir: Path, channel: str):
        super().__init__(Path(logdir) / "status", f"{channel}_", STATUS_COLUMNS)

    def log(self, t: float, s: dict, i_amps: float, sp_amps: float,
            out1_volts: float) -> None:
        self.write(t, [_stamp(t), f"{t:.3f}", s["fsm_state_name"],
                       s["armed"], s["bridge_en"], s["polarity"],
                       s["tripped"], s["out_sat"], s["u_floor"],
                       s["sp_sign_mismatch"], s["timeout_hold"], s["fault"],
                       s["map_rev"], f"{i_amps:.4f}", f"{sp_amps:.4f}",
                       s["u14"], f"{out1_volts:.5f}"])


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
