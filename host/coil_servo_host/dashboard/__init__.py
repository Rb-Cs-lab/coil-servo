"""Web dashboard: live monitoring of every servo board for anyone on the lab
LAN, manual control from the lab PC only. Start it with

    python -m coil_servo_host.dashboard            # real boards
    python -m coil_servo_host.dashboard --simulate # model-driven, no hardware

and see docs/dashboard.md.

Structure: a *device* is one board (one connection, one poll thread); a
*card* is a view of a device with one channels.toml profile. The cards
come from host/config/dashboard.toml; profiles that share a board_host
share one device.
"""

import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from ..board import Board
from ..config import (CONFIG_PATH, build_model, channel_names, channel_table,
                      load_channel)
from ..simboard import SimBoard
from coil_servo_model.registers import MAP_REV
from .servo_channel import CoilServoChannel

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOGDIR = REPO_ROOT / "logs"
CARDS_PATH = CONFIG_PATH.parent / "dashboard.toml"
CARD_STYLES = ("", "bench")


@dataclass(frozen=True)
class Card:
    id: str
    title: str
    profile: str
    device: str
    style: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def load_cards(path: Path = CARDS_PATH, config_path: Path = CONFIG_PATH):
    """[(id, title, profile, style)] from dashboard.toml; without the file,
    one card per channels.toml table (title = table name, bench profiles
    styled "bench")."""
    names = channel_names(config_path)
    if not Path(path).exists():
        return [(n, n, n, "bench" if channel_table(n, config_path)
                 .get("bench") else "") for n in names]
    with open(path, "rb") as f:
        raw = tomllib.load(f).get("card", [])
    out, seen = [], set()
    for c in raw:
        cid, prof = str(c["id"]), str(c["profile"])
        style = str(c.get("style", ""))
        if cid in seen:
            raise ValueError(f"{path}: duplicate card id {cid!r}")
        if prof not in names:
            raise ValueError(f"{path}: card {cid!r} names profile [{prof}], "
                             f"which is not in {config_path.name}")
        if style not in CARD_STYLES:
            raise ValueError(f"{path}: card {cid!r} style {style!r} not one "
                             f"of {CARD_STYLES}")
        seen.add(cid)
        out.append((cid, str(c.get("title", cid)), prof, style))
    return out


def build_dashboard(only=None, *, config_path=CONFIG_PATH,
                    cards_path=CARDS_PATH, simulate=(), sim_old=(),
                    logdir=DEFAULT_LOGDIR, action_log=None,
                    sim_sensor_offset=(0.0, 0.0),
                    **kwargs) -> tuple[dict, list]:
    """(devices, cards). One CoilServoChannel per board_host; its profiles
    are every channels.toml table with that board_host (file order).

    only: card ids or profile names to show (default: all cards).
    simulate: card ids / profile names / board names to run on a SimBoard
    ("*" = all); sim_old: the same, for a simulated bitstream that reports
    the old map (rev 0). sim_sensor_offset: (IN1, IN2) amps the simulated
    sensors read with nothing flowing (for the zero calibration). A profile
    the loader refuses is still shown (monitoring only, with the loader's
    message).
    """
    config_path = Path(config_path)
    specs = load_cards(cards_path, config_path)
    if only:
        only = set(only)
        specs = [s for s in specs if s[0] in only or s[2] in only]
        missing = only - {s[0] for s in specs} - {s[2] for s in specs}
        if missing:
            raise ValueError(f"no card or profile named {sorted(missing)}")

    tables = {n: channel_table(n, config_path)
              for n in channel_names(config_path)}
    hosts = []                                 # board_hosts in use, ordered
    for _, _, prof, _ in specs:
        h = tables[prof].get("board_host")
        if h not in hosts:
            hosts.append(h)

    devices, by_host = {}, {}
    for host in hosts:
        names = [n for n, t in tables.items() if t.get("board_host") == host]
        profiles, errors, models = {}, {}, {}
        for n in names:
            try:
                profiles[n] = load_channel(n, config_path)
            except (KeyError, ValueError, TypeError) as exc:
                profiles[n] = None
                errors[n] = (f"missing key {exc}" if isinstance(exc, KeyError)
                             else str(exc))
                try:
                    models[n] = build_model(n, tables[n])
                except (KeyError, ValueError, TypeError):
                    pass
        dev_name = names[0]
        keys = {dev_name, *names} | {s[0] for s in specs
                                     if tables[s[2]].get("board_host") == host}
        sim = "*" in simulate or bool(keys & set(simulate))
        old = bool(keys & set(sim_old))
        dev = CoilServoChannel(
            dev_name, host=host, profiles=profiles, profile_errors=errors,
            models=models,
            connect=_connector(host, profiles, models, sim, old,
                               sim_sensor_offset),
            simulated=sim, logdir=logdir, action_log=action_log,
            config_path=config_path, **kwargs)
        devices[dev_name] = dev
        by_host[host] = dev_name

    cards = [Card(cid, title, prof, by_host[tables[prof].get("board_host")],
                  style) for cid, title, prof, style in specs]
    return devices, cards


def _connector(host, profiles: dict, models: dict, sim: bool, old: bool,
               sim_sensor_offset=(0.0, 0.0)):
    good = [p for p in profiles.values() if p is not None]
    # the guard against typing a clamp above rated: the largest clamp of
    # this board's profiles (each profile's own clamp is in its cfg)
    clamp = max((p["cfg"]["out_clamp"] for p in good), default=None)
    if sim:
        if good:
            ch, variants = good[0], good[1:]
        else:
            m = next(iter(models.values()))
            ch = {"model": m, "i_fs": m.I_FS, "i_rated": m.I_rated,
                  "cfg": {}}
            variants = ()
        return lambda: SimBoard(ch, map_rev=0 if old else MAP_REV,
                                max_clamp=clamp, variants=variants,
                                sensor_offset_amps=sim_sensor_offset)
    return lambda: Board(host, timeout=2.0, max_clamp=clamp)
