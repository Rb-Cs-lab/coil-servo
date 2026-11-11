"""Web dashboard: live monitoring of every servo board for anyone on the lab
LAN, manual control from the lab PC only. Start it with

    python -m coil_servo_host.dashboard            # real boards
    python -m coil_servo_host.dashboard --simulate # model-driven, no hardware

and see docs/dashboard.md.
"""

from pathlib import Path

from ..board import Board
from ..config import (CONFIG_PATH, build_model, channel_names, channel_table,
                      load_channel)
from ..simboard import SimBoard
from coil_servo_model.registers import MAP_REV
from .servo_channel import CoilServoChannel

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOGDIR = REPO_ROOT / "logs"


def build_channels(names=None, *, config_path=CONFIG_PATH, simulate=(),
                   sim_old=(), logdir=DEFAULT_LOGDIR, action_log=None,
                   **kwargs) -> dict:
    """One CoilServoChannel per channels.toml entry (or the given names).

    simulate: names to run on a SimBoard instead of hardware ("*" = all);
    sim_old: simulated names whose bitstream reports the old map (rev 0).
    A profile the loader refuses is still shown (monitoring only, with the
    loader's message), as long as its physics can be built.
    """
    config_path = Path(config_path)
    names = list(names) if names else channel_names(config_path)
    out = {}
    for name in names:
        table = channel_table(name, config_path)
        profile = err = model = None
        try:
            profile = load_channel(name, config_path)
        except (KeyError, ValueError, TypeError) as exc:
            err = str(exc) if not isinstance(exc, KeyError) else \
                f"missing key {exc}"
            try:
                model = build_model(name, table)
            except (KeyError, ValueError, TypeError):
                model = None
        sim = "*" in simulate or name in simulate
        out[name] = CoilServoChannel(
            name, table=table, profile=profile, profile_error=err,
            model=model, connect=_connector(name, table, profile, model, sim,
                                            name in sim_old),
            simulated=sim, logdir=logdir, action_log=action_log,
            config_path=config_path, **kwargs)
    return out


def _connector(name, table, profile, model, sim: bool, old: bool):
    clamp = profile["cfg"]["out_clamp"] if profile else None
    if sim:
        ch = profile or {"model": model, "i_fs": model.I_FS,
                         "i_rated": model.I_rated, "cfg": {}}
        return lambda: SimBoard(ch, map_rev=0 if old else MAP_REV,
                                max_clamp=clamp)
    host = table.get("board_host")
    return lambda: Board(host, timeout=2.0, max_clamp=clamp)
