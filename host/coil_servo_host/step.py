"""Step-response capture -> CSV. FIRST RUNS: DUMMY RESISTIVE LOAD ONLY.

    python -m coil_servo_host.step mot --amps 5 --open-loop -o step.csv

--open-loop drives OUT1 directly with a fixed command -- the measurement the
PI gains are derived from. In the MVP the OUT1 command is NOT in amps: the
pass bank has a dead zone and a gain G_pass, so --amps is converted to the
OUT1 command the model predicts for that current (channels.toml physics;
PROVISIONAL until G_pass and the dead zone are measured -- the captured
current shows what really happened). Without --open-loop the closed-loop
step response is captured; the firmware ramps the step at the configured
slew rates (200 A/ms up on the MOT). Either way the decimated capture
(976.6 kS/s, 16.8 ms window) records the error and the measured current;
the CSV has time, measured amps, and setpoint amps (in open loop the
"setpoint" column is the OUT1 command in sensor-scale amps, not a current).

The servo is enabled first; then raise arm (DIO4) when prompted -- the FSM
needs an arm EDGE after servo_enable.
"""

import argparse
import time

from .board import FIFO_ADDR, FIFO_DEPTH, Board
from .capture import FS_DEC, decimated_columns, decode_words, write_csv
from .config import amps_to_counts, load_channel


def command_counts(ch: dict, amps: float, open_loop: bool) -> int:
    """The setpoint register value for a step to `amps`. Raises ValueError
    above the rated current, or for a negative open-loop step. In open
    loop the value is the OUT1 command the model predicts for that current
    (PROVISIONAL until G_pass and the dead zone are measured)."""
    if abs(amps) > ch["i_rated"]:
        raise ValueError(
            f"{amps} A exceeds this channel's rated current "
            f"({ch['i_rated']} A); the fabric clamp would cap the drive at "
            f"rated anyway, so this is almost certainly a typo")
    if not open_loop:
        return amps_to_counts(amps, ch["i_fs"])
    if amps < 0:
        raise ValueError("an open-loop step needs amps >= 0: OUT2 is not "
                         "fitted in the MVP, OUT1 only drives up")
    return round(ch["model"].out1_for_current(amps) * 8192) if amps > 0 else 0


def prepare(b: Board, ch: dict, open_loop: bool) -> None:
    """Apply the channel config, select the setpoint register at 0 and
    enable the servo (it then waits for the arm edge)."""
    b.apply_config(ch["cfg"])
    b.write_cfg(sp_source=1, setpoint=0,
                open_loop=1 if open_loop else 0, servo_enable=1)


def capture_step(b: Board, sp_counts: int):
    """In RUN: restart the decimated capture, step the setpoint ~2 ms into
    the 16.8 ms window, drain the FIFO. Returns the raw FIFO words."""
    b.write_cfg(capture_sel=1)
    b.pulse("fifo_rst")
    time.sleep(0.002)
    b.write_cfg(setpoint=sp_counts)
    time.sleep(0.020)          # capture window (16.8 ms) elapses
    return b.pop_words(FIFO_ADDR, FIFO_DEPTH)


def finish(b: Board) -> None:
    """Ramp back down and disable (graceful stop in fabric)."""
    b.write_cfg(setpoint=0)
    time.sleep(0.05)
    b.write_cfg(servo_enable=0, open_loop=0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    p.add_argument("--amps", type=float, required=True, help="step target")
    p.add_argument("--open-loop", action="store_true")
    p.add_argument("-o", "--out", default="step.csv")
    args = p.parse_args()
    ch = load_channel(args.channel)
    try:
        sp_counts = command_counts(ch, args.amps, args.open_loop)
    except ValueError as exc:
        raise SystemExit(f"--amps: {exc}") from None
    if args.open_loop:
        print(f"open loop: OUT1 command {sp_counts} counts (model estimate "
              f"for {args.amps} A)")

    with Board(ch["host"], max_clamp=ch["cfg"]["out_clamp"]) as b:
        prepare(b, ch, args.open_loop)
        b.warn_flags()
        b.wait_for_run()
        time.sleep(0.05)
        raw = capture_step(b, sp_counts)
        finish(b)

    cols = decimated_columns(decode_words(raw, decimated=True), ch["i_fs"])
    write_csv(args.out, cols)
    n = len(cols["t_s"])
    print(f"wrote {args.out} ({n} samples, {n / FS_DEC * 1e3:.1f} ms)")


if __name__ == "__main__":
    main()
