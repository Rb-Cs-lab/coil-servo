"""Coil-servo web dashboard.

    python -m coil_servo_host.dashboard                 # all cards
    python -m coil_servo_host.dashboard --channels mot  # just the MOT
    python -m coil_servo_host.dashboard --simulate      # no hardware needed

Then open http://localhost:8081 on the lab PC (control works only there),
or http://<lab-pc-ip>:8081 from any other machine on the lab LAN (viewing
only); the start-up message prints the PC's address on the boards'
network. See docs/dashboard.md.
"""

import argparse
import ipaddress
import socket
import sys
from pathlib import Path

from ..config import CONFIG_PATH
from . import CARDS_PATH, DEFAULT_LOGDIR, build_dashboard
from .access import DEFAULT_ALLOW, AccessPolicy
from .logs import ActionLog


# 8080 is taken on the lab PCs (KoheronControl, the laser controller
# software, listens there)
DEFAULT_PORT = 8081


def _split(text):
    return [s.strip() for s in text.split(",") if s.strip()] if text else []


def lan_address(board_hosts) -> str | None:
    """This PC's own IPv4 address on the network the boards are on: the
    source address the operating system would use to reach the first board
    given as a numeric IP (no packet is sent). None if no board has a
    numeric address or there is no route."""
    for h in board_hosts:
        try:
            ipaddress.IPv4Address(h or "")
        except ValueError:
            continue                     # a name: skip, no DNS delays here
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((h, 9))
                return s.getsockname()[0]
        except OSError:
            continue
    return None


def local_addresses() -> list:
    """Every IPv4 address of this PC except loopback."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None,
                                   socket.AF_INET)
    except OSError:
        return []
    out = []
    for info in infos:
        a = info[4][0]
        if not a.startswith("127.") and a not in out:
            out.append(a)
    return out


def port_free(bind: str, port: int) -> bool:
    """True if nothing else is listening on bind:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((bind, port))
        except OSError:
            return False
    return True


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="web port (default %(default)s; 8080 is taken by "
                        "KoheronControl on the lab PCs)")
    p.add_argument("--bind", default="0.0.0.0",
                   help="address to listen on (default 0.0.0.0: every "
                        "network interface, so the LAN can view; "
                        "127.0.0.1 = this PC only)")
    p.add_argument("--channels", default="",
                   help="comma-separated card ids or profile names "
                        "(default: every card in dashboard.toml)")
    p.add_argument("--config", type=Path, default=CONFIG_PATH,
                   help="channels.toml to use")
    p.add_argument("--cards", type=Path, default=CARDS_PATH,
                   help="dashboard.toml: which cards the overview shows")
    p.add_argument("--simulate", nargs="?", const="*", default="",
                   metavar="CHANNELS",
                   help="run on the model instead of hardware: all "
                        "boards, or the comma-separated cards/profiles "
                        "given")
    p.add_argument("--sim-old-bitstream", default="", metavar="CHANNELS",
                   help="simulated cards/profiles whose bitstream "
                        "reports the old register map (to see that display)")
    p.add_argument("--poll", type=float, default=0.1,
                   help="status poll interval in seconds (default 0.1 = "
                        "10 Hz)")
    p.add_argument("--history", type=float, default=600,
                   help="seconds of history kept in memory (default 600)")
    p.add_argument("--logdir", type=Path, default=DEFAULT_LOGDIR,
                   help=f"where logs and captures go (default {DEFAULT_LOGDIR})")
    p.add_argument("--log-every", type=int, default=1,
                   help="write every Nth polled status to the CSV log "
                        "(default 1 = all). Only the last hour of status is "
                        "kept on disk (~3 MB per board at 10 Hz)")
    p.add_argument("--allow", default=",".join(DEFAULT_ALLOW),
                   help="addresses/networks allowed to control (default: "
                        "this PC only, %(default)s)")
    p.add_argument("--allow-remote-capture", action="store_true",
                   help="let every viewer start a capture")
    p.add_argument("--allow-remote-stop", action="store_true",
                   help="let every viewer press Stop")
    p.add_argument("--enable-timeout", type=float, default=30.0,
                   help="seconds to wait for the arm edge after Enable")
    args = p.parse_args(argv)

    if not port_free(args.bind, args.port):
        sys.exit(f"port {args.port} is already in use by another program on "
                 f"this PC (on the lab PCs 8080 is KoheronControl). Pick a "
                 f"free one, e.g. --port {args.port + 1}")

    import uvicorn                       # only needed to serve

    from .app import create_app

    action_log = ActionLog(args.logdir)
    devices, cards = build_dashboard(
        _split(args.channels) or None, config_path=args.config,
        cards_path=args.cards,
        simulate=_split(args.simulate) if args.simulate != "*" else ["*"],
        sim_old=_split(args.sim_old_bitstream), logdir=args.logdir,
        action_log=action_log, poll_interval=args.poll,
        history_s=args.history, log_every=args.log_every,
        enable_timeout=args.enable_timeout)
    policy = AccessPolicy(allow=tuple(_split(args.allow)),
                          remote_capture=args.allow_remote_capture,
                          remote_stop=args.allow_remote_stop)
    app = create_app(devices, policy, action_log, cards=cards)

    print(f"coil-servo dashboard: {', '.join(c.title for c in cards)}"
          f"{' (SIMULATED)' if args.simulate else ''}")
    print(f"  on this PC (control):   http://localhost:{args.port}")
    if args.bind not in ("127.0.0.1", "localhost"):
        lan = lan_address(d.host for d in devices.values())
        others = [a for a in local_addresses() if a != lan]
        if lan:
            print(f"  from the lab LAN (view): http://{lan}:{args.port}  "
                  f"(this PC's address on the boards' network)")
            if others:
                print(f"    not on the boards' network, don't use: "
                      f"{', '.join(others)}")
        else:
            print(f"  from the lab LAN (view): http://<this PC's wired "
                  f"address>:{args.port}  (ipconfig; candidates: "
                  f"{', '.join(others) or 'none found'})")
    print(f"  logs and captures:      {args.logdir}")
    print("Ctrl-C stops the dashboard (the servo boards keep running).")
    uvicorn.run(app, host=args.bind, port=args.port, proxy_headers=False,
                log_level="warning")


if __name__ == "__main__":
    main()
