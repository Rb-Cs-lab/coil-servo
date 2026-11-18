"""Measure the OUT1-vs-current curve for the setpoint feedforward
(register map rev 7, docs/register_map.md).

    python -m coil_servo_host.ffcal mot_bench            # measure and print
    python -m coil_servo_host.ffcal mot_bench --write    # ... and store it

What the feedforward needs: for each current, the OUT1 command that holds
it. The pass transistors' curve (dead zone, then a gain that rises with
current, and a shift as they warm) is measured here directly, in closed
loop: the servo holds a series of currents set through the setpoint
register, feedforward off, and for each one the tool averages the
controller's output (STS u14, the OUT1 command before the dither) once the
current has settled. Up the list and back down; the two passes are
averaged, and their difference is printed -- it is the drift during the
scan (the transistors warming), the uncertainty of the table.

--write stores the result as ff_curve_amps / ff_curve_counts in the
channel's section of channels.toml (comments kept); ff_enable and
ff_margin_counts are left as they are. The firmware table is then built by
config.py (python -m coil_servo_host.config <channel> shows it).

Before running: servo stopped (IDLE), bridge power on, the arm input
(DIO4) low -- the tool arms in software (dio_invert bit 1), like the bench
test scripts, and leaves the board stopped with the file's settings.
Currents never exceed the channel's I_rated.
"""

import argparse
import re
import time
from pathlib import Path

import numpy as np

from .board import Board
from .config import (CONFIG_PATH, amps_to_counts, announce, i_meas_amps,
                     load_channel)

FRACTIONS = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def _start(b: Board, ch: dict) -> None:
    s = b.sts()
    if s["fsm_state_name"] != "IDLE":
        raise RuntimeError(f"board is {s['fsm_state_name']}: stop it first")
    if s["armed"]:
        raise RuntimeError("the arm input is high: lower DIO4 first (the "
                           "tool needs an arm edge it makes itself)")
    b.apply_config(ch["cfg"])
    b.write_cfg(sp_source=1, open_loop=0, setpoint=0, ff_enable=0,
                dio_invert=0, servo_enable=1)
    time.sleep(0.05)
    b.write_cfg(dio_invert=0b010)              # software arm edge
    t_end = time.monotonic() + 1.0
    while b.sts()["fsm_state_name"] not in ("RUN", "ZERO_OPEN"):
        if time.monotonic() > t_end:
            raise RuntimeError(f"did not start: {b.sts()['fsm_state_name']}")
        time.sleep(0.01)


def _finish(b: Board, ch: dict) -> str:
    b.write_cfg(setpoint=0)
    time.sleep(0.2)
    if b.sts()["fsm_state_name"] == "TRIPPED":
        b.pulse("trip_ack")
    st = b.stop()
    b.write_cfg(dio_invert=0)
    b.apply_config(ch["cfg"])
    return st


def hold_point(b: Board, ch: dict, amps: float, settle_s: float,
               reads: int) -> tuple[float, float]:
    """Hold `amps` (drive frame: the setpoint register is in the board
    frame, so it takes the bridge polarity's sign), wait, then average
    (u14, |current|) over `reads` STS reads."""
    sign = -1 if b.sts()["polarity"] else 1
    b.write_cfg(setpoint=sign * amps_to_counts(amps, ch["i_fs"]))
    time.sleep(settle_s)
    u, i = [], []
    for _ in range(reads):
        s = b.sts()
        if s["fsm_state_name"] not in ("RUN",):
            raise RuntimeError(f"left RUN at {amps:.3f} A: {s['fsm_state_name']}")
        u.append(s["u14"])
        i.append(sign * i_meas_amps(s["i_meas"], ch["i_fs"]))
    return float(np.mean(u)), float(np.mean(i))


def measure(b: Board, ch: dict, fractions=FRACTIONS, settle_s: float = 0.15,
            reads: int = 200) -> dict:
    """Closed-loop scan up and down. Returns amps (requested), counts (mean
    of both passes), up / down counts, measured currents, and the scan's
    duration."""
    i_rated = ch["i_rated"]
    amps = [round(f * i_rated, 4) for f in fractions]
    t0 = time.monotonic()
    _start(b, ch)
    up, down, i_up, i_down = [], [], [], []
    try:
        for a in amps:
            u, i = hold_point(b, ch, a, settle_s, reads)
            up.append(u); i_up.append(i)
            print(f"  up   {a:7.3f} A: OUT1 {u:8.1f}  (measured {i:.3f} A)", flush=True)
        for a in reversed(amps):
            u, i = hold_point(b, ch, a, settle_s, reads)
            down.append(u); i_down.append(i)
            print(f"  down {a:7.3f} A: OUT1 {u:8.1f}  (measured {i:.3f} A)", flush=True)
    finally:
        print("finish:", _finish(b, ch), flush=True)
    down = down[::-1]
    i_down = i_down[::-1]
    mean = [(x + y) / 2 for x, y in zip(up, down)]
    # the curve must not decrease (more current never needs less OUT1):
    # running maximum removes noise-level dips at the bottom
    counts = list(np.maximum.accumulate(np.round(mean).astype(int)))
    return {"amps": amps, "counts": [int(c) for c in counts], "up": up,
            "down": down, "i_up": i_up, "i_down": i_down,
            "minutes": (time.monotonic() - t0) / 60}


def write_curve(name: str, amps, counts, path: Path = CONFIG_PATH) -> None:
    """Replace ff_curve_amps / ff_curve_counts in the [name] section of
    channels.toml, keeping everything else (comments included) as is."""
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(rf"^\[{re.escape(name)}\]\s*$", text, re.M)
    if not m:
        raise KeyError(f"no [{name}] section in {path}")
    nxt = re.search(r"^\[", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    sec = text[m.start():end]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    for key, vals, fmt in (("ff_curve_amps", amps, "{:g}"),
                           ("ff_curve_counts", counts, "{:d}")):
        new = (f"{key} = [" + ", ".join(fmt.format(v) for v in vals)
               + f"]  # measured {stamp} (coil_servo_host.ffcal)")
        sec, n = re.subn(rf"^{key}\s*=.*$", new, sec, count=1, flags=re.M)
        if n == 0:
            sec = sec.rstrip("\n") + "\n" + new + "\n\n"
    Path(path).write_text(text[:m.start()] + sec + text[end:], encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("channel")
    p.add_argument("--write", action="store_true",
                   help="store the curve in channels.toml")
    p.add_argument("--settle", type=float, default=0.15,
                   help="seconds to wait at each current (default 0.15)")
    args = p.parse_args()
    ch = load_channel(args.channel)
    announce(ch)
    with Board(ch["host"]) as b:
        r = measure(b, ch, settle_s=args.settle)
    drift = np.array(r["down"]) - np.array(r["up"])
    print(f"\nscan took {r['minutes']:.1f} min; down minus up: "
          f"{drift.min():+.1f} .. {drift.max():+.1f} counts "
          f"(warming during the scan; the table's uncertainty)")
    print(f"ff_curve_amps = {r['amps']}")
    print(f"ff_curve_counts = {r['counts']}")
    if args.write:
        write_curve(args.channel, r["amps"], r["counts"])
        print(f"stored in [{args.channel}] of {CONFIG_PATH}")
        ch2 = load_channel(args.channel)
        from .config import ff_text
        print("table:", ff_text(ch2["cfg"], ch2["model"].amps_per_lsb))
    else:
        print("to store: rerun with --write")


if __name__ == "__main__":
    main()
