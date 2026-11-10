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


def plant_gain(channel) -> float:
    """Normalized plant gain: IN1 units of current per OUT1 unit above the
    dead zone (1.0 for the full-architecture plant, whose G_pass was
    specified to equal I_FS; up to ~8 for the MVP bank)."""
    return channel.G_pass / channel.I_FS


def open_loop(channel, kp: float, ki: float, f):
    """Small-signal open-loop transfer L(j2*pi*f), normalized units.

    Plant: transconductance with normalized gain plant_gain(channel) behind
    the inner analog lag, plus the digital delay. PI: kp + ki/s with ki in
    1/s. (The MVP dead zone is a large-signal effect, not in here.)
    """
    w = 2 * math.pi * np.asarray(f, dtype=float)
    s = 1j * w
    plant = plant_gain(channel) / (1 + s * channel.tau_inner)
    delay = np.exp(-s * DIGITAL_DELAY)
    pi = kp + ki / s
    return pi * plant * delay


def crossover_and_margin(channel, kp: float, ki: float):
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


def suggest_gains(channel, f_c: float = 3e3):
    """Starting-point (kp, ki_tick) for a target crossover f_c.

    The plant is a flat transconductance (no L/R pole in the loop), so the
    crossover is set by the integrator: ki ~ 2*pi*f_c / K with K the
    normalized plant gain, and kp = 0.5 / K keeps the proportional loop gain
    well below 1 at high frequency (with a flat plant and ~4 us of delay, a
    proportional loop gain above 1 would be unstable). For the
    full-architecture plant K = 1 and this returns the commissioned values
    (kp 0.5, ki 15080/s). The real gains come from the measured step
    response.
    """
    K = plant_gain(channel)
    ki = 2 * math.pi * f_c * 0.8 / K  # 1/s
    kp = 0.5 / K
    return kp, ki * T_S               # (kp, per-tick ki)
