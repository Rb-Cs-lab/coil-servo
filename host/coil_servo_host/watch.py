"""Live status display: measured current, state, and flags, a few times a
second until Ctrl-C. Read-only and safe to leave running.

    python -m coil_servo_host.watch mot
"""

import argparse
import time

from .board import Board
from .config import i_meas_amps, load_channel


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    p.add_argument("--interval", type=float, default=0.2, help="seconds")
    args = p.parse_args()
    ch = load_channel(args.channel)

    with Board(ch["host"], max_clamp=ch["cfg"]["out_clamp"]) as b:
        try:
            while True:
                s = b.sts()
                amps = i_meas_amps(s["i_meas"], ch["i_fs"])
                flags = " ".join(name for name in
                                 ("tripped", "out_sat", "u_floor",
                                  "sp_sign_mismatch", "timeout_hold",
                                  "fault") if s[name])
                print(f"\r{amps:+9.3f} A  {s['fsm_state_name']:<12s} "
                      f"armed={s['armed']} bridge={s['bridge_en']} "
                      f"pol={s['polarity']}  {flags:<30s}",
                      end="", flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
