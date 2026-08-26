"""Single-frequency transfer-function estimation from a synchronous
stimulus/response capture (the swept-sine measurement's math, kept pure so
it is unit-testable without hardware)."""

import numpy as np


def estimate_tf(stim: np.ndarray, resp: np.ndarray, fs: float):
    """Estimate H = resp/stim at the stimulus's dominant frequency.

    Both arrays are the same synchronous capture. Returns
    (f0_hz, H_complex, stim_amplitude) or None if no clean tone is found.
    The correlation window is truncated to an integer number of periods so
    spectral leakage doesn't bias the estimate.
    """
    stim = np.asarray(stim, dtype=float)
    resp = np.asarray(resp, dtype=float)
    n = len(stim)
    stim = stim - stim.mean()
    resp = resp - resp.mean()

    spec = np.abs(np.fft.rfft(stim))
    spec[0] = 0.0
    k = int(np.argmax(spec))
    if k == 0 or spec[k] < 4 * np.median(spec[1:]):
        return None                       # no dominant tone in the stimulus
    # refine beyond the fs/n bin resolution: at low frequencies (a couple of
    # periods per record) FFT leakage biases both the peak position and the
    # correlation amplitude. Scan the correlation magnitude -- a smooth
    # function of frequency -- across the neighboring bins, then polish with
    # a parabolic fit on the scan.
    t_full = np.arange(n) / fs
    f_lo = max(k - 1, 0.5) * fs / n
    f_hi = (k + 1) * fs / n
    fscan = np.linspace(f_lo, f_hi, 41)
    mags = np.array([abs(np.dot(stim, np.exp(-2j * np.pi * f * t_full)))
                     for f in fscan])
    j = int(np.argmax(mags))
    if 1 <= j < len(fscan) - 1:
        a, b, c = mags[j - 1], mags[j], mags[j + 1]
        denom = a - 2 * b + c
        delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
        delta = max(-1.0, min(1.0, delta))
    else:
        delta = 0.0
    f0 = fscan[j] + delta * (fscan[1] - fscan[0])

    periods = int(np.floor(n * f0 / fs))
    if periods < 1:
        return None
    m = int(round(periods * fs / f0))
    t = np.arange(m) / fs
    ref = np.exp(-2j * np.pi * f0 * t)
    s = np.dot(stim[:m], ref) * 2 / m
    r = np.dot(resp[:m], ref) * 2 / m
    if abs(s) == 0.0:
        return None
    return f0, r / s, abs(s)
