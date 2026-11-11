"""Live status display: measured current, state, and flags, a few times a
second until Ctrl-C. Read-only and safe to leave running.

    python -m coil_servo_host.watch mot

Also reads the board's settings every few seconds and says which
channels.toml profile they match (bench or production); it warns loudly if
that is not the channel this tool was started with.
"""

import argparse
import time

from .board import Board
from .config import (announce, i_meas_amps, load_channel,
                     profile_report)

PROFILE_EVERY_S = 3.0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    p.add_argument("--interval", type=float, default=0.2, help="seconds")
    args = p.parse_args()
    ch = load_channel(args.channel)
    announce(ch)

    with Board(ch["host"], max_clamp=ch["cfg"]["out_clamp"]) as b:
        last_text, next_check = None, 0.0
        try:
            while True:
                if time.monotonic() >= next_check:
                    ok, text = profile_report(b.read_cfg(), ch)
                    if text != last_text:       # print on start and on change
                        print(f"\n{text}")
                        last_text = text
                    next_check = time.monotonic() + PROFILE_EVERY_S
                s = b.sts()
                amps = i_meas_amps(s["i_meas"], ch["i_fs"])
                flags = " ".join(name for name in
                                 ("tripped", "out_sat", "u_floor",
                                  "sp_sign_mismatch", "timeout_hold",
                                  "fault") if s[name])
                tag = "" if ok else "  PROFILE?"
                print(f"\r{amps:+9.3f} A  {s['fsm_state_name']:<12s} "
                      f"armed={s['armed']} bridge={s['bridge_en']} "
                      f"pol={s['polarity']}  {flags:<30s}{tag}",
                      end="", flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
