"""Post-deploy sanity check (safe: never enables the servo).

    python -m coil_servo_host.check mot

Verifies the register link, that the fabric is alive (heartbeat counter
advancing), walks the LEDs, applies the channel configuration, and dumps
the status flags.
"""

import argparse
import time

from .board import Board
from .config import load_channel


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    args = p.parse_args()
    ch = load_channel(args.channel)

    with Board(ch["host"], max_clamp=ch["cfg"]["out_clamp"]) as b:
        s0 = b.sts()
        time.sleep(0.05)
        s1 = b.sts()
        alive = s1["heartbeat"] != s0["heartbeat"]
        print(f"fabric heartbeat: {'ADVANCING' if alive else '*** STUCK ***'} "
              f"({s0['heartbeat']} -> {s1['heartbeat']})")

        for k in range(8):
            b.write_cfg(led=1 << k)
            time.sleep(0.05)
        b.write_cfg(led=0)
        print("LED walk done (watch the board)")

        b.apply_config(ch["cfg"])
        s = b.sts()
        print(f"state={s['fsm_state_name']} armed={s['armed']} "
              f"fault={s['fault']} bridge_en={s['bridge_en']} "
              f"polarity={s['polarity']}")
        amps = s["i_meas"] / 128 * ch["i_fs"] / 8192
        print(f"measured current: {amps:+.3f} A "
              f"(should be ~0 with the servo disabled)")
        if s["fault"]:
            print("*** interlock fault flag is SET ***")


if __name__ == "__main__":
    main()
