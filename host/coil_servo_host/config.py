"""Load host/config/channels.toml, build the physics model from it, and
convert physical units into register values (amps -> counts, us -> 8 ns
ticks, A/ms -> slew codes, float gains -> mantissa + shift).

    python -m coil_servo_host.config mot     # print what gets derived

The physics block of each channel overrides the defaults in
model/coil_servo_model/channels.py; I_FS, the OUT1 command clamp and the
dead-zone floor are DERIVED from that model (so a new bench measurement in
channels.toml flows straight into the registers), unless overridden by a
directly measured value (out_clamp_counts, u_floor_counts).

mot_bench is the MOT channel's low-current test profile (bench supply with a
current limit, LEM wound N times; docs/bench_supply_test.md). Its supply
limit I_supply_max adds checks: rated current and trip level below it.
"""

import argparse
import tomllib
from dataclasses import replace
from pathlib import Path

from coil_servo_model.channels import BENCH_CHANNELS, CHANNELS, Channel
from coil_servo_model.fixed_point import encode_gain, slew_rate_code
from coil_servo_model.loop import T_S

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "channels.toml"
TICK_NS = 8.0

# fixed requirement (CLAUDE.md): MOT rail <= 6 V, shim rails <= 10 V.
# mot_bench is the MOT coil on a bench supply: same limit.
RAIL_LIMIT = {"mot": 6.0, "mot_bench": 6.0}
RAIL_LIMIT_DEFAULT = 10.0

# channels.toml keys that override model Channel fields
PHYSICS_KEYS = ("I_rated", "V_rail", "V_th", "g_fs", "gain_opamp",
                "out1_terminated", "R_burden", "G_inamp", "lem_turns",
                "I_supply_max")


def amps_to_counts(amps: float, i_fs: float) -> int:
    """Amps -> counts of the IN1 sensor scale (1 count = I_FS/8192)."""
    return round(amps / i_fs * 8192)


def build_model(name: str, table: dict) -> Channel:
    """The model Channel for this channel, with channels.toml's physics."""
    base = (CHANNELS.get(name) or BENCH_CHANNELS.get(name)
            or Channel(name=name, L=16e-6, R_coil=6.4e-3,
                       I_rated=float(table["I_rated"])))
    over = {k: table[k] for k in PHYSICS_KEYS if k in table}
    if "V_th" in over:
        over["V_th"] = tuple(float(v) for v in over["V_th"])
    for k in ("I_rated", "V_rail", "g_fs", "gain_opamp", "R_burden", "G_inamp",
              "I_supply_max"):
        if k in over:
            over[k] = float(over[k])
    if "out1_terminated" in over:
        over["out1_terminated"] = bool(over["out1_terminated"])
    if "lem_turns" in over:
        n = over["lem_turns"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"[{name}] lem_turns = {n!r} must be a whole "
                             f"number of turns >= 1")
    # the clamp override lives in this file only: without the key the clamp
    # is derived from the model (I_rated), whatever the model's default
    over["out_clamp_counts"] = (int(table["out_clamp_counts"])
                                if "out_clamp_counts" in table else None)
    return replace(base, name=name, **over)


def load_channel(name: str, path: Path = CONFIG_PATH) -> dict:
    """Returns {"host", "i_fs", "i_rated", "model", "cfg"}.

    cfg is ready for Board.apply_config(); it does NOT include
    servo_enable -- enabling is always an explicit, separate call.
    """
    with open(path, "rb") as f:
        table = tomllib.load(f)[name]

    model = build_model(name, table)
    i_fs = model.I_FS
    i_rated = model.I_rated

    # --- sanity rails on values that would silently defeat safety ---
    def refuse(msg):
        raise ValueError(f"[{name}] {msg}")

    rail_max = RAIL_LIMIT.get(name, RAIL_LIMIT_DEFAULT)
    if not 0 < model.V_rail <= rail_max:
        refuse(f"V_rail = {model.V_rail} V is outside 0 .. {rail_max} V "
               f"(fixed limit for this coil)")
    if not 0 < i_rated < i_fs:
        refuse(f"needs 0 < I_rated < I_FS (I_rated={i_rated}, I_FS={i_fs:g}: "
               f"rated current must be measurable on IN1)")
    trip_amps = float(table["trip_amps"])
    if not 0 < trip_amps < i_fs:
        refuse(f"trip_amps = {trip_amps} must be inside 0 .. I_FS = {i_fs:g} A")
    i_sup = model.I_supply_max
    if i_sup is not None:
        # current-limited (bench) supply: its CC limit is the hard limit, and
        # the firmware trip must sit below it -- otherwise the supply holds
        # the current at its limit, the trip never fires, and the PI winds up
        # to the clamp (slow recovery). Rated must sit below both.
        if i_sup <= 0:
            refuse(f"I_supply_max = {i_sup} A must be > 0")
        if not i_rated < i_sup:
            refuse(f"I_rated = {i_rated} A must be below the supply current "
                   f"limit I_supply_max = {i_sup} A")
        if not trip_amps < i_sup:
            refuse(f"trip_amps = {trip_amps} A must be below the supply current "
                   f"limit I_supply_max = {i_sup} A, or the firmware trip can "
                   f"never fire (the supply holds the current at its limit)")
    if float(table["zero_win_amps"]) <= 0:
        refuse("zero_win_amps must be > 0, or a flip can never qualify its "
               "zero-current window")
    if float(table["flip_timeout_us"]) <= 0:
        refuse("flip_timeout_us must be > 0: the timeout is the safety net "
               "that parks an unreachable flip in TIMEOUT_HOLD "
               "(see docs/register_map.md)")
    if float(table["trip_timeout_us"]) < 0:
        refuse("trip_timeout_us must be >= 0")
    for k in ("slew_up_amps_per_ms", "slew_down_amps_per_ms"):
        if float(table[k]) <= 0:
            refuse(f"{k} must be > 0 (0 freezes the setpoint)")
    for k in ("out2_enable", "boost_mode"):
        if int(table[k]) != 0:
            refuse(f"{k} must be 0: OUT2 / the boost output are not fitted "
                   f"in the MVP")

    # --- derived register values ---
    out_clamp = model.clamp_counts     # out_clamp_counts if set, else derived
    if "u_floor_counts" in table:
        u_floor = int(table["u_floor_counts"])
    else:
        u_floor = round(float(table["floor_frac"]) * model.dead_zone * 8192)
    if not 0 < out_clamp <= 8191:
        refuse(f"OUT1 clamp {out_clamp} counts outside 1 .. 8191")
    if not 0 <= u_floor < out_clamp:
        refuse(f"floor {u_floor} counts must be below the clamp {out_clamp}")

    kp_mant, kp_shift = encode_gain(float(table["kp"]), shift_bits=5)
    ki_mant, ki_shift = encode_gain(float(table["ki"]) * T_S, shift_bits=6)

    def ticks(us: float) -> int:
        return round(us * 1000.0 / TICK_NS)

    cfg = dict(
        kp_mant=kp_mant, kp_shift=kp_shift,
        ki_mant=ki_mant, ki_shift=ki_shift,
        out_clamp=out_clamp,
        u_floor=u_floor,
        deadband=int(table["deadband_counts"]),
        zero_win=amps_to_counts(float(table["zero_win_amps"]), i_fs),
        zero_holdoff=int(table["zero_holdoff_ticks"]),
        deadtime=ticks(float(table["deadtime_us"])),
        settle=ticks(float(table["settle_us"])),
        flip_timeout=ticks(float(table["flip_timeout_us"])),
        trip_level=amps_to_counts(trip_amps, i_fs),
        trip_ticks=int(table["trip_ticks"]),
        trip_timeout=ticks(float(table["trip_timeout_us"])),
        slew_up=slew_rate_code(float(table["slew_up_amps_per_ms"]) * 1e3, i_fs),
        slew_down=slew_rate_code(float(table["slew_down_amps_per_ms"]) * 1e3, i_fs),
        dio_invert=int(table["dio_invert"]),
        out2_invert=int(table["out2_invert"]),
        out2_enable=int(table["out2_enable"]),
        boost_mode=int(table["boost_mode"]),
        dither_enable=int(table["dither_enable"]),
    )
    return {"host": table["board_host"], "i_fs": i_fs, "i_rated": i_rated,
            "model": model, "cfg": cfg}


def summary(ch: dict) -> str:
    """Human-readable account of the derived quantities."""
    m, c = ch["model"], ch["cfg"]
    f = m.out1_factor                  # OUT1 volts per normalized unit

    def rate(code):                    # slew code -> A/ms
        return code / 2**14 * m.amps_per_lsb * 125e6 / 1e3

    turns = f", LEM {m.lem_turns} primary turns" if m.lem_turns != 1 else ""
    lines = [
        f"channel {m.name}: rail {m.V_rail:g} V, rated {m.I_rated:g} A",
    ]
    if m.I_supply_max is not None:
        lines.append(
            f"  supply limit       = {m.I_supply_max:g} A (current-limited "
            f"supply: the hard limit; the firmware trip sits below it)")
    lines += [
        f"  sensor scale I_FS  = {m.I_FS:g} A at +1 V on IN1 "
        f"({m.amps_per_lsb * 1e3:.2f} mA per ADC count{turns})",
        f"  pass bank G_pass   = {m.G_pass / f:.0f} A per volt at OUT1 "
        f"(OUT1 {'terminated' if m.out1_terminated else 'UNterminated'}; "
        f"plant gain {m.G_pass / m.I_FS:.2f}; one OUT1 count "
        f"~ {m.G_pass / 8192 * 1e3:.0f} mA)",
        f"  dead zone          = {m.dead_zone * f:.3f} V at OUT1 "
        f"({min(m.V_th):.2f} V gate, {m.dead_zone * 8192:.0f} counts)",
        f"  RUN floor          = {c['u_floor']} counts "
        f"({c['u_floor'] / 8192 * f:.3f} V at OUT1)",
        f"  OUT1 command clamp = {c['out_clamp']} counts "
        f"({c['out_clamp'] / 8192 * f:.3f} V at OUT1; limits the command, "
        f"not the current)",
        f"  over-current trip  = {c['trip_level'] * m.amps_per_lsb:.4g} A "
        f"({c['trip_level']} counts) for {c['trip_ticks']} samples",
        f"  flip zero window   = {c['zero_win'] * m.amps_per_lsb:.3g} A "
        f"({c['zero_win']} counts); freewheel from rated "
        f"~ {m.freewheel_time * 1e6:.0f} us",
        f"  setpoint ramps     = {rate(c['slew_up']):.0f} A/ms up, "
        f"{rate(c['slew_down']):.0f} A/ms down "
        f"(codes {c['slew_up']} / {c['slew_down']})",
        f"  dither             = {'on' if c['dither_enable'] else 'off'}",
    ]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("channel")
    args = p.parse_args()
    print(summary(load_channel(args.channel)))


if __name__ == "__main__":
    main()
