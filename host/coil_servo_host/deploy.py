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

from .config import load_channel

SERVER_SRC = Path(__file__).resolve().parents[1] / "board" / "coil_servo_server.py"


def run(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("channel", help="channel name from channels.toml")
    p.add_argument("--bitstream", default="coil_servo.bit.bin",
                   help="byte-swapped bitstream (make coil_servo.bit.bin)")
    p.add_argument("--port", type=int, default=9001)
    args = p.parse_args()

    host = load_channel(args.channel)["host"]
    target = f"root@{host}"
    bit = Path(args.bitstream)
    if not bit.exists():
        sys.exit(f"{bit} not found -- build it on the Vivado machine first")

    run(["scp", str(bit), str(SERVER_SRC), f"{target}:/root/"])
    run(["ssh", target, f"fpgautil -b /root/{bit.name}"])
    run(["ssh", target,
         "pkill -f coil_servo_server.py; "
         f"nohup python3 /root/coil_servo_server.py --port {args.port} "
         ">/dev/null 2>&1 & sleep 0.5"])
    print(f"deployed to {host}; verify with: python -m coil_servo_host.check "
          f"{args.channel}")


if __name__ == "__main__":
    main()
