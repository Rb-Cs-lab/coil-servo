"""Coil-servo web dashboard.

    python -m coil_servo_host.dashboard                 # all channels
    python -m coil_servo_host.dashboard --channels mot  # just the MOT
    python -m coil_servo_host.dashboard --simulate      # no hardware needed

Then open http://localhost:8080 on the lab PC (control works only there),
or http://<lab-pc-ip>:8080 from any other machine on the lab LAN (viewing
only). See docs/dashboard.md.
"""

import argparse
import socket
from pathlib import Path

from ..config import CONFIG_PATH
from . import DEFAULT_LOGDIR, build_channels
from .access import DEFAULT_ALLOW, AccessPolicy
from .logs import ActionLog


def _split(text):
    return [s.strip() for s in text.split(",") if s.strip()] if text else []


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--bind", default="0.0.0.0",
                   help="address to listen on (default 0.0.0.0: every "
                        "network interface, so the LAN can view; "
                        "127.0.0.1 = this PC only)")
    p.add_argument("--channels", default="",
                   help="comma-separated channel names (default: every "
                        "channel in the config file)")
    p.add_argument("--config", type=Path, default=CONFIG_PATH,
                   help="channels.toml to use")
    p.add_argument("--simulate", nargs="?", const="*", default="",
                   metavar="CHANNELS",
                   help="run on the model instead of hardware: all "
                        "channels, or the comma-separated ones given")
    p.add_argument("--sim-old-bitstream", default="", metavar="CHANNELS",
                   help="simulated channels whose bitstream reports the old "
                        "register map (to see the 'old bitstream' display)")
    p.add_argument("--poll", type=float, default=0.1,
                   help="status poll interval in seconds (default 0.1 = "
                        "10 Hz)")
    p.add_argument("--history", type=float, default=600,
                   help="seconds of history kept in memory (default 600)")
    p.add_argument("--logdir", type=Path, default=DEFAULT_LOGDIR,
                   help=f"where logs and captures go (default {DEFAULT_LOGDIR})")
    p.add_argument("--log-every", type=int, default=1,
                   help="write every Nth polled status to the CSV log "
                        "(default 1 = all; ~70 MB per channel per day at "
                        "10 Hz)")
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

    import uvicorn                       # only needed to serve

    from .app import create_app

    action_log = ActionLog(args.logdir)
    devices = build_channels(
        _split(args.channels) or None, config_path=args.config,
        simulate=_split(args.simulate) if args.simulate != "*" else ["*"],
        sim_old=_split(args.sim_old_bitstream), logdir=args.logdir,
        action_log=action_log, poll_interval=args.poll,
        history_s=args.history, log_every=args.log_every,
        enable_timeout=args.enable_timeout)
    policy = AccessPolicy(allow=tuple(_split(args.allow)),
                          remote_capture=args.allow_remote_capture,
                          remote_stop=args.allow_remote_stop)
    app = create_app(devices, policy, action_log)

    print(f"coil-servo dashboard: {', '.join(devices)}"
          f"{' (SIMULATED)' if args.simulate else ''}")
    print(f"  on this PC (control):   http://localhost:{args.port}")
    if args.bind not in ("127.0.0.1", "localhost"):
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "<lab-pc-ip>"
        print(f"  from the lab LAN (view): http://{ip}:{args.port}  "
              f"(ipconfig shows the wired adapter's IPv4 address)")
    print(f"  logs and captures:      {args.logdir}")
    print("Ctrl-C stops the dashboard (the servo boards keep running).")
    uvicorn.run(app, host=args.bind, port=args.port, proxy_headers=False,
                log_level="warning")


if __name__ == "__main__":
    main()
