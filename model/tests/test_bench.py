"""The low-current bench profile (docs/bench_supply_test.md): the MOT coil
and power stage on a current-limited bench supply (5 V, 3 A), coil lead
wound 10x through the LEM.

What changes against the production MOT channel:
  * the supply's constant-current (CC) limit caps the coil current;
  * I_FS = 20 A (2.44 mA per ADC count), so the normalized plant gain is
    10x larger (~48) and the suggested gains 10x smaller -- the loop's
    crossover and phase margin are unchanged;
  * one OUT1 count is still ~0.12 A (G_pass does not depend on the sensor),
    ~5 % of a 2.5 A hold: without the dither the hold hunts by several %;
  * the RUN floor gap below the dead zone (10 % of it, ~42 A worth of gate
    at G_pass) is crossed by the integrator at a rate proportional to the
    step size, so small steps from zero are slow.

Tolerance band. The 0.1 % spec is a production number (0.1 A at 100 A).
0.1 % of a 2 A target is 2 mA, below one ADC count (2.44 mA) and far below
one OUT1 count (117 mA), so the bench tests use +-2.5 mA = 0.1 % of the
bench's rated 2.5 A (about one ADC count) as their band; the fixed-point
tests need the dither to get there.
"""

import math
from dataclasses import replace

import numpy as np
import pytest

from coil_servo_model import (BENCH_CHANNELS, CHANNELS, Channel, FixedPI,
                              FloatPI, Plant, T_S, decode_gain, encode_gain,
                              run_loop)
from coil_servo_model.analysis import (crossover_and_margin, plant_gain,
                                       settling_time, suggest_gains)

BENCH = BENCH_CHANNELS["mot_bench"]
MOT = CHANNELS["mot"]
DEADBAND_COUNTS = 8
FLOOR = round(0.9 * BENCH.dead_zone * 8192)      # RUN floor, counts
BAND = 2.5e-3                                     # amps, see module doc


def hold(plant, v1, t):
    for _ in range(int(round(t / T_S))):
        plant.step(v1, 0.0, False, T_S)
    return plant.I


def float_run(ch, setpoint_fn, t_end, i0=0.0, floor=FLOOR / 8192):
    kp, ki_tick = suggest_gains(ch)
    pi = FloatPI(kp, ki_tick, clamp=ch.clamp_counts / 8192, floor=floor)
    if i0 > 0.0:
        pi.integrator = ch.out1_for_current(i0)
    return run_loop(ch, pi, DEADBAND_COUNTS, t_end, setpoint_fn,
                    plant=Plant(ch, i0=i0))


def fixed_run(ch, setpoint_fn, t_end, i0, dither, floor=FLOOR):
    kp, ki_tick = suggest_gains(ch)
    kp_m, kp_s = encode_gain(kp, 5)
    ki_m, ki_s = encode_gain(ki_tick, 6)
    pi = FixedPI(kp_m, kp_s, ki_m, ki_s, ch.clamp_counts, floor)
    pi.acc = round(ch.out1_for_current(i0) * 8192) << (7 + ki_s)
    return run_loop(ch, pi, DEADBAND_COUNTS, t_end, setpoint_fn,
                    plant=Plant(ch, i0=i0), dither=dither)


def band_frac(target):
    return BAND / target


# --- profile ------------------------------------------------------------

def test_bench_profile_is_the_mot_stage_with_a_limited_supply():
    # same coil, bank and rail as the MOT; only supply limit, sensing and
    # the protection-related values differ
    same = replace(BENCH, name="mot", I_rated=MOT.I_rated, I_supply_max=None,
                   lem_turns=1, out_clamp_counts=None)
    assert same == MOT
    assert "mot_bench" not in CHANNELS      # production table untouched


def test_defaults_leave_production_unchanged():
    ch = Channel(name="x", L=16e-6, R_coil=6.4e-3, I_rated=100.0)
    assert ch.I_supply_max is None and ch.lem_turns == 1
    assert ch.out_clamp_counts is None
    for c in CHANNELS.values():
        assert c.I_supply_max is None and c.lem_turns == 1
        assert c.I_FS == pytest.approx(200.0)


def test_lem_turns_multiply_the_sensitivity():
    # N primary turns: N x the ampere-turns for the same coil current
    assert BENCH.lem_turns == 10
    assert BENCH.I_FS == pytest.approx(20.0)
    assert BENCH.amps_per_lsb == pytest.approx(2.44e-3, rel=1e-3)
    for n in (1, 2, 5, 10):
        assert replace(MOT, lem_turns=n).I_FS == pytest.approx(200.0 / n)
    # the alternative, in-amp gain, scales the same way
    assert replace(MOT, G_inamp=10.0).I_FS == pytest.approx(BENCH.I_FS)


def test_current_ordering():
    # rated < supply limit < what the 5 V rail could push: the supply, not
    # the rail, is the limit on the bench
    rail_limited = replace(BENCH, I_supply_max=None).max_current
    assert BENCH.I_rated < BENCH.I_supply_max < rail_limited
    assert BENCH.max_current == BENCH.I_supply_max
    assert BENCH.I_supply_max < BENCH.I_FS     # measurable on IN1


def test_bench_clamp_leaves_headroom_above_the_dead_zone():
    # set directly (the derived one sits ~44 counts above the dead zone)
    derived = round(BENCH.out1_for_current(BENCH.I_rated) * 8192)
    dz = BENCH.dead_zone * 8192
    assert derived - dz < 60
    assert BENCH.clamp_counts == 4000
    assert BENCH.clamp_counts > 1.1 * dz          # >10 % dead-zone headroom
    assert BENCH.clamp_counts < MOT.clamp_counts  # below the production gate
    assert FLOOR < dz < BENCH.clamp_counts


def test_bank_dissipation_is_harmless_without_water():
    # whole supply power minus the series path, at the supply limit
    I = BENCH.I_supply_max
    p_bank = (BENCH.V_rail - I * BENCH.R_series) * I
    assert p_bank < 15.1                      # ~15 W total
    assert p_bank / len(BENCH.V_th) < 2.6     # ~2.5 W per FET (vs ~50 W at 100 A)


def test_heartbeat_trip_energy_is_tiny():
    # bridge opened at the supply limit: the coil energy goes back into the
    # bench supply's output capacitor
    energy = 0.5 * BENCH.L * BENCH.I_supply_max ** 2
    assert energy == pytest.approx(72e-6, rel=0.01)
    plant = Plant(BENCH, i0=BENCH.I_supply_max)
    plant.bridge_enabled = False
    assert hold(plant, 0.0, 20e-6) == 0.0     # gone within ~7 us


# --- plant: supply current limit ----------------------------------------

def test_supply_limit_caps_the_current():
    # the bank asks for ~45 A (the clamp); the supply holds the coil at 3 A
    plant = Plant(BENCH)
    v = BENCH.clamp_counts / 8192
    assert BENCH.bank_current(BENCH.gate_command(v)) > 10 * BENCH.I_supply_max
    assert hold(plant, v, 200e-6) == BENCH.I_supply_max
    assert hold(plant, v, 1e-3) == BENCH.I_supply_max


def test_supply_limit_does_not_change_the_rise_below_it():
    # below the limit the rise is the 5 V compliance ramp, as without one
    v = BENCH.clamp_counts / 8192
    limited, free = Plant(BENCH), Plant(replace(BENCH, I_supply_max=None))
    for _ in range(5):                      # ~5 us, ~1.5 A at ~0.3 A/us
        limited.step(v, 0.0, False, T_S)
        free.step(v, 0.0, False, T_S)
        assert limited.I == free.I < BENCH.I_supply_max


def test_freewheel_is_not_affected_by_the_supply_limit():
    # turn-off circulates in the bridge, not through the supply
    a = Plant(BENCH, i0=2.5)
    b = Plant(replace(BENCH, I_supply_max=None), i0=2.5)
    for _ in range(30):
        a.step(0.0, 0.0, False, T_S)
        b.step(0.0, 0.0, False, T_S)
        assert a.I == b.I
    # ~ L I / V_f: tens of us from 2.5 A
    assert 30e-6 < BENCH.freewheel_time < 80e-6


def test_none_limit_reproduces_production_behaviour():
    v = MOT.out1_for_current(100.0)
    a, b = Plant(MOT), Plant(replace(MOT, I_supply_max=1e9))
    for _ in range(600):
        a.step(v, 0.0, False, T_S)
        b.step(v, 0.0, False, T_S)
    assert a.I == b.I


def test_plant_refuses_to_start_above_the_supply_limit():
    with pytest.raises(AssertionError):
        Plant(BENCH, i0=3.5)


# --- loop: gains and margins --------------------------------------------

def test_bench_plant_gain_and_gains_scale_with_the_turns():
    K = plant_gain(BENCH)
    assert K == pytest.approx(10 * plant_gain(MOT))
    kp, ki_tick = suggest_gains(BENCH)
    kp_mot, ki_mot = suggest_gains(MOT)
    assert kp == pytest.approx(kp_mot / 10)
    assert ki_tick == pytest.approx(ki_mot / 10)


def test_bench_gains_encode_precisely():
    # ~10x smaller gains still fit the s18 mantissa + shift with ~17 bits
    kp, ki_tick = suggest_gains(BENCH)
    for g, bits in ((kp, 5), (ki_tick, 6)):
        mant, shift = encode_gain(g, bits)
        assert abs(mant) >= 1 << 16
        assert decode_gain(mant, shift) == pytest.approx(g, rel=2e-5)


def test_bench_small_signal_margins():
    kp, ki_tick = suggest_gains(BENCH)
    f_c, pm = crossover_and_margin(BENCH, kp, ki_tick / T_S)
    assert 2e3 < f_c < 5e3, f"crossover {f_c:.0f} Hz"
    assert pm > 45, f"phase margin {pm:.0f} deg"


@pytest.mark.parametrize("g_fs", [5.0, 10.0, 20.0, 1e6])
def test_bench_gains_stay_stable_across_the_near_threshold_g_fs(g_fs):
    # PROVISIONAL g_fs is least certain near threshold (<= 0.5 A per FET),
    # most likely LOWER than 50 A/V there. Lower g_fs -> lower plant gain
    # -> the nominal gains give a slower loop (crossover down to ~0.5 kHz
    # at 5 A/V) but more phase margin; ballast-limited -> ~7 kHz, still
    # stable. The measured G_pass then sets the gains.
    kp, ki_tick = suggest_gains(BENCH)
    f_c, pm = crossover_and_margin(replace(BENCH, g_fs=g_fs), kp, ki_tick / T_S)
    assert 300 < f_c < 10e3, f"crossover {f_c:.0f} Hz"
    assert pm > 45, f"phase margin {pm:.0f} deg"


# --- loop: closed-loop steps at bench currents --------------------------

@pytest.mark.parametrize("i0,target", [(1.0, 1.1), (1.0, 0.9), (2.0, 2.2),
                                       (2.0, 1.8), (1.0, 2.0)])
def test_float_steps_from_an_operating_point_meet_spec(i0, target):
    res = float_run(BENCH, lambda t: target, 2e-3, i0=i0)
    ts = settling_time(res.t, res.I, target, band_frac(target))
    assert ts is not None and ts < 1e-3, f"{i0}->{target} A settled in {ts}"
    assert np.max(res.I) < max(i0, target) + BAND      # no overshoot
    assert np.min(res.I) > min(i0, target) - BAND      # no undershoot
    assert np.all(res.v2 == 0.0)


def test_fixed_point_hold_hunts_without_dither_and_not_with_it():
    # DOCUMENTED: one OUT1 count is ~0.12 A, ~6 % of a 2 A hold.
    count_amps = BENCH.G_pass / 8192
    ptp = {}
    for dither in (False, True):
        res = fixed_run(BENCH, lambda t: 2.0, 3e-3, i0=2.0, dither=dither)
        tail = res.I[res.t > 1e-3]
        ptp[dither] = np.ptp(tail)
        assert abs(tail.mean() - 2.0) < BENCH.amps_per_lsb
    assert ptp[False] > 0.01 * 2.0, f"no hunting? {ptp[False]}"   # > 1 %
    assert ptp[True] < 0.1 * count_amps                            # ~mA


def test_fixed_point_step_needs_the_dither_to_settle_in_band():
    # 1 -> 2 A through the bit-exact mirror: with the dither it settles
    # within +-2.5 mA (~1 ADC count) in < 1 ms; without, it never does.
    with_d = fixed_run(BENCH, lambda t: 2.0, 3e-3, i0=1.0, dither=True)
    ts = settling_time(with_d.t, with_d.I, 2.0, band_frac(2.0))
    assert ts is not None and ts < 1e-3, f"settled in {ts}"
    without = fixed_run(BENCH, lambda t: 2.0, 3e-3, i0=1.0, dither=False)
    ts = settling_time(without.t, without.I, 2.0, band_frac(2.0))
    assert ts is None or ts > 2e-3


def test_small_steps_from_zero_are_slow_with_the_90_percent_floor():
    # DOCUMENTED: from the floor (10 % of the dead zone below conduction),
    # the integrator climbs at ki * error -- for a 2 A step that takes
    # ~1.4 ms before current flows. Production steps (tens of A) are 10x+
    # faster. Not a fault; the bench procedure tests steps from a held
    # current, or raises the floor once the dead zone is measured.
    res = float_run(BENCH, lambda t: 2.0, 4e-3)
    ts = settling_time(res.t, res.I, 2.0, band_frac(2.0))
    assert ts is not None and 1.5e-3 < ts < 3e-3


FLOOR_NEAR = round(BENCH.dead_zone * 8192) - 50   # counts


@pytest.mark.parametrize("target,t_max", [(2.0, 1e-3), (2.5, 1e-3), (1.0, 1.5e-3)])
def test_floor_near_the_measured_dead_zone_speeds_up_steps_from_zero(target, t_max):
    # Bench option: u_floor_counts = (measured dead zone - 50 counts), i.e.
    # ~50 mV of gate below conduction (~10 K of V_th drift; the bank warms
    # by only a few K at <= 15 W). 2-2.5 A from zero then settle in ~1 ms.
    # Below ~1 A only one to three FETs conduct (V_th spread 60 mV ~ 60
    # OUT1 counts), so the plant gain -- and the loop -- is slower there:
    # DOCUMENTED, not a fault.
    res = float_run(BENCH, lambda t: target, 3e-3, floor=FLOOR_NEAR / 8192)
    ts = settling_time(res.t, res.I, target, band_frac(target))
    assert ts is not None and ts < t_max, f"{target} A settled in {ts}"
    assert np.max(res.I) < target + BAND


def test_slew_limited_moves_at_bench_currents():
    # The production ramp rates carry over: up 200 A/ms (below the 5 V /
    # 16 uH ~ 310 A/ms compliance rate), down 50 A/ms. At 2-3 A the body
    # diode V_f is likely lower than the model's 0.8 V; even at 0.6 V
    # (freewheel ~38 A/ms, slower than the 50 A/ms ramp) the short ramp
    # does not undershoot.
    up = float_run(BENCH, lambda t: min(2.5, 200e3 * t), 3e-3,
                   floor=FLOOR_NEAR / 8192)
    assert np.max(up.I) < 2.5 + BAND
    for vf in (0.8, 0.6):
        ch = replace(BENCH, V_f=vf)
        res = float_run(ch, lambda t: max(0.5, 2.5 - 50e3 * t), 2e-3, i0=2.5)
        assert np.min(res.I) > 0.5 - BAND, f"V_f {vf}: undershoot"
        ts = settling_time(res.t, res.I, 0.5, band_frac(0.5))
        assert ts is not None and ts < 0.04e-3 + 1e-3


def test_setpoint_above_the_supply_limit_winds_the_integrator_up():
    # DOCUMENTED (why trip_amps sits below the supply limit): asking for
    # more than the supply gives holds the coil at the CC limit while the
    # integrator climbs toward the clamp (anti-windup only acts AT the
    # clamp). Recovery then takes longer than a normal step; the firmware
    # trip below the limit prevents this on the bench.
    res = float_run(BENCH, lambda t: 3.5 if t < 6e-3 else 2.0, 12e-3, i0=2.0)
    held = res.I[(res.t > 0.5e-3) & (res.t < 6e-3)]
    assert np.all(held == BENCH.I_supply_max)
    assert np.max(res.u) == pytest.approx(BENCH.clamp_counts / 8192)  # wound up
    after = res.t >= 6e-3
    ts = settling_time(res.t[after] - 6e-3, res.I[after], 2.0, band_frac(2.0))
    assert ts is not None and 2e-3 < ts < 6e-3, f"recovered in {ts}"
    # the same move without the wind-up (from a settled 2.9 A) is quick
    normal = float_run(BENCH, lambda t: 2.0, 3e-3, i0=2.9)
    assert settling_time(normal.t, normal.I, 2.0, band_frac(2.0)) < 1e-3
