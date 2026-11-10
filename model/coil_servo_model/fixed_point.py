"""Bit-exact fixed-point mirror of the PI signal path (design.md sections 1-2).

Every operation here is meant to be reproduced 1:1 in Verilog; the cocotb
bench compares the HDL against this class cycle-for-cycle. Python ints are
arbitrary-precision and `>>` on negatives is an arithmetic (floor) shift,
matching Verilog's signed `>>>`, so the arithmetic is exact by construction --
the only thing to get right is where widths saturate, which is what the
sat*() calls pin down.

Formats (design.md section 2):
  ADC/DAC codes   s14  (Q1.13, 1.0 == I_FS == 1 V)
  fast error      s15
  decimated error s22  (Q2.20)
  gain mantissas  s18 + right-shift (u5 for P, u6 for I)
  p, i, u         s24  (Q3.20)
  integrator acc  s48, saturating
"""


def sat(x: int, bits: int) -> int:
    """Saturate x to a signed `bits`-wide integer."""
    lim = 1 << (bits - 1)
    if x >= lim:
        return lim - 1
    if x < -lim:
        return -lim
    return x


def adc_quantize(value: float) -> int:
    """Normalized value (1.0 == full scale) -> s14 ADC code."""
    return sat(round(value * 8192), 14)


class Decimator:
    """Boxcar accumulate-and-dump, ratio 128: 128 x s15 -> s22 exact."""
    RATIO = 128

    def __init__(self):
        self.acc = 0
        self.count = 0

    def push(self, e_fast: int):
        """Feed one s15 fast-rate error sample. Returns the s22 decimated
        error once per 128 samples, else None."""
        assert -(1 << 14) <= e_fast < (1 << 14), "fast error exceeds s15"
        self.acc += e_fast
        self.count += 1
        if self.count == self.RATIO:
            out = self.acc          # sum of 128 s15 fits s22 exactly
            self.acc = 0
            self.count = 0
            return out
        return None


def encode_gain(g: float, shift_bits: int) -> tuple[int, int]:
    """Encode a float gain as (s18 mantissa, right-shift) with the shift
    chosen for maximum precision. Returns (mant, shift)."""
    max_shift = (1 << shift_bits) - 1
    if g == 0.0:
        return 0, 0
    best = None
    for shift in range(max_shift + 1):
        mant = round(g * (1 << shift))
        if -(1 << 17) <= mant < (1 << 17):
            err = abs(mant / (1 << shift) - g)
            if best is None or err <= best[2]:
                best = (mant, shift, err)
    if best is None or best[0] == 0:
        raise ValueError(f"gain {g} not representable as s18 * 2^-shift")
    return best[0], best[1]


def decode_gain(mant: int, shift: int) -> float:
    return mant / (1 << shift)


class FixedPI:
    """Integer PI, one step() per PI tick. Same structure/order as FloatPI.

    Output bounds: u14 = min(max(u_pre, floor), clamp) -- the clamp wins if
    the two cross. floor_counts=None gives the full-architecture symmetric
    bound (-clamp, OUT2 side); the MVP uses 0, or the RUN-only dead-zone
    floor (register u_floor). `floor` may be changed between steps, as the
    HDL does when the FSM enters or leaves RUN.

    A non-negative floor also bounds the integrator: after every non-held
    tick acc >= floor << (7 + ki_shift), so the integral term alone holds
    the output at the floor. If that shift would not fit s48 (ki_shift > 26
    with a large floor) the integrator bound falls back to 0 -- the output
    floor still applies. servo_pi.v implements exactly this.
    """

    ACC_MAX = (1 << 47) - 1

    def __init__(self, kp_mant: int, kp_shift: int, ki_mant: int,
                 ki_shift: int, clamp_counts: int,
                 floor_counts: int | None = None):
        self.kp_mant = kp_mant
        self.kp_shift = kp_shift
        self.ki_mant = ki_mant
        self.ki_shift = ki_shift
        self.clamp = clamp_counts
        self.floor = -clamp_counts if floor_counts is None else floor_counts
        self.acc = 0                   # s48 integrator accumulator
        # Introspection for cocotb comparison (set by step()):
        self.p = self.i = self.u24 = self.u14 = self.u_pre = 0
        self.out_sat = self.at_floor = False

    def acc_floor(self) -> int:
        """Integrator lower bound for the current floor (see class doc)."""
        if self.floor < 0:
            return -(1 << 47)          # no bound beyond s48 saturation
        w = self.floor << (7 + self.ki_shift)
        return w if w <= self.ACC_MAX else 0

    def clear(self):
        self.acc = 0

    def step(self, e22: int, hold: bool = False) -> int:
        """e22 = decimated drive-frame error, s22 Q2.20. Returns u14, the
        bounded output in DAC counts (s14 Q1.13)."""
        assert -(1 << 21) <= e22 < (1 << 21), "error exceeds s22"

        self.p = sat((e22 * self.kp_mant) >> self.kp_shift, 24)   # sat #1
        self.i = sat(self.acc >> self.ki_shift, 24)
        self.u24 = sat(self.p + self.i, 24)                        # sat #3

        # Q3.20 -> Q1.13: round to nearest on the 7 dropped bits, then the
        # output bounds (sat #4/#5; design.md Node E).
        u_pre = (self.u24 + 64) >> 7
        self.u_pre = u_pre
        self.u14 = min(max(u_pre, self.floor), self.clamp)
        self.out_sat = u_pre > self.clamp or u_pre < -self.clamp
        self.at_floor = u_pre <= self.floor

        # anti-windup: don't integrate further into whichever bound is hit
        skip = ((u_pre > self.clamp and e22 > 0) or
                (u_pre < self.floor and e22 < 0))
        if not hold:
            acc = self.acc if skip else sat(self.acc + e22 * self.ki_mant, 48)
            self.acc = max(acc, self.acc_floor())                  # sat #2
        return self.u14


def output_mux_fixed(u14: int, deadband_counts: int, out2_enable: bool = True):
    """Node F in counts. Returns (out1, out2) DAC codes, both >= 0,
    never both nonzero. out2 saturates at 8191: -(-8192) does not fit s14
    (unreachable through the PI clamp, but the mux is safe standalone).
    out2_enable False (MVP, CFG ctrl b18 = 0): OUT2 is always 0."""
    if u14 > deadband_counts:
        return u14, 0
    if u14 < -deadband_counts and out2_enable:
        return 0, min(-u14, 8191)
    return 0, 0


class SlewLimiter:
    """Setpoint slew-rate limiter, one step() per 8 ns fabric clock.
    Bit-exact mirror of modules/servo_slew.v (see its header).

    acc holds the setpoint in counts << 14; rates are in 2^-14 counts per
    clock. "Up" = away from zero, "down" = toward zero. hold_zero forces 0,
    bypass passes the target through; a rate of 0 freezes the setpoint."""

    FRAC = 14

    def __init__(self):
        self.acc = 0

    @property
    def sp(self) -> int:
        return self.acc >> self.FRAC

    def step(self, target: int, rate_up: int, rate_down: int,
             hold_zero: bool = False, bypass: bool = False) -> int:
        t = target << self.FRAC
        if hold_zero:
            self.acc = 0
        elif bypass:
            self.acc = t
        else:
            delta = t - self.acc
            away = (self.acc >= 0 and delta > 0) or (self.acc <= 0 and delta < 0)
            r = rate_up if away else rate_down
            if delta > r:
                self.acc += r
            elif delta < -r:
                self.acc -= r
            else:
                self.acc = t
        return self.sp


def slew_rate_code(amps_per_s: float, i_fs: float, clk_hz: float = 125e6) -> int:
    """Physical ramp rate -> slew register value (2^-14 counts per clock).
    200 A/ms at I_FS = 200 A -> 1074."""
    return round(amps_per_s / clk_hz / i_fs * 8192 * (1 << SlewLimiter.FRAC))


def drive_frame_error(meas_code: int, in2_code: int, sp_reg: int,
                      sp_source: int, sp_force_zero: int, polarity: int):
    """design.md Node B: setpoint mux + bridge-frame rotation, in s14 codes.

    Returns (e_fast s15, sp_active s14, sp_sign_mismatch). The mismatch flag
    reports a setpoint whose sign disagrees with the bridge polarity by more
    than MISMATCH_THR counts (a safe condition -- the loop drives to zero --
    but one the host should warn about loudly).
    """
    MISMATCH_THR = 64
    sp = 0 if sp_force_zero else (sp_reg if sp_source else in2_code)
    e = sp - meas_code
    if polarity:
        e = -e
    mismatch = (sp > MISMATCH_THR) if polarity else (sp < -MISMATCH_THR)
    return e, sp, int(mismatch)
