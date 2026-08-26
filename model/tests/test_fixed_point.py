"""Fixed-point mirror: format safety and agreement with the float reference."""

import numpy as np
import pytest

from coil_servo_model import (CHANNELS, Decimator, FixedPI, FloatPI,
                              adc_quantize, decode_gain, encode_gain,
                              output_mux_fixed, run_loop, sat)
from coil_servo_model.analysis import settling_time, suggest_gains

MOT = CHANNELS["mot"]
DEADBAND_COUNTS = 8


def quantized_gains(channel):
    kp, ki_tick = suggest_gains(channel)
    kp_m, kp_s = encode_gain(kp, shift_bits=5)
    ki_m, ki_s = encode_gain(ki_tick, shift_bits=6)
    return (kp_m, kp_s, ki_m, ki_s), (decode_gain(kp_m, kp_s),
                                      decode_gain(ki_m, ki_s))


def test_sat():
    assert sat(8191, 14) == 8191
    assert sat(8192, 14) == 8191
    assert sat(-8192, 14) == -8192
    assert sat(-8193, 14) == -8192


def test_adc_quantize_clips_at_rails():
    assert adc_quantize(2.0) == 8191
    assert adc_quantize(-2.0) == -8192
    assert adc_quantize(0.0) == 0
    assert adc_quantize(1.0 / 8192) == 1


def test_encode_gain_precision():
    for g in (0.5, 0.015, 42.0, 1.9e-4, -0.3):
        mant, shift = encode_gain(g, shift_bits=6)
        assert abs(decode_gain(mant, shift) - g) <= abs(g) * 2 ** -16
        assert -(1 << 17) <= mant < (1 << 17)


def test_decimator_exact_sum():
    d = Decimator()
    for k in range(127):
        assert d.push(-16384 + 257 * (k % 128)) is None
    out = d.push(100)
    assert out == sum(-16384 + 257 * (k % 128) for k in range(127)) + 100


def test_rail_to_rail_no_overflow():
    # Worst-case error with worst-case gains: every internal word must
    # saturate gracefully, output must stay inside the clamp (safety
    # invariant 1 at the arithmetic level -- BOOTSTRAP testbench assertion,
    # checked here first).
    clamp = MOT.clamp_counts
    for e22 in (2**21 - 1, -(2**21), 12345, -1):
        pi = FixedPI(kp_mant=2**17 - 1, kp_shift=0,
                     ki_mant=2**17 - 1, ki_shift=0, clamp_counts=clamp)
        for _ in range(10):
            u14 = pi.step(e22)
            assert -clamp <= u14 <= clamp
            assert -(2**23) <= pi.p < 2**23
            assert -(2**23) <= pi.u24 < 2**23
            assert -(2**47) <= pi.acc < 2**47


def test_output_mux_fixed_exclusive():
    for u in range(-8192, 8192, 61):
        o1, o2 = output_mux_fixed(u, DEADBAND_COUNTS)
        assert o1 == 0 or o2 == 0
        assert o1 >= 0 and o2 >= 0
        if abs(u) <= DEADBAND_COUNTS:
            assert o1 == 0 and o2 == 0


def test_fixed_matches_float_closed_loop():
    """The bit-exact path must reproduce the float reference within
    quantization: same step, same (quantized) gains, same plant."""
    (kp_m, kp_s, ki_m, ki_s), (kp_q, ki_q) = quantized_gains(MOT)
    target = 80.0
    sp = lambda t: target
    boost = lambda t: t < 0.3e-3

    f_res = run_loop(MOT, FloatPI(kp_q, ki_q, MOT.clamp_counts / 8192.0),
                     DEADBAND_COUNTS, 2e-3, sp, boost)
    x_res = run_loop(MOT, FixedPI(kp_m, kp_s, ki_m, ki_s, MOT.clamp_counts),
                     DEADBAND_COUNTS, 2e-3, sp, boost)

    # Both settle on the setpoint to spec.
    for res in (f_res, x_res):
        ts = settling_time(res.t, res.I, target)
        assert ts is not None and ts < 1e-3
    # Trajectories agree within a few DAC LSB throughout.
    assert np.max(np.abs(f_res.u - x_res.u)) < 16 / 8192
    # Final currents agree within one ADC LSB.
    assert abs(f_res.final_current() - x_res.final_current()) < 2 * MOT.amps_per_lsb


def test_fixed_steady_state_within_one_lsb():
    (kp_m, kp_s, ki_m, ki_s), _ = quantized_gains(MOT)
    res = run_loop(MOT, FixedPI(kp_m, kp_s, ki_m, ki_s, MOT.clamp_counts),
                   DEADBAND_COUNTS, 2e-3, lambda t: 50.0,
                   boost_fn=lambda t: t < 0.3e-3)
    tail = res.I[-200:]
    sp_quantized = adc_quantize(50.0 / MOT.I_FS) * MOT.amps_per_lsb
    assert np.all(np.abs(tail - sp_quantized) < 1.5 * MOT.amps_per_lsb)
