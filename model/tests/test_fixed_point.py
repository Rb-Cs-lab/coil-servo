"""Fixed-point mirror: format safety and agreement with the float reference,
closed around the MVP plant (unipolar OUT1, dead zone, RUN-only floor)."""

import numpy as np
import pytest

from coil_servo_model import (CHANNELS, Decimator, FixedPI, FloatPI, Plant,
                              adc_quantize, decode_gain, encode_gain,
                              output_mux_fixed, run_loop, sat)
from coil_servo_model.analysis import overshoot_frac, settling_time, suggest_gains

MOT = CHANNELS["mot"]
DEADBAND_COUNTS = 8
FLOOR = round(0.9 * MOT.dead_zone * 8192)     # RUN-only floor, counts


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


@pytest.mark.parametrize("floor", [None, 0, FLOOR])
def test_rail_to_rail_no_overflow(floor):
    # Worst-case error with worst-case gains: every internal word must
    # saturate gracefully, output must stay inside its bounds (safety
    # invariant 1 at the arithmetic level), with and without the floor.
    clamp = MOT.clamp_counts
    lo = -clamp if floor is None else floor
    for e22 in (2**21 - 1, -(2**21), 12345, -1):
        pi = FixedPI(kp_mant=2**17 - 1, kp_shift=0, ki_mant=2**17 - 1,
                     ki_shift=0, clamp_counts=clamp, floor_counts=floor)
        for _ in range(10):
            u14 = pi.step(e22)
            assert lo <= u14 <= clamp
            assert -(2**23) <= pi.p < 2**23
            assert -(2**23) <= pi.u24 < 2**23
            assert -(2**47) <= pi.acc < 2**47


def test_clamp_wins_over_a_misconfigured_floor():
    pi = FixedPI(1000, 10, 1000, 20, clamp_counts=3000, floor_counts=5000)
    for e in (-(2**21), 0, 2**21 - 1):
        assert pi.step(e) == 3000


def test_floor_bounds_output_and_integrator():
    kp_m, kp_s, ki_m, ki_s = quantized_gains(MOT)[0]
    pi = FixedPI(kp_m, kp_s, ki_m, ki_s, MOT.clamp_counts, FLOOR)
    for _ in range(500):                       # long negative error
        u = pi.step(-(2**20))
        assert u == FLOOR and pi.at_floor
    # the integral term alone holds the output at the floor: no wind-down
    assert pi.acc == FLOOR << (7 + ki_s)
    assert (pi.acc >> ki_s) >> 7 == FLOOR
    # leaving RUN: the floor drops to 0 and a negative error unwinds the
    # integrator until p + i reaches the new floor; the output sits at 0,
    # the integrator never goes negative.
    pi.floor = 0
    for _ in range(5000):
        pi.step(-(2**20))
    assert pi.u14 == 0 and pi.at_floor and pi.acc >= 0


def test_integrator_floor_falls_back_when_the_shift_overflows():
    pi = FixedPI(0, 0, 1, 40, clamp_counts=8000, floor_counts=7000)
    assert pi.acc_floor() == 0                 # 7000 << 47 does not fit s48
    pi.step(-1000)
    assert pi.acc == 0 and pi.u14 == 7000      # output floor still holds


def test_output_mux_fixed_exclusive():
    for out2_enable in (True, False):
        for u in range(-8192, 8192, 61):
            o1, o2 = output_mux_fixed(u, DEADBAND_COUNTS, out2_enable)
            assert o1 == 0 or o2 == 0
            assert o1 >= 0 and o2 >= 0
            if abs(u) <= DEADBAND_COUNTS:
                assert o1 == 0 and o2 == 0
            if not out2_enable:
                assert o2 == 0, "OUT2 driven with out2_enable = 0"


@pytest.mark.parametrize("target", [50.0, 100.0])
def test_fixed_matches_float_closed_loop(target):
    """The bit-exact path must reproduce the float reference within
    quantization: same step from zero, same (quantized) gains and floor,
    same MVP plant. (Below ~50 A one OUT1 count is more than the 0.1 % band
    -- see test_actuator_resolution_dither.)"""
    (kp_m, kp_s, ki_m, ki_s), (kp_q, ki_q) = quantized_gains(MOT)
    sp = lambda t: target

    f_res = run_loop(MOT, FloatPI(kp_q, ki_q, MOT.clamp_counts / 8192.0,
                                  floor=FLOOR / 8192.0),
                     DEADBAND_COUNTS, 2e-3, sp)
    x_res = run_loop(MOT, FixedPI(kp_m, kp_s, ki_m, ki_s, MOT.clamp_counts,
                                  FLOOR),
                     DEADBAND_COUNTS, 2e-3, sp)

    for res in (f_res, x_res):
        ts = settling_time(res.t, res.I, target)
        assert ts is not None and ts < 1e-3
        assert overshoot_frac(res.I, target) < 0.01
        assert np.all(res.v2 == 0.0)
    # Trajectories agree within a few DAC LSB after the first tick (FloatPI
    # starts its integrator on the floor; FixedPI, like the HDL, clears to
    # 0 and is lifted onto the floor by its first tick).
    assert np.max(np.abs(f_res.u[2:] - x_res.u[2:])) < 16 / 8192
    # Final currents agree within a couple of ADC LSB.
    assert abs(f_res.final_current() - x_res.final_current()) < 2 * MOT.amps_per_lsb


def test_fixed_steady_state_within_one_lsb():
    (kp_m, kp_s, ki_m, ki_s), _ = quantized_gains(MOT)
    pi = FixedPI(kp_m, kp_s, ki_m, ki_s, MOT.clamp_counts, FLOOR)
    pi.acc = round(MOT.out1_for_current(50.0) * 8192) << (7 + ki_s)
    res = run_loop(MOT, pi, DEADBAND_COUNTS, 2e-3, lambda t: 50.0,
                   plant=Plant(MOT, i0=50.0))
    tail = res.I[-500:]
    sp_quantized = adc_quantize(50.0 / MOT.I_FS) * MOT.amps_per_lsb
    assert np.all(np.abs(tail - sp_quantized) < 1.5 * MOT.amps_per_lsb)


def test_actuator_resolution_dither():
    # DOCUMENTED (mvp_architecture.md 3.1, REVIEW): one OUT1 count moves
    # the coil current by G_pass/8192 ~ 0.12 A, so a steady hold dithers
    # between two adjacent codes. At 30 A that ripple (~50 mA p-p, after
    # the gate lag) is as wide as the whole +-0.1 % band (+-30 mA), so the
    # spec is met only marginally there and not at all lower down. Bench
    # measurement decides whether future_upgrades.md section 7 (lower
    # op-amp gain + gate bias) is needed.
    (kp_m, kp_s, ki_m, ki_s), _ = quantized_gains(MOT)
    res = run_loop(MOT, FixedPI(kp_m, kp_s, ki_m, ki_s, MOT.clamp_counts, FLOOR),
                   DEADBAND_COUNTS, 3e-3, lambda t: 30.0)
    late = res.t > 1.5e-3
    codes = set(np.round(res.v1[late] * 8192).astype(int))
    assert len(codes) == 2 and max(codes) - min(codes) == 1
    ripple = np.ptp(res.I[late])
    count_amps = MOT.G_pass / 8192
    assert 0.3 * count_amps < ripple < 1.5 * count_amps
    assert ripple > 1e-3 * 30.0
