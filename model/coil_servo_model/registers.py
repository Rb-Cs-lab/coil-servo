"""CFG/STS register layout -- Python mirror of docs/register_map.md.

Used by the integration testbench (driving coil_servo_top.cfg_data directly)
and by the host tools (writing 32-bit words to axi_hub). If this file and
docs/register_map.md disagree, the doc wins; fix this file.
"""

# name: (word, lsb_within_word, width, signed)
CFG_FIELDS = {
    "servo_enable":   (0, 0, 1, False),
    "int_clear":      (0, 1, 1, False),
    "sp_source":      (0, 2, 1, False),
    "fifo_rst":       (0, 3, 1, False),
    "out2_invert":    (0, 4, 1, False),
    "boost_mode":     (0, 5, 1, False),
    "boost_manual":   (0, 6, 1, False),
    "flip_fault_ack": (0, 7, 1, False),
    "led":            (0, 8, 8, False),
    "open_loop":      (0, 16, 1, False),
    "capture_sel":    (0, 17, 1, False),
    "setpoint":       (1, 0, 14, True),
    "kp_mant":        (2, 0, 18, True),
    "kp_shift":       (3, 0, 5, False),
    "ki_mant":        (4, 0, 18, True),
    "ki_shift":       (5, 0, 6, False),
    "out_clamp":      (6, 0, 14, False),
    "deadband":       (7, 0, 14, False),
    "zero_win":       (8, 0, 14, False),
    "zero_holdoff":   (9, 0, 16, False),
    "deadtime":       (10, 0, 16, False),
    "settle":         (11, 0, 32, False),
    "flip_timeout":   (12, 0, 32, False),
    "dio_invert":     (13, 0, 3, False),
}

STS_FIELDS = {
    "fsm_state":        (0, 0, 4, False),
    "fault":            (0, 4, 1, False),
    "bridge_en":        (0, 5, 1, False),
    "polarity":         (0, 6, 1, False),
    "armed":            (0, 7, 1, False),
    "out_sat":          (0, 8, 1, False),
    "int_railed":       (0, 9, 1, False),
    "sp_sign_mismatch": (0, 10, 1, False),
    "timeout_hold":     (0, 11, 1, False),
    "i_meas":           (1, 0, 22, True),
    "sp_active":        (2, 0, 14, True),
    "u14":              (3, 0, 14, True),
    "heartbeat":        (4, 0, 32, False),
    "fifo_count":       (5, 0, 32, False),
}

FSM_STATES = ["IDLE", "RUN", "RAMP_DOWN", "DISABLE", "FLIP", "ENABLE",
              "SETTLE", "TIMEOUT_HOLD"]

CFG_BASE = 0x4000_0000
STS_BASE = 0x4100_0000


def _encode(value: int, width: int, signed: bool) -> int:
    lo = -(1 << (width - 1)) if signed else 0
    hi = (1 << (width - 1)) - 1 if signed else (1 << width) - 1
    if not lo <= value <= hi:
        raise ValueError(f"value {value} does not fit {'s' if signed else 'u'}{width}")
    return value & ((1 << width) - 1)


def pack_cfg(**fields) -> int:
    """Build the full CFG word (512-bit int) from named fields; unnamed
    fields are zero. Used to drive cfg_data in simulation."""
    word = 0
    for name, value in fields.items():
        w, lsb, width, signed = CFG_FIELDS[name]
        word |= _encode(value, width, signed) << (32 * w + lsb)
    return word


def cfg_words(**fields) -> dict:
    """The same fields as {word_index: 32-bit value} for host register
    writes at CFG_BASE + 4*word_index."""
    packed = pack_cfg(**fields)
    words = {}
    for name in fields:
        w = CFG_FIELDS[name][0]
        words[w] = (packed >> (32 * w)) & 0xFFFF_FFFF
    return words


def parse_sts(sts: int) -> dict:
    """Decode the STS block (int of any width >= the fields used) into
    named values, sign-extending the signed fields."""
    out = {}
    for name, (w, lsb, width, signed) in STS_FIELDS.items():
        raw = (sts >> (32 * w + lsb)) & ((1 << width) - 1)
        if signed and raw & (1 << (width - 1)):
            raw -= 1 << width
        out[name] = raw
    out["fsm_state_name"] = FSM_STATES[out["fsm_state"] & 0x7]
    return out
