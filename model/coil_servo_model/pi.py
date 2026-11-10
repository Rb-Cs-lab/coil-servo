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
    def __init__(self, kp: float, ki_tick: float, clamp: float,
                 floor: float | None = None):
        """kp, ki_tick in normalized units; clamp in normalized volts (0..1),
        i.e. clamp_counts / 8192 (100% of rated current).

        floor: lower output bound. None = symmetric (-clamp, the
        full-architecture output stage with OUT2); 0.0 = MVP unipolar
        actuator, anti-wound at the floor exactly as at the clamp."""
        self.kp = kp
        self.ki_tick = ki_tick
        self.clamp = clamp
        self.floor = -clamp if floor is None else floor
        self.integrator = max(0.0, self._i_lo)

    @property
    def _i_lo(self):
        # A non-negative floor also bounds the integrator, and clear() starts
        # it there (with floor > 0: OUT1 starts just below the dead zone).
        return self.floor if self.floor >= 0.0 else -Q320_LIMIT

    def clear(self):
        self.integrator = max(0.0, self._i_lo)

    def step(self, e: float, hold: bool = False) -> float:
        """One PI tick. e = drive-frame error, normalized. Returns u after
        the hard output bounds (saturation point 4)."""
        p = _clip(self.kp * e, -Q320_LIMIT, Q320_LIMIT)          # sat #1
        i = self.integrator                                       # already sat #2
        u_raw = _clip(p + i, -Q320_LIMIT, Q320_LIMIT)             # sat #3
        u = min(max(u_raw, self.floor), self.clamp)               # sat #4

        # Anti-windup: freeze on hold; skip accumulation while an output
        # bound is engaged and the error would push further into it.
        pushing = (u_raw > self.clamp and e > 0) or (u_raw < self.floor and e < 0)
        if not hold:
            integ = self.integrator if pushing else self.integrator + self.ki_tick * e
            self.integrator = _clip(integ, self._i_lo, Q320_LIMIT)  # sat #2
        return u


def output_mux(u: float, deadband: float, out2_enable: bool = True):
    """design.md Node F: sign-of-u handoff with deadband.

    Returns (v_out1, v_out2), both >= 0, never both nonzero. With
    out2_enable False (MVP, no clamp fitted) OUT2 is always 0 and the
    deadband is an OUT1 turn-on hysteresis.
    """
    if u > deadband:
        return u, 0.0
    if u < -deadband and out2_enable:
        return 0.0, -u
    return 0.0, 0.0
