"""Transfer-function estimator: recover a known H from synthetic captures."""

import math

import numpy as np
import pytest

from coil_servo_host.tf import estimate_tf

FS = 125e6 / 128
N = 16384


def make_capture(f0, mag, phase_deg, amp=1000.0, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(N) / FS
    stim = amp * np.sin(2 * np.pi * f0 * t)
    resp = mag * amp * np.sin(2 * np.pi * f0 * t + math.radians(phase_deg))
    if noise:
        stim = stim + rng.normal(0, noise, N)
        resp = resp + rng.normal(0, noise, N)
    return stim, resp


@pytest.mark.parametrize("f0,mag,ph", [
    (100.0, 0.8, -5.0),        # near the plant pole, ~1.7 periods in window
    (1000.0, 0.5, -30.0),
    (5000.0, 0.05, -85.0),
    (50000.0, 0.01, -95.0),
])
def test_recovers_known_tf(f0, mag, ph):
    stim, resp = make_capture(f0, mag, ph, noise=2.0)
    result = estimate_tf(stim, resp, FS)
    assert result is not None
    f_est, h, amp = result
    assert f_est == pytest.approx(f0, rel=0.05)
    assert abs(h) == pytest.approx(mag, rel=0.03)
    assert math.degrees(math.atan2(h.imag, h.real)) == pytest.approx(ph, abs=2.0)
    assert amp == pytest.approx(1000.0, rel=0.05)


def test_rejects_noise_only_capture():
    rng = np.random.default_rng(7)
    stim = rng.normal(0, 5.0, N)
    resp = rng.normal(0, 5.0, N)
    assert estimate_tf(stim, resp, FS) is None
