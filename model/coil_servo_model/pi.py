"""Floating-point PI controller and output stage -- the trusted reference.

Mirrors the structure of design.md Node D/E/F exactly (same operation order,
same saturation points, same anti-windup rules), just with float arithmetic
and normalized units: 1.0 == I_FS == 1 V at the SMA. The fixed-point mirror
in fixed_point.py must agree with this to within quantization; the HDL must
agree with the fixed-point mirror exactly.

Per-tick convention: step() is called once per PI tick (T_s = 1.024 us);
ki_tick is the integral gain per tick (physical Ki [1/s] * T_s).
"""

# Q3.20 integer range in normalized units -- p, i, and u saturate here
# (saturation points 1-3 of design.md).
Q320_LIMIT = 8.0


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class FloatPI:
    def __init__(self, kp: float, ki_tick: float, clamp: float):
        """kp, ki_tick in normalized units; clamp in normalized volts (0..1),
        i.e. clamp_counts / 8192 (100% of rated current)."""
        self.kp = kp
        self.ki_tick = ki_tick
        self.clamp = clamp
        self.integrator = 0.0

    def clear(self):
        self.integrator = 0.0

    def step(self, e: float, hold: bool = False) -> float:
        """One PI tick. e = drive-frame error, normalized. Returns u after
        the hard output clamp (saturation point 4)."""
        p = _clip(self.kp * e, -Q320_LIMIT, Q320_LIMIT)          # sat #1
        i = self.integrator                                       # already sat #2
        u_raw = _clip(p + i, -Q320_LIMIT, Q320_LIMIT)             # sat #3
        u = _clip(u_raw, -self.clamp, self.clamp)                 # sat #4
        engaged = u != u_raw

        # Anti-windup: freeze on hold; skip accumulation while the output
        # clamp is engaged and the error would push further into it.
        if not hold and not (engaged and (e > 0) == (u_raw > 0)):
            self.integrator = _clip(self.integrator + self.ki_tick * e,
                                    -Q320_LIMIT, Q320_LIMIT)      # sat #2
        return u


def output_mux(u: float, deadband: float):
    """design.md Node F: sign-of-u handoff with deadband.

    Returns (v_out1, v_out2), both >= 0, never both nonzero.
    """
    if u > deadband:
        return u, 0.0
    if u < -deadband:
        return 0.0, -u
    return 0.0, 0.0
