"""Swept-sine open-loop transfer function -> CSV.
FIRST RUNS: DUMMY RESISTIVE LOAD ONLY.

    python -m coil_servo_host.sweep mot -o tf.csv

Setup: a lab function generator drives IN2 (the analog setpoint input,
+/-1 V after the /10 divider -- keep the amplitude small and the offset
positive so the drive stays on OUT1). The board is put in open-loop mode:
the setpoint goes straight to the output stage through the hard clamp.
The capture FIFO records stimulus and response synchronously at 976.6 kS/s
(16.8 ms per record), so amplitude and phase come from correlating the two
captured channels -- no signal generator in the FPGA, no timing needed from
this script.

Workflow: start this script, then step the function generator through the
frequencies you care about (10 Hz .. ~100 kHz; a few seconds per point).
Each time a clean new frequency is detected, a CSV row is appended:
frequency, |H|, phase in degrees, stimulus amplitude in amps. Ctrl-C ends
the sweep. The board must be armed (DIO4).
"""

import argparse
import csv
import math
import time

from .board import Board
from .config import load_channel
from .tf import estimate_tf

FS_DEC = 125e6 / 128


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    p.add_argument("-o", "--out", default="tf.csv")
    args = p.parse_args()
    ch = load_channel(args.channel)
    scale = ch["i_fs"] / 16384       # amps per captured LSB

    rows = []
    last_f = None
    with Board(ch["host"]) as b:
        b.apply_config(ch["cfg"])
        b.write_cfg(sp_source=0, open_loop=1, servo_enable=1)  # IN2 drives
        if not b.sts()["armed"]:
            raise SystemExit("board is not armed (DIO4) -- nothing will drive")
        print("open-loop mode; sweep the function generator now (Ctrl-C to stop)")
        try:
            while True:
                data = b.capture(decimated=True)
                i_meas = data[:, 0].astype(float)
                stim = data[:, 1].astype(float) + i_meas   # sp = e + i
                result = estimate_tf(stim, i_meas, FS_DEC)
                if result is None:
                    time.sleep(0.2)
                    continue
                f0, h, amp = result
                if last_f is not None and abs(f0 - last_f) < 0.02 * last_f:
                    time.sleep(0.2)
                    continue                    # same point as last time
                last_f = f0
                rows.append([f0, abs(h), math.degrees(math.atan2(h.imag, h.real)),
                             amp * scale])
                print(f"f = {f0:9.1f} Hz   |H| = {abs(h):7.4f}   "
                      f"phase = {rows[-1][2]:+7.1f} deg   "
                      f"stim = {rows[-1][3]:.3f} A")
        except KeyboardInterrupt:
            pass
        finally:
            b.write_cfg(servo_enable=0, open_loop=0)

    rows.sort()
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["f_hz", "mag", "phase_deg", "stim_amps"])
        w.writerows(rows)
    print(f"wrote {args.out} ({len(rows)} points)")


if __name__ == "__main__":
    main()
