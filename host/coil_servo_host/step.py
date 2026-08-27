"""Step-response capture -> CSV. FIRST RUNS: DUMMY RESISTIVE LOAD ONLY.

    python -m coil_servo_host.step mot --amps 5 --open-loop -o step.csv

--open-loop drives the output stage directly with the (clamped) setpoint --
this is the measurement BOOTSTRAP wants the PI gains derived from. Without
it, the closed-loop step response is captured instead. Either way the
decimated capture (976.6 kS/s, 16.8 ms window) records both the error and
the measured current; the CSV has time, measured amps, and setpoint amps.

The board must be armed (DIO4) for anything to drive.
"""

import argparse
import csv
import time

from .board import Board
from .config import load_channel

FS_DEC = 125e6 / 128


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    p.add_argument("--amps", type=float, required=True, help="step target")
    p.add_argument("--open-loop", action="store_true")
    p.add_argument("-o", "--out", default="step.csv")
    args = p.parse_args()
    ch = load_channel(args.channel)
    # captured columns are (i_dec>>6, e_dec>>6); i_dec = 128 * mean code, so
    # one captured LSB = 2^6/128 = 0.5 ADC code = i_fs/16384 amps
    scale = ch["i_fs"] / 16384

    if abs(args.amps) > ch["i_rated"]:
        raise SystemExit(
            f"--amps {args.amps} exceeds this channel's rated current "
            f"({ch['i_rated']} A); the fabric clamp would cap the drive at "
            f"rated anyway, so this is almost certainly a typo")

    sp_counts = round(args.amps / ch["i_fs"] * 8192)
    with Board(ch["host"], max_clamp=ch["cfg"]["out_clamp"]) as b:
        b.apply_config(ch["cfg"])
        b.write_cfg(sp_source=1, setpoint=0,
                    open_loop=1 if args.open_loop else 0, servo_enable=1)
        s = b.warn_flags()
        if not s["armed"]:
            raise SystemExit("board is not armed (DIO4) -- nothing will drive")
        time.sleep(0.05)

        # restart the capture, then step the setpoint ~2 ms into the window
        b.write_cfg(capture_sel=1)
        b.pulse("fifo_rst")
        time.sleep(0.002)
        b.write_cfg(setpoint=sp_counts)
        time.sleep(0.020)          # capture window (16.8 ms) elapses
        raw = b.pop_words(0x4200_0000, 16384)

        # ramp back down and disable (graceful stop in fabric)
        b.write_cfg(setpoint=0)
        time.sleep(0.05)
        b.write_cfg(servo_enable=0, open_loop=0)

    import numpy as np
    raw = raw.astype(np.int64)
    i16 = (raw & 0xFFFF).astype(np.int32)
    e16 = ((raw >> 16) & 0xFFFF).astype(np.int32)
    i16[i16 >= 1 << 15] -= 1 << 16
    e16[e16 >= 1 << 15] -= 1 << 16

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "i_amps", "setpoint_amps"])
        for k in range(len(i16)):
            w.writerow([k / FS_DEC, i16[k] * scale,
                        (e16[k] + i16[k]) * scale])
    print(f"wrote {args.out} ({len(i16)} samples, {len(i16)/FS_DEC*1e3:.1f} ms)")


if __name__ == "__main__":
    main()
