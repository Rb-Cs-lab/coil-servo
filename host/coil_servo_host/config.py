"""Load host/config/channels.toml and convert physical units into register
values (amps -> counts, us -> 8 ns ticks, float gains -> mantissa+shift).
"""

import tomllib
from pathlib import Path

from coil_servo_model.fixed_point import encode_gain
from coil_servo_model.loop import T_S

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "channels.toml"
TICK_NS = 8.0


def amps_to_counts(amps: float, i_fs: float) -> int:
    return round(amps / i_fs * 8192)


def load_channel(name: str, path: Path = CONFIG_PATH) -> dict:
    """Returns {"host": ..., "i_fs": ..., "cfg": {register fields}}.

    The cfg dict is ready for Board.apply_config(); it does NOT include
    servo_enable -- enabling is always an explicit, separate call.
    """
    with open(path, "rb") as f:
        table = tomllib.load(f)[name]

    i_fs = float(table["I_FS"])
    kp_mant, kp_shift = encode_gain(float(table["kp"]), shift_bits=5)
    ki_mant, ki_shift = encode_gain(float(table["ki"]) * T_S, shift_bits=6)

    def ticks(us: float) -> int:
        return round(us * 1000.0 / TICK_NS)

    cfg = dict(
        kp_mant=kp_mant, kp_shift=kp_shift,
        ki_mant=ki_mant, ki_shift=ki_shift,
        out_clamp=amps_to_counts(float(table["I_rated"]), i_fs),
        deadband=int(table["deadband_counts"]),
        zero_win=amps_to_counts(float(table["zero_win_amps"]), i_fs),
        zero_holdoff=int(table["zero_holdoff_ticks"]),
        deadtime=ticks(float(table["deadtime_us"])),
        settle=ticks(float(table["settle_us"])),
        flip_timeout=ticks(float(table["flip_timeout_us"])),
        dio_invert=int(table["dio_invert"]),
        out2_invert=int(table["out2_invert"]),
        boost_mode=int(table["boost_mode"]),
    )
    return {"host": table["board_host"], "i_fs": i_fs,
            "i_rated": float(table["I_rated"]), "cfg": cfg}
