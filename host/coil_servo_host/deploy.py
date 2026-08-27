"""Deploy to a board over the wired network: copy the bitstream and the
register server, load the FPGA, start the server.

    python -m coil_servo_host.deploy mot --bitstream coil_servo.bit.bin

Uses your ssh/scp (set up key auth once with `ssh-copy-id root@<board>`).
The board must run the official Red Pitaya OS 3.x and be on the wired
Ethernet lab network.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .board import Board
from .config import load_channel

SERVER_SRC = Path(__file__).resolve().parents[1] / "board" / "coil_servo_server.py"

# decimated counts (/128) of measured current above which we consider
# current to be flowing (~1% of full scale)
I_QUIET_COUNTS = 64


def refuse_if_running(host: str, port: int, force: bool) -> None:
    """Loading a bitstream reprograms the FPGA instantly, dropping every
    output with no graceful stop -- at nonzero current that dumps the
    coil's stored energy into the bridge body diodes. So: if a register
    server is already answering, refuse to reload while the bridge is
    enabled or current is flowing. No server answering = fresh board,
    proceed."""
    try:
        b = Board(host, port=port, timeout=2.0)
    except OSError:
        return
    try:
        s = b.sts()
    except OSError:
        msg = (f"a register server on {host} accepted the connection but "
               f"didn't answer -- can't verify the servo is stopped. "
               f"Close other host tools (watch?) and retry, or --force.")
        if force:
            print("--force given; overriding: " + msg)
            return
        raise SystemExit(msg)
    finally:
        b.close()
    quiet = abs(s["i_meas"]) // 128 <= I_QUIET_COUNTS
    if s["bridge_en"] or not quiet:
        msg = (f"REFUSING to reload: the servo on {host} looks ACTIVE "
               f"(bridge_en={s['bridge_en']}, "
               f"i_meas={s['i_meas'] / 128:.0f} counts, "
               f"state={s['fsm_state_name']}). Stop it first "
               f"(servo_enable=0 performs a graceful ramp-to-zero). "
               f"If these readings are garbage because the board is "
               f"running a stale/foreign bitstream, re-run with --force.")
        if force:
            print("--force given; overriding:\n" + msg)
            return
        raise SystemExit(msg)


def run(cmd, check=True):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=check)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel", help="channel name from channels.toml")
    p.add_argument("--bitstream", default="coil_servo.bit.bin",
                   help="byte-swapped bitstream (make coil_servo.bit.bin)")
    p.add_argument("--port", type=int, default=9001)
    p.add_argument("--force", action="store_true",
                   help="reload even if the servo looks active (see "
                        "refuse_if_running)")
    args = p.parse_args()

    host = load_channel(args.channel)["host"]
    target = f"root@{host}"
    bit = Path(args.bitstream)
    if not bit.exists():
        sys.exit(f"{bit} not found -- build it on the Vivado machine first")

    refuse_if_running(host, args.port, args.force)

    run(["scp", str(bit), str(SERVER_SRC), f"{target}:/root/"])
    # non-interactive ssh doesn't load the profile that puts Red Pitaya's
    # tools on PATH, so name fpgautil's directory explicitly
    run(["ssh", target,
         f"PATH=/opt/redpitaya/bin:$PATH fpgautil -b /root/{bit.name}"])
    # Kill any old server in its OWN ssh call with failures tolerated:
    # pkill's pattern unavoidably matches this ssh shell's command line
    # too, so this call always ends with a dropped connection -- after
    # having signalled the old server, which is all we need. The server
    # start then runs in a separate call nothing is killing.
    run(["ssh", target, "pkill -f coil_servo_server.py"], check=False)
    run(["ssh", target,
         f"nohup python3 /root/coil_servo_server.py --port {args.port} "
         ">/dev/null 2>&1 & sleep 0.5"])
    print(f"deployed to {host}; verify with: python -m coil_servo_host.check "
          f"{args.channel}")


if __name__ == "__main__":
    main()
