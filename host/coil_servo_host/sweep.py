"""Swept-sine open-loop transfer function -> CSV.
FIRST RUNS: DUMMY RESISTIVE LOAD ONLY.

    python -m coil_servo_host.sweep mot -o tf.csv

Setup: a lab function generator drives IN2 (the analog setpoint input,
+/-1 V after the /10 divider). The board is put in open-loop mode: the IN2
value goes straight to OUT1 (count for count) through the hard clamp. In
the MVP OUT1 drives the pass bank, which conducts only above its dead zone
(~0.44 V at OUT1, i.e. ~4.4 V at the generator before the /10): set the
generator OFFSET above the dead zone at the operating point you want to
measure, and keep the amplitude small and inside [dead zone, clamp]. |H|
is then the plant gain G_pass/I_FS (~4.8 nominal), not 1.
The capture FIFO records stimulus and response synchronously at 976.6 kS/s
(16.8 ms per record), so amplitude and phase come from correlating the two
captured channels -- no signal generator in the FPGA, no timing needed from
this script.

Workflow: start this script, then step the function generator through the
frequencies you care about (10 Hz .. ~100 kHz; a few seconds per point).
Each time a clean new frequency is detected, a CSV row is appended and
flushed: frequency in Hz, |H| (dimensionless: measured amps per commanded
amp), phase in degrees, stimulus amplitude in amps. Ctrl-C ends the sweep.
The servo is enabled first; raise arm (DIO4) when prompted (the FSM needs
an arm EDGE after servo_enable).
"""

import argparse
import csv
import math
import time

from .board import Board
from .capture import FS_DEC, lsb_amps
from .config import announce, load_channel
from .tf import estimate_tf


def prepare(b: Board, ch: dict) -> None:
    """Apply the channel config, put the board in open loop with IN2 as the
    drive and enable the servo (it then waits for the arm edge)."""
    b.apply_config(ch["cfg"])
    b.write_cfg(sp_source=0, open_loop=1, servo_enable=1)   # IN2 drives


def finish(b: Board) -> None:
    b.write_cfg(servo_enable=0, open_loop=0)


def analyse(data, i_fs: float, min_stim: float, last_f: float | None):
    """One decimated capture -> a transfer-function point, or None when no
    clean new frequency is in it (no drive, stimulus below min_stim amps,
    or within 2 % of the last point's frequency).
    Returns (f_hz, |H|, phase_deg, stim_amps)."""
    scale = lsb_amps(i_fs)
    i_meas = data[:, 0].astype(float)
    stim = data[:, 1].astype(float) + i_meas          # sp = e + i
    result = estimate_tf(stim, i_meas, FS_DEC)
    if result is None:
        return None
    f0, h, amp = result
    if amp * scale < min_stim:
        return None                     # noise-floor pickup, not a drive
    if last_f is not None and abs(f0 - last_f) < 0.02 * last_f:
        return None                     # same point as last time
    return f0, abs(h), math.degrees(math.atan2(h.imag, h.real)), amp * scale


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    p.add_argument("-o", "--out", default="tf.csv")
    p.add_argument("--min-stim", type=float, default=0.05,
                   help="ignore detections with stimulus amplitude below "
                        "this many amps (rejects mains-hum pickup on an "
                        "undriven input; default 0.05)")
    args = p.parse_args()
    ch = load_channel(args.channel)
    announce(ch)

    # the CSV is written incrementally and flushed per row, so a crash or a
    # pulled cable can't lose an hour of sweeping
    out = open(args.out, "w", newline="")
    writer = csv.writer(out)
    writer.writerow(["f_hz", "mag", "phase_deg", "stim_amps"])
    n_rows = 0
    last_f = None
    with Board(ch["host"], max_clamp=ch["cfg"]["out_clamp"]) as b:
        prepare(b, ch)
        b.warn_flags()
        b.wait_for_run()
        print("open-loop mode; sweep the function generator now (Ctrl-C to stop)")
        try:
            while True:
                point = analyse(b.capture(decimated=True), ch["i_fs"],
                                args.min_stim, last_f)
                if point is None:
                    time.sleep(0.2)
                    continue
                f0, mag, phase, stim = point
                last_f = f0
                writer.writerow([f0, mag, phase, stim])
                out.flush()
                n_rows += 1
                print(f"f = {f0:9.1f} Hz   |H| = {mag:7.4f}   "
                      f"phase = {phase:+7.1f} deg   stim = {stim:.3f} A")
        except KeyboardInterrupt:
            pass
        finally:
            finish(b)
            out.close()

    print(f"wrote {args.out} ({n_rows} points; rows in sweep order -- "
          f"sort by the f_hz column when plotting)")


if __name__ == "__main__":
    main()
