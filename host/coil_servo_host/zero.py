"""Zero-offset calibration of the current sensor (IN1) and the analog
setpoint input (IN2).

    python -m coil_servo_host.zero mot            # measure and print
    python -m coil_servo_host.zero mot --write    # ... and store in channels.toml
    python -m coil_servo_host.zero mot --apply    # ... and load into the board now

Every LEM current transducer has a small zero offset (the reading with no
current flowing); a magnetised core can make it several amps (11-11: about
-5.8 A). The FPGA subtracts a stored offset from every sample (CFG words
21/22, register_map.md) so the loop, the over-current trip and the flip zero
window all work on the true current. Characterise every new sensor, and
re-measure after changing the LEM, burden, in-amp gain or cabling.

Conditions (checked): the servo is stopped (IDLE, bridge disabled), so no
coil current flows; the LEM is powered (+-15 V) and warmed up; the control
computer's setpoint output (IN2) sits at 0 V. The tool records the RAW ADC
codes (capture_sel = 0, before the FPGA's correction), several 131 us
records spread over ~1 s so mains hum averages out.
"""

import argparse
import re
import time
from pathlib import Path

import numpy as np

from .board import Board
from .config import CONFIG_PATH, announce, load_channel


def measure_offsets(b: Board, i_fs: float, records: int = 8,
                    spacing_s: float = 0.125) -> dict:
    """Mean raw IN1/IN2 codes with the servo stopped. Returns counts (float),
    amps, and the per-record spread. Raises RuntimeError if the board is not
    idle with the bridge disabled."""
    s = b.sts()
    if s["fsm_state_name"] not in ("IDLE", "TRIPPED") or s["bridge_en"]:
        raise RuntimeError(
            f"the servo must be stopped for a zero calibration (state "
            f"{s['fsm_state_name']}, bridge_en {s['bridge_en']}): no coil "
            f"current may flow. Stop it first.")
    means = []
    for k in range(records):
        data = b.capture(decimated=False)
        means.append(data.mean(axis=0))
        if k + 1 < records:
            time.sleep(spacing_s)
    means = np.array(means)
    in1, in2 = means.mean(axis=0)
    lsb = i_fs / 8192
    return {"in1_counts": float(in1), "in2_counts": float(in2),
            "in1_amps": float(in1 * lsb), "in2_amps": float(in2 * lsb),
            "in1_spread_counts": float(np.ptp(means[:, 0])),
            "in2_spread_counts": float(np.ptp(means[:, 1]))}


def write_offsets(name: str, in1_amps: float, in2_amps: float,
                  path: Path = CONFIG_PATH) -> None:
    """Replace in1_offset_amps / in2_offset_amps in the [name] section of
    channels.toml, keeping everything else (comments included) as is."""
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(rf"^\[{re.escape(name)}\]\s*$", text, re.M)
    if not m:
        raise KeyError(f"no [{name}] section in {path}")
    nxt = re.search(r"^\[", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    sec = text[m.start():end]
    stamp = time.strftime("%Y-%m-%d")
    for key, val in (("in1_offset_amps", in1_amps),
                     ("in2_offset_amps", in2_amps)):
        line = f"{key} = {val:.4f}"
        pad = " " * max(1, 32 - len(line))
        new = f"{line}{pad}# measured {stamp} (coil_servo_host.zero)"
        sec, n = re.subn(rf"^{key}\s*=.*$", new, sec, count=1, flags=re.M)
        if n == 0:
            sec = sec.rstrip("\n") + "\n" + new + "\n\n"
    Path(path).write_text(text[:m.start()] + sec + text[end:],
                          encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("channel")
    p.add_argument("--records", type=int, default=8)
    p.add_argument("--write", action="store_true",
                   help="store the result in channels.toml")
    p.add_argument("--apply", action="store_true",
                   help="also load the new offsets into the board now")
    args = p.parse_args()
    ch = load_channel(args.channel)
    announce(ch)
    try:
        b = Board(ch["host"])
    except ConnectionError as exc:
        raise SystemExit(f"*** {exc}") from None
    with b:
        try:
            r = measure_offsets(b, ch["i_fs"], args.records)
        except RuntimeError as exc:
            raise SystemExit(f"*** {exc}") from None
        lsb_ma = ch["i_fs"] / 8192 * 1e3
        print(f"IN1 zero offset: {r['in1_amps']:+.3f} A ({r['in1_counts']:+.1f} "
              f"counts; spread {r['in1_spread_counts']:.1f} counts between "
              f"records; 1 count = {lsb_ma:.1f} mA)")
        print(f"IN2 zero offset: {r['in2_amps']:+.3f} A ({r['in2_counts']:+.1f} "
              f"counts; spread {r['in2_spread_counts']:.1f} counts)")
        if abs(r["in1_amps"]) > 0.1 * ch["i_fs"]:
            print("*** IN1 offset above 10 % of full scale: check the sensor "
                  "(magnetised core? unpowered? wiring?) before calibrating ***")
        if args.write:
            write_offsets(args.channel, r["in1_amps"], r["in2_amps"])
            print(f"written to channels.toml [{args.channel}]")
        else:
            print(f"to store: rerun with --write (or set in [{args.channel}]: "
                  f"in1_offset_amps = {r['in1_amps']:.4f}, "
                  f"in2_offset_amps = {r['in2_amps']:.4f})")
        if args.apply:
            ch2 = load_channel(args.channel) if args.write else ch
            cfg_counts = dict(
                in1_offset=round(r["in1_counts"]),
                in2_offset=round(r["in2_counts"]))
            if args.write:
                cfg_counts = {k: ch2["cfg"][k] for k in cfg_counts}
            b.write_cfg(**cfg_counts)
            print(f"loaded into the board: {cfg_counts}")


if __name__ == "__main__":
    main()
