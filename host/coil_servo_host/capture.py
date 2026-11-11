"""Decoding and saving capture-FIFO records (shared by Board.capture, step,
sweep and the dashboard).

The FIFO holds 16384 32-bit words. Two sources (CFG ctrl b17 capture_sel):

  * raw (0): {IN2 code, IN1 code} per 8 ns clock, 131 us window;
  * decimated (1): {e_dec >> 6, i_dec >> 6} per PI tick (1.024 us),
    16.8 ms window. i_dec is 128 x the mean IN1 code over the tick, so one
    captured LSB is 2^6 / 128 = 0.5 ADC code = I_FS / 16384 amps. The
    setpoint is not recorded directly: setpoint = error + measured.
"""

import csv

import numpy as np

FS_DEC = 125e6 / 128          # decimated sample rate [S/s] (976.6 kS/s)


def decode_words(raw, decimated: bool) -> np.ndarray:
    """FIFO words -> int32 array of shape (N, 2).

    decimated=False: columns (IN1 code, IN2 code), s14
    decimated=True:  columns (i_dec>>6, e_dec>>6), s16
    """
    raw = np.asarray(raw).astype(np.int64)
    lo = (raw & 0xFFFF).astype(np.int32)
    hi = ((raw >> 16) & 0xFFFF).astype(np.int32)
    if decimated:
        lo[lo >= 1 << 15] -= 1 << 16
        hi[hi >= 1 << 15] -= 1 << 16
    else:
        lo &= 0x3FFF
        hi &= 0x3FFF
        lo[lo >= 1 << 13] -= 1 << 14
        hi[hi >= 1 << 13] -= 1 << 14
    return np.stack([lo, hi], axis=1)


def lsb_amps(i_fs: float) -> float:
    """Amps per captured LSB of a decimated record."""
    return i_fs / 16384


def decimated_columns(data: np.ndarray, i_fs: float) -> dict:
    """A decoded decimated record -> {"t_s", "i_amps", "setpoint_amps"}
    (numpy arrays). setpoint = error + measured; in open loop it is the
    OUT1 command expressed in sensor-scale amps, not a current."""
    scale = lsb_amps(i_fs)
    i16 = data[:, 0].astype(float)
    e16 = data[:, 1].astype(float)
    return {"t_s": np.arange(len(i16)) / FS_DEC,
            "i_amps": i16 * scale,
            "setpoint_amps": (e16 + i16) * scale}


def write_csv(path, cols: dict) -> None:
    """Write decimated_columns() output as t_s, i_amps, setpoint_amps."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "i_amps", "setpoint_amps"])
        for row in zip(cols["t_s"], cols["i_amps"], cols["setpoint_amps"]):
            w.writerow([f"{v:.9g}" for v in row])
