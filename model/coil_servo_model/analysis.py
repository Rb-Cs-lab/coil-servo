"""Loop analysis helpers: settling metrics, small-signal margins, gain
suggestions.

The gain suggestions are STARTING POINTS derived from the provisional plant
model. BOOTSTRAP is explicit that the real PI gains are set from the measured
open-loop step response -- these numbers exist so simulation has something
sensible to run with, and to show the fixed-point formats accommodate the
plausible range.
"""

import math

import numpy as np

from .channels import Channel
from .loop import T_S

# Digital delay budget from design.md section 4: decimator group delay
# (64 fast samples) + PI pipeline + DAC, ~3.6 us total.
DIGITAL_DELAY = 3.6e-6


def settling_time(t, I, target: float, tol_frac: float = 1e-3):
    """Time after which |I - target| stays within tol_frac * |target|.

    Returns None if it never settles within the record.
    """
    band = abs(target) * tol_frac
    outside = np.abs(np.asarray(I) - target) > band
    if outside[-1]:
        return None
    idx = np.where(outside)[0]
    if len(idx) == 0:
        return 0.0
    return float(t[idx[-1] + 1])


def overshoot_frac(I, target: float) -> float:
    """Peak excursion beyond target as a fraction of |target| (0 if none)."""
    if target == 0:
        return 0.0
    peak = np.max(np.sign(target) * np.asarray(I))
    return max(0.0, (peak - abs(target)) / abs(target))


def open_loop(channel: Channel, kp: float, ki: float, f):
    """Small-signal open-loop transfer L(j2*pi*f), normalized units.

    Plant (pass-bank regime, provisional): transconductance with unity
    normalized DC gain (G_pass scaled to I_FS) behind the inner analog lag,
    plus the digital delay. PI: kp + ki/s with ki in 1/s.
    """
    w = 2 * math.pi * np.asarray(f, dtype=float)
    s = 1j * w
    plant = (channel.G_pass / channel.I_FS) / (1 + s * channel.tau_inner)
    delay = np.exp(-s * DIGITAL_DELAY)
    pi = kp + ki / s
    return pi * plant * delay


def crossover_and_margin(channel: Channel, kp: float, ki: float):
    """Returns (f_c [Hz], phase margin [deg]) of the small-signal loop."""
    f = np.logspace(1, 6, 20000)
    L = open_loop(channel, kp, ki, f)
    mag = np.abs(L)
    idx = np.where(mag < 1.0)[0]
    if len(idx) == 0 or idx[0] == 0:
        raise ValueError("no unity-gain crossover in 10 Hz - 1 MHz")
    i = idx[0]
    # log-interpolate the crossover frequency
    f_c = np.interp(0.0, np.log10(mag[[i, i - 1]]), np.log10(f[[i, i - 1]]))
    f_c = 10.0 ** f_c
    phase = np.degrees(np.angle(open_loop(channel, kp, ki, f_c)))
    return float(f_c), float(180.0 + phase)


def suggest_gains(channel: Channel, f_c: float = 3e3):
    """Starting-point (kp, ki_tick) for a target crossover f_c.

    With the provisional transconductance plant (flat, unity normalized gain)
    the crossover is set by the integrator: ki ~ 2*pi*f_c, with a modest kp
    for phase lead. If the composite plant turns out to look like L/R instead
    (voltage-mode actuator), kp rises to ~f_c/f_pole (order 20-50) -- both
    regimes fit the s18+shift gain formats with room to spare.
    """
    ki = 2 * math.pi * f_c * 0.8      # 1/s
    kp = 0.5
    return kp, ki * T_S               # (kp, per-tick ki)
