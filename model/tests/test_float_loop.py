"""Closed-loop tests of the float reference on the MVP plant.

Loop targets: crossover 2-5 kHz, settle to 0.1 % in < 1 ms, stability
< 0.1 %. The MVP adds a unipolar actuator (OUT1 only, PI floored at zero and
anti-wound there), a dead zone in the pass bank, and slow freewheel
turn-down. Gains are analysis.suggest_gains, i.e. the commissioned values
rescaled by the bank's plant gain -- real gains come from the measured step
response.

Two behaviours are DOCUMENTED here rather than required to meet spec,
because fixing them is a design decision for rewrite step 3
(mvp_architecture.md 3.1):
  * from zero, the integrator must climb the dead zone before any current
    flows (a 0 -> 10 A step starts ~2.7 ms late); an integrator floor just
    below the dead zone restores the spec;
  * a large down-STEP undershoots, because the integrator winds down while
    the coil freewheels slowly; a setpoint RAMP no faster than the freewheel
    rate does not.
"""

from dataclasses import replace

import numpy as np
import pytest

from coil_servo_model import CHANNELS, FloatPI, Plant, T_S, run_loop
from coil_servo_model.analysis import (crossover_and_margin, overshoot_frac,
                                       plant_gain, settling_time, suggest_gains)

MOT = CHANNELS["mot"]
DEADBAND_COUNTS = 8   # PROVISIONAL


def make_pi(ch, floor=0.0, gains_from=None):
    kp, ki_tick = suggest_gains(gains_from or ch)
    return FloatPI(kp, ki_tick, clamp=ch.clamp_counts / 8192.0, floor=floor)


def run(ch, setpoint_fn, t_end, i0=0.0, floor=0.0, gains_from=None):
    """Closed loop; with i0 > 0 the loop starts settled at i0 amps."""
    pi = make_pi(ch, floor, gains_from)
    if i0 > 0.0:
        pi.integrator = ch.out1_for_current(i0)
    return run_loop(ch, pi, DEADBAND_COUNTS, t_end, setpoint_fn,
                    plant=Plant(ch, i0=i0))


def check_invariants(res, ch, floor=0.0):
    assert np.all(res.v2 == 0.0), "OUT2 driven in the MVP"
    assert np.all(res.u >= floor - 1e-12), "PI output below its floor"
    clamp = ch.clamp_counts / 8192.0
    assert np.all(res.u <= clamp + 1e-12)
    assert np.all(res.v1 <= clamp + 1e-12)


def first_current_time(res, thresh=0.01):
    idx = np.argmax(res.I > thresh)
    return res.t[idx] if res.I[idx] > thresh else None


@pytest.mark.parametrize("name", ["mot", "z_shim", "x_shim", "y_shim"])
@pytest.mark.parametrize("frac", [1.1, 0.9])
def test_small_steps_from_an_operating_point_meet_spec(name, frac):
    # +-10 % around half of rated: the regime the experiment lives in.
    ch = CHANNELS[name]
    i0 = 0.5 * ch.I_rated
    target = frac * i0
    res = run(ch, lambda t: target, 2e-3, i0=i0)
    check_invariants(res, ch)
    ts = settling_time(res.t, res.I, target)
    assert ts is not None and ts < 1e-3, f"{name} {frac}: settled in {ts}"
    if frac > 1:
        assert overshoot_frac(res.I, target) < 0.01
    else:
        assert np.min(res.I) > 0.99 * target, "undershoot on a down-step"


def test_full_step_is_compliance_limited_and_meets_spec():
    # 0 -> 100 A on the 5 V rail: the current ramps at the compliance limit
    # (~0.4 ms), no overshoot, inside 1 ms.
    res = run(MOT, lambda t: MOT.I_rated, 2e-3)
    check_invariants(res, MOT)
    ts = settling_time(res.t, res.I, MOT.I_rated)
    assert ts is not None and ts < 1e-3, f"settled in {ts}"
    assert overshoot_frac(res.I, MOT.I_rated) < 0.01


def test_small_step_from_zero_is_dead_zone_delayed():
    # DOCUMENTED: with the PI floored at zero the integrator climbs ~0.44 of
    # full scale of dead zone before any current flows.
    res = run(MOT, lambda t: 10.0, 5e-3)
    check_invariants(res, MOT)
    start = first_current_time(res)
    assert start is not None and start > 2e-3, f"current started at {start}"
    ts = settling_time(res.t, res.I, 10.0)
    assert ts is not None and ts > 1e-3


@pytest.mark.parametrize("target", [10.0, 30.0, 100.0])
def test_dead_zone_floor_restores_spec_from_zero(target):
    # Candidate remedy for rewrite step 3: integrator (and output) floored at
    # 90 % of the dead zone -- OUT1 then rests just below conduction.
    floor = 0.9 * MOT.dead_zone
    res = run(MOT, lambda t: target, 3e-3, floor=floor)
    check_invariants(res, MOT, floor=floor)
    ts = settling_time(res.t, res.I, target)
    assert ts is not None and ts < 1e-3, f"{target} A: settled in {ts}"


def test_pi_never_commands_below_zero():
    # Big down-step: the output floors at 0 and the integrator is anti-wound
    # there -- it never goes negative.
    pi = make_pi(MOT)
    pi.integrator = MOT.out1_for_current(80.0)
    res = run_loop(MOT, pi, DEADBAND_COUNTS, 4e-3, lambda t: 5.0,
                   plant=Plant(MOT, i0=80.0))
    check_invariants(res, MOT)
    assert np.min(res.integrator) >= 0.0


def test_large_down_step_undershoots_but_a_ramp_does_not():
    # DOCUMENTED: an 80 -> 20 A step undershoots towards zero (integrator
    # winds down during the slow freewheel decay). A setpoint ramp at
    # 50 A/ms, below the freewheel rate, tracks without undershoot.
    step = run(MOT, lambda t: 20.0, 6e-3, i0=80.0)
    check_invariants(step, MOT)
    assert np.min(step.I) < 5.0
    assert settling_time(step.t, step.I, 20.0) is not None

    ramp = lambda t: max(20.0, 80.0 - 50e3 * t)
    res = run(MOT, ramp, 3e-3, i0=80.0)
    check_invariants(res, MOT)
    assert np.min(res.I) > 19.5
    ts = settling_time(res.t, res.I, 20.0)
    assert ts is not None and ts < 1.2e-3 + 1e-3   # ramp end + 1 ms


def test_steady_hold_has_no_limit_cycle():
    res = run(MOT, lambda t: 50.0, 3e-3, i0=50.0)
    tail = res.I[res.t > 1e-3]
    assert np.max(np.abs(tail - 50.0)) < 1e-3 * 50.0   # inside 0.1 %
    assert np.std(tail) < 1e-3


def test_anti_windup_at_the_command_clamp():
    # Asking for more than rated: the command rides the clamp (which asks
    # for ~rated current), the integrator stays near it, current ~100 A.
    res = run(MOT, lambda t: 150.0, 2e-3)
    check_invariants(res, MOT)
    clamp = MOT.clamp_counts / 8192.0
    assert np.max(res.integrator) < clamp + 0.05
    late = res.I[res.t > 1e-3]
    assert np.all(np.abs(late - MOT.I_rated) < 0.02 * MOT.I_rated)


@pytest.mark.parametrize("g_fs", [20.0, 1e6])
def test_stable_across_the_pass_bank_gain_range(g_fs):
    # g_fs is PROVISIONAL: 20 A/V (plant gain ~3) to ballast-limited (~8).
    # Gains tuned for the nominal bank must stay stable and in spec at both
    # ends.
    ch = replace(MOT, g_fs=g_fs)
    kp, ki_tick = suggest_gains(MOT)
    f_c, pm = crossover_and_margin(ch, kp, ki_tick / T_S)
    assert 1e3 < f_c < 10e3, f"crossover {f_c:.0f} Hz"
    assert pm > 45, f"phase margin {pm:.0f} deg"
    res = run(ch, lambda t: 55.0, 2e-3, i0=50.0, gains_from=MOT)
    ts = settling_time(res.t, res.I, 55.0)
    assert ts is not None and ts < 1e-3
    assert overshoot_frac(res.I, 55.0) < 0.05


@pytest.mark.parametrize("name", ["mot", "z_shim", "x_shim", "y_shim"])
def test_small_signal_margins(name):
    ch = CHANNELS[name]
    kp, ki_tick = suggest_gains(ch)
    f_c, pm = crossover_and_margin(ch, kp, ki_tick / T_S)
    assert 2e3 < f_c < 5e3, f"{name}: crossover {f_c:.0f} Hz"
    assert pm > 45, f"{name}: phase margin {pm:.0f} deg"


def test_gains_are_the_commissioned_ones_rescaled_by_plant_gain():
    kp, ki_tick = suggest_gains(MOT)
    K = plant_gain(MOT)
    assert 3.0 < K < 8.0
    assert kp == pytest.approx(0.5 / K)
    assert ki_tick / T_S == pytest.approx(15080.0 / K, rel=1e-3)
