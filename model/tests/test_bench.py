"""The low-current bench profile (docs/bench_supply_test.md): the MOT power
stage on a current-limited bench supply (5 V, 3 A) driving ONE coil of the
pair (L ~9 uH PROVISIONAL), LEM on the 4/0 lead with one pass.

What changes against the production MOT channel:
  * the supply's constant-current (CC) limit caps the coil current;
  * the coil is one coil, not the pair -- at <= 3 A L only sets ramp and
    freewheel times of tens of us;
  * sensing is unchanged: I_FS = 200 A, 24.4 mA per ADC count, so 3 A is
    only ~123 counts; plant gain and suggested gains are the production
    ones;
  * one OUT1 count is ~0.12 A, ~5 % of a 2.5 A hold: without the dither
    the hold hunts by a few %;
  * the RUN floor gap below the dead zone (10 % of it, ~42 A worth of gate
    at G_pass) is crossed by the integrator at a rate proportional to the
    step size, so small steps from zero are slow.

Tolerance band. The 0.1 % spec is a production number (0.1 A at 100 A).
0.1 % of 2.5 A is 2.5 mA, a tenth of one ADC count (24.4 mA): the board
cannot even see it. The bench tests therefore use +-1 ADC count (24.4 mA,
~1 % of 2.5 A) as their settling band -- the measurement's resolution, not
the spec.
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
BAND = BENCH.amps_per_lsb                         # 1 ADC count, see module doc


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
    # same bank, bridge, rail and sensing as the MOT; only the coil (one of
    # the pair), the supply limit and the protection-related values differ
    same = replace(BENCH, name="mot", L=MOT.L, R_coil=MOT.R_coil,
                   I_rated=MOT.I_rated, I_supply_max=None,
                   out_clamp_counts=None)
    assert same == MOT
    assert "mot_bench" not in CHANNELS      # production table untouched
    # one coil of a series-opposed pair: L_pair/2 + M, PROVISIONAL
    assert MOT.L / 2 <= BENCH.L <= 10e-6
    assert BENCH.R_coil == pytest.approx(MOT.R_coil / 2)


def test_defaults_leave_production_unchanged():
    ch = Channel(name="x", L=16e-6, R_coil=6.4e-3, I_rated=100.0)
    assert ch.I_supply_max is None and ch.lem_turns == 1
    assert ch.out_clamp_counts is None
    for c in CHANNELS.values():
        assert c.I_supply_max is None and c.lem_turns == 1
        assert c.I_FS == pytest.approx(200.0)


def test_bench_sensing_is_the_production_scale():
    # LEM on the 4/0 lead, one pass: 3 A is only ~123 ADC counts
    assert BENCH.lem_turns == 1
    assert BENCH.I_FS == pytest.approx(200.0)
    assert BENCH.amps_per_lsb == pytest.approx(24.4e-3, rel=1e-3)
    assert BENCH.I_supply_max / BENCH.amps_per_lsb == pytest.approx(123, abs=1)


def test_lem_turns_and_in_amp_gain_multiply_the_sensitivity():
    # N primary turns: N x the ampere-turns for the same coil current. The
    # optional alternative, in-amp gain (G = 1 + 9.9k/R_G), scales the same.
    for n in (1, 2, 5, 10):
        assert replace(MOT, lem_turns=n).I_FS == pytest.approx(200.0 / n)
    assert replace(MOT, G_inamp=10.0).I_FS == pytest.approx(20.0)
    assert 1 + 9.9e3 / 1.1e3 == pytest.approx(10.0)


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
    assert energy < 50e-6                     # ~40 uJ with L ~9 uH
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
    for _ in range(4):                      # ~4 us, ~2.2 A at ~0.55 A/us
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
    # ~ L I / V_f: a few tens of us from 2.5 A (one coil, L ~9 uH)
    assert 15e-6 < BENCH.freewheel_time < 60e-6


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

def test_bench_gains_are_the_production_ones():
    # same bank, same sensor scale -> same plant gain (~4.8) and gains (the
    # coil's L is not in the small-signal loop)
    assert plant_gain(BENCH) == pytest.approx(plant_gain(MOT))
    assert 4.0 < plant_gain(BENCH) < 5.5
    assert suggest_gains(BENCH) == pytest.approx(suggest_gains(MOT))


def test_bench_gains_encode_precisely():
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
    # stable. The measured G_pass then sets the gains -- and the same
    # gains then serve the 100 A run only if G_pass there agrees.
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
    # DOCUMENTED: one OUT1 count is ~0.12 A, ~8 % of a 1.5 A hold. Whether
    # the undithered hold hunts depends on where the nearest OUT1 code's
    # current falls against the 24 mA ADC steps (in this noise-free model:
    # ~40-50 mA p-p at 1.0-1.5 A, none at some other points); with the
    # dither it never does. Either way the hold sits within one ADC count.
    count_amps = BENCH.G_pass / 8192
    ptp = {}
    for dither in (False, True):
        res = fixed_run(BENCH, lambda t: 1.5, 3e-3, i0=1.5, dither=dither)
        tail = res.I[res.t > 1e-3]
        ptp[dither] = np.ptp(tail)
        assert abs(tail.mean() - 1.5) < BENCH.amps_per_lsb
    assert ptp[False] > 1.5 * BENCH.amps_per_lsb, f"no hunting? {ptp[False]}"
    assert ptp[True] < 0.1 * count_amps                            # ~mA


def test_fixed_point_step_settles_within_one_adc_count():
    # 1 -> 2 A through the bit-exact mirror, dither on: settles within
    # +-1 ADC count (24.4 mA) in < 1 ms. (Without the dither the hold hunts
    # by ~2 counts p-p -- test above.)
    res = fixed_run(BENCH, lambda t: 2.0, 3e-3, i0=1.0, dither=True)
    ts = settling_time(res.t, res.I, 2.0, band_frac(2.0))
    assert ts is not None and ts < 1e-3, f"settled in {ts}"


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
    # 9 uH ~ 550 A/ms compliance rate of one coil), down 50 A/ms (below
    # V_f/L ~ 65-90 A/ms for V_f 0.6-0.8 V).
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
