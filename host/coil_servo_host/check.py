"""Post-deploy sanity check (safe: never enables the servo).

    python -m coil_servo_host.check mot

Prints what the configuration derives (sensor scale, pass-bank gain, dead
zone, floor, clamp, trip, ramps), verifies the register link, that the
fabric is alive (heartbeat counter advancing), says which channels.toml
profile the board held (bench or production), walks the LEDs, applies the
channel configuration (LED 7 lit for a bench profile), reads it back, and
dumps the status flags.
"""

import argparse
import time

from .board import Board
from .config import announce, load_channel, profile_report, summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel")
    args = p.parse_args()
    ch = load_channel(args.channel)
    announce(ch)
    print(summary(ch))
    print()

    try:
        b = Board(ch["host"], max_clamp=ch["cfg"]["out_clamp"])
    except ConnectionError as exc:
        raise SystemExit(f"*** {exc}") from None

    with b:
        s0 = b.sts()
        time.sleep(0.05)
        s1 = b.sts()
        alive = s1["heartbeat"] != s0["heartbeat"]
        print(f"fabric heartbeat: {'ADVANCING' if alive else '*** STUCK ***'} "
              f"({s0['heartbeat']} -> {s1['heartbeat']})")

        # which profile did the board hold before this check?
        _, text = profile_report(b.read_cfg(), ch)
        print(f"before this check: {text}")

        for k in range(8):
            b.write_cfg(led=1 << k)
            time.sleep(0.05)
        b.write_cfg(led=ch["cfg"]["led"])      # back to the profile's pattern
        print("LED walk done (watch the board)")

        b.apply_config(ch["cfg"])
        rb = b.read_cfg()
        ok, text = profile_report(rb, ch)
        print(f"after this check:  {text}")
        bad = {k: (v, rb[k]) for k, v in ch["cfg"].items() if rb[k] != v}
        if bad:
            print(f"*** CFG readback mismatch (wrote, read): {bad} -- stale "
                  f"bitstream? The register map changed in MVP rewrite "
                  f"step 3; redeploy the current coil_servo.bit.bin ***")
        else:
            print("configuration written and read back OK")
        print(f"OUT2 enable={rb['out2_enable']}  boost_mode={rb['boost_mode']}"
              f"  boost_manual={rb['boost_manual']}  (all must be 0 in the MVP)"
              f"  dither={rb['dither_enable']}")

        s = b.warn_flags()
        print(f"state={s['fsm_state_name']} armed={s['armed']} "
              f"bridge_en={s['bridge_en']} polarity={s['polarity']} "
              f"tripped={s['tripped']} u_floor={s['u_floor']} "
              f"fault(DIO5, unwired)={s['fault']}")
        amps = s["i_meas"] / 128 * ch["i_fs"] / 8192
        print(f"measured current: {amps:+.3f} A "
              f"(should be ~0 with the servo disabled; the offset is the "
              f"IN1 calibration)")


if __name__ == "__main__":
    main()
