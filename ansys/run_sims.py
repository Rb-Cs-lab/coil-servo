"""Run the bench test scenarios in Ansys Circuit and save the waveforms.

    ansys\\.venv\\Scripts\\python.exe ansys\\run_sims.py                 # all bench scenarios
    ansys\\.venv\\Scripts\\python.exe ansys\\run_sims.py turnoff step_2A # some of them
    ansys\\.venv\\Scripts\\python.exe ansys\\run_sims.py --list

Each scenario is a set of design-variable values (the same variables you can
edit by hand in the Ansys GUI) plus which transient setup to run. Waveforms
go to ansys/results/<scenario>.csv; compare.py turns them into plots and a
table against the bench measurements.
"""
from __future__ import annotations

import os
import sys
import time

from build_project import AEDT_VERSION, COMMON_VARS, CONFIGS, HERE, project_of

RESULTS = os.path.join(HERE, "results")

# nets saved for every scenario
NETS = ["i_meas", "u_kcounts", "in1", "in2", "out1", "gate_bus", "ret", "rail", "rail_in", "sup_p", "A", "B",
        "coil_a", "coil_m", "dmp2", "lem_m", "g1", "g2", "g3", "g4", "dis", "hb_ok", "latch_trip", "pg1", "ps1", "ps4",
        "dio0", "dio1", "dio6", "run", "ol"]

NO_DAMPER = {"R_damp1": "1e6ohm"}
HOLD_2A = {"sp_amps": "2", "t_sp_on": "20us"}

RAIL5 = {"V_opamp": "5.184V"}          # op-amp rail before 11-13
T_REV = 1.4e-3                          # when the reversal is requested, seconds
REV_BASE = {"sp_amps": "2", "t_sp_len": "1290us", "sp2_amps": "-2", "t_sp2_on": "1400us", "slew2": "1e12"}


def _reversal(holdoff_us, settle_us):
    """Timing of a flip, as the firmware does it: wait until the current is inside the
    +-0.49 A zero window (taken from the run without a flip, rev_pass1), then the hold-off,
    then bridge off 1 us - polarity change - 1 us bridge on, then the settling pause."""
    def derive(load):
        import numpy as np
        s = load("rev_pass1")
        i = s.i_coil
        k = np.where((s.t > T_REV) & (np.abs(i) < 0.49))[0]
        t_gap = float(s.t[k[0]]) + holdoff_us * 1e-6
        us = lambda t: f"{t * 1e6:.2f}us"
        return {"t_en_off": us(t_gap), "en_gap": "2us", "t_pol": us(t_gap + 1e-6),
                "t_run_off": us(t_gap), "t_run_len": us(2e-6 + settle_us * 1e-6)}
    return derive


# name: (design, setup, stop time, {variable: value}, what it reproduces[, function giving more values from an earlier run])
SCENARIOS = {
    # --- Test 0: parts on their own ---
    "safety_board": ("bench", "Tran_12ms", "12ms", {"sp_amps": "0", "t_hb_off": "1.2ms", "t_hb_len": "8.3ms", "t_rst_per": "11.2ms"},
                     "Test 0d: safety-board light sequence (power-up, reset, enable, pull heartbeat, re-plug, reset)"),
    "reset_no_heartbeat": ("bench", "Tran_12ms", "12ms", {"sp_amps": "0", "t_hb_off": "1.2ms", "t_hb_len": "8.8ms", "t_rst_per": "9.5ms"},
                           "reset pressed while the heartbeat is missing: the cut-off clears and is not triggered again"),
    "hand_forward": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.85", **RAIL5}, "Test 0f: current through the coil, forward"),
    "hand_reverse": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.85", "t_pol": "0s", **RAIL5}, "Test 0f: the same, bridge reversed"),
    # --- Test 1: gate stage ---
    "gate_sweep_term": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "0", "ol_step": "8.191", "t_ol_step": "100us", "t_ol_ramp": "2.5ms", "t_en_off": "10.02us", **RAIL5},
                        "Test 1b: OUT1 swept 0 to full scale, gate voltage, terminator fitted"),
    "gate_sweep_unterm": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "0", "ol_step": "8.191", "t_ol_step": "100us", "t_ol_ramp": "2.5ms", "t_en_off": "10.02us", "R_term": "1e9ohm", **RAIL5},
                          "Test 1: the same without the 50 ohm terminator"),
    # --- Test 3 day 1 / Q5: current against OUT1 ---
    "bank_scan_cold": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.4", "ol_step": "0.6", "t_ol_step": "200us", "t_ol_ramp": "2.6ms"},
                       "Q5: slow OUT1 sweep 3400 to 4000 counts, cold transistors, 12 V op-amp rail"),
    "bank_scan_warm": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.4", "ol_step": "0.6", "t_ol_step": "200us", "t_ol_ramp": "2.6ms", "vt_pass": "3.868"},
                       "Q5: the same with warm transistors"),
    # --- 3d: controller settings (before the damper was fitted) ---
    "step_1A_kp15": ("bench", "Tran_3ms", "3ms", {"sp_amps": "1", "kp": "15.6", "ki": "471e3", **RAIL5, **NO_DAMPER}, "3d: 0 -> 1 A at half the gains"),
    "step_1A_kp31": ("bench", "Tran_3ms", "3ms", {"sp_amps": "1", **RAIL5, **NO_DAMPER}, "3d: 0 -> 1 A at the chosen gains, no damper"),
    "step_1A_kp62": ("bench", "Tran_3ms", "3ms", {"sp_amps": "1", "kp": "62.4", "ki": "1884e3", **RAIL5, **NO_DAMPER}, "3d: 0 -> 1 A at twice the gains (bench: 21 kHz oscillation)"),
    "step_2p5A": ("bench", "Tran_3ms", "3ms", {"sp_amps": "2.5", "trip_amps": "3.5", **RAIL5, **NO_DAMPER}, "3d: 0 -> 2.5 A"),
    # --- extra checks: steps over the range, up and down (firmware before the zero snap) ---
    "step_0p5A": ("bench", "Tran_3ms", "3ms", {"sp_amps": "0.5", **RAIL5, **NO_DAMPER}, "0 -> 0.5 A"),
    "step_1p5A": ("bench", "Tran_3ms", "3ms", {"sp_amps": "1.5", **RAIL5, **NO_DAMPER}, "0 -> 1.5 A"),
    "step_2p2A": ("bench", "Tran_3ms", "3ms", {"sp_amps": "2.2", **RAIL5, **NO_DAMPER}, "0 -> 2.2 A"),
    "down_2p2_to_1A": ("bench", "Tran_3ms", "3ms", {"sp_amps": "2.2", "sp2_amps": "-1.2", "t_sp2_on": "1.5ms", "slew2": "50e3", "snap_amps": "-1", **RAIL5, **NO_DAMPER}, "2.2 -> 1 A (ramp 50 A/ms)"),
    "down_1_to_0_nosnap": ("bench", "Tran_3ms", "3ms", {"sp_amps": "1", "t_sp_len": "1.4ms", "snap_amps": "-1", **RAIL5, **NO_DAMPER}, "1 -> 0 A without the zero snap"),
    "down_2_to_0_nosnap": ("bench", "Tran_3ms", "3ms", {"sp_amps": "2", "t_sp_len": "1.4ms", "snap_amps": "-1", **RAIL5, **NO_DAMPER}, "2 -> 0 A without the zero snap (the slow tail)"),
    # --- 3g: reversal +2 -> -2 A ---
    "rev_pass1": ("bench", "Tran_3ms", "3ms", dict(REV_BASE), "helper: setpoint sign changed, no flip (gives the time the current reaches the zero window)"),
    "reversal": ("bench", "Tran_3ms", "3ms", dict(REV_BASE), "3g: reversal +2 -> -2 A, setpoint sign and flip together, current settings (66 us hold-off, 100 us settle)", _reversal(66, 100)),
    "reversal_fast": ("bench", "Tran_3ms", "3ms", dict(REV_BASE), "fastest reversal: hold-off 8 us, settle 0", _reversal(8, 0)),
    # --- Q1: small steps of OUT1 at a hold (controller off, damper fitted) ---
    "ss_0p5A": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.750", "ol_step": "0.010", "t_ol_step": "1.5ms"}, "Q1: +10 counts at ~0.5 A"),
    "ss_1A": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.836", "ol_step": "0.010", "t_ol_step": "1.5ms"}, "Q1: +10 counts at ~1 A"),
    "ss_2A": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.946", "ol_step": "0.010", "t_ol_step": "1.5ms"}, "Q1: +10 counts at ~2 A"),
    # --- the bench ringing: fixed OUT1, no damper, with a larger drain-gate capacitance in the pass FETs ---
    "ring_3800": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.800", "cgd_pass": "17e-9", "trip_amps": "10", **RAIL5, **NO_DAMPER}, "ringing test at 3800 counts (bench: 0.71 A, 20 kHz, +-0.08 A)"),
    "ring_3870": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.870", "cgd_pass": "17e-9", "trip_amps": "10", **RAIL5, **NO_DAMPER}, "3870 counts (1.23 A, 15 kHz, +-0.11 A)"),
    "ring_3935": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.935", "cgd_pass": "17e-9", "trip_amps": "10", **RAIL5, **NO_DAMPER}, "3935 counts (1.93 A, 11.5 kHz, +-0.15 A)"),
    "ring_3935_damper": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.935", "cgd_pass": "17e-9", "trip_amps": "10", **RAIL5}, "the same with the damper fitted (bench: ringing gone)"),
    "ring_3935_cgd5n": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.935", "cgd_pass": "5e-9", "trip_amps": "10", **RAIL5, **NO_DAMPER}, "3935 counts with 5 nF instead of 17 nF"),
    # --- the first set (11-12 tests with the op-amp on 5.18 V) ---
    "ol_3800": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.800", "V_opamp": "5.184V"}, "open loop, OUT1 = 3800 counts (bench 11-12: 0.71 A)"),
    "ol_3870": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.870", "V_opamp": "5.184V"}, "open loop, 3870 counts (1.23 A)"),
    "ol_3935": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.935", "V_opamp": "5.184V"}, "open loop, 3935 counts (1.93 A)"),
    "ol_3935_nodamper": ("bench", "Tran_3ms", "3ms", {"ol_kcounts": "3.935", "V_opamp": "5.184V", **NO_DAMPER}, "same without the damper: does the model ring at 10-20 kHz?"),
    "turnoff": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.950", "V_opamp": "5.184V", "t_run_off": "200us"}, "OUT1 -> 0 from 2 A, bridge closed, damper fitted (Test 3c)"),
    "turnoff_nodamper": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.900", "V_opamp": "5.184V", "t_run_off": "200us", **NO_DAMPER}, "OUT1 -> 0 from 1.5 A, no damper (Test 3f)"),
    "bridge_open": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.950", "V_opamp": "5.184V", "t_en_off": "200us"}, "bridge opened at 2 A, damper fitted (Test 3c)"),
    "bridge_open_nodamper": ("bench", "Tran_600us", "600us", {"ol_kcounts": "3.950", "V_opamp": "5.184V", "t_en_off": "200us", **NO_DAMPER}, "bridge opened at 2 A, no damper (Test 3h)"),
    "step_1A": ("bench", "Tran_3ms", "3ms", {"sp_amps": "1", "V_opamp": "5.184V"}, "closed loop 0 -> 1 A (bench: 90 % 0.61-0.73 ms, 98 % 0.81-1.04 ms)"),
    "step_2A": ("bench", "Tran_3ms", "3ms", {"sp_amps": "2", "V_opamp": "5.184V"}, "closed loop 0 -> 2 A (bench: 90 % 0.45 ms, 98 % 0.65 ms)"),
    "pulse_snap": ("bench", "Tran_3ms", "3ms", {"sp_amps": "2", "t_sp_len": "1.5ms"}, "2 A pulse, zero-setpoint snap at the end (rev 5: 2 % in 0.14 ms)"),
    "trip": ("bench", "Tran_3ms", "3ms", {"sp_amps": "2.9", "u_clamp": "4100"}, "step to 2.9 A with the 2.8 A trip (bench: fired at ~2.85 A)"),
    "hb_pull": ("bench", "Tran_12ms", "12ms", {**HOLD_2A, "t_hb_off": "1.2ms"}, "heartbeat wire pulled during a 2 A hold: bridge cut 7.6 ms later"),
    # --- predictions for the MOT pair on the SE-600-5 (NOT built; the bank law is extrapolated 50x) ---
    "mot_turnoff_100A": ("mot", "Tran_3ms", "3ms", {"ol_kcounts": "6.04", "trip_amps": "150", "t_run_off": "600us"}, "PREDICTION: OUT1 -> 0 from ~100 A, bridge closed, proposed damper"),
    "mot_bridge_open_100A": ("mot", "Tran_3ms", "3ms", {"ol_kcounts": "6.04", "trip_amps": "150", "t_en_off": "600us"}, "PREDICTION: bridge opened at ~100 A (safety-board cut-off)"),
    "in1_lost": ("bench", "Tran_3ms", "3ms", {**HOLD_2A, "t_in1_off": "1.5ms"}, "IN1 cable loses contact during a 2 A hold (the 5 A surge hypothesis)"),
    # --- fast turn-off parts (coil_servo_fast_turnoff.aedt): 5KP30A rail bar <-> return bar, DSS2X101-015A in the
    #     supply + lead. NOT fitted yet -- all of these are PREDICTIONS. Same settings as the runs they are compared with.
    "ft_step_2A": ("bench_ft", "Tran_3ms", "3ms", {"sp_amps": "2", "V_opamp": "5.184V"}, "PREDICTION: closed loop 0 -> 2 A with the blocking diode in the supply lead"),
    "ft_turnoff": ("bench_ft", "Tran_600us", "600us", {"ol_kcounts": "3.950", "V_opamp": "5.184V", "t_run_off": "200us"}, "PREDICTION: OUT1 -> 0 from 2 A, bridge closed (should not change)"),
    "ft_bridge_open": ("bench_ft", "Tran_600us", "600us", {"ol_kcounts": "3.950", "V_opamp": "5.184V", "t_en_off": "200us"}, "PREDICTION: bridge opened at 2 A, damper fitted"),
    "ft_bridge_open_nodamper": ("bench_ft", "Tran_600us", "600us", {"ol_kcounts": "3.950", "V_opamp": "5.184V", "t_en_off": "200us", **NO_DAMPER}, "PREDICTION: bridge opened at 2 A, no damper"),
    "mot_ft_turnoff_100A": ("mot_ft", "Tran_3ms", "3ms", {"ol_kcounts": "6.04", "trip_amps": "150", "t_run_off": "600us"}, "PREDICTION: OUT1 -> 0 from ~100 A, bridge closed"),
    "mot_ft_bridge_open_100A": ("mot_ft", "Tran_3ms", "3ms", {"ol_kcounts": "6.04", "trip_amps": "150", "t_en_off": "600us"}, "PREDICTION: bridge opened at ~100 A, proposed damper fitted"),
    # what the blocking diode costs: slow OUT1 sweep, largest current the 5 V supply can push, without and with the diode
    "mot_scan": ("mot", "Tran_12ms", "12ms", {"ol_kcounts": "4", "ol_step": "4", "t_ol_step": "200us", "t_ol_ramp": "11ms", "trip_amps": "200"}, "PREDICTION: current against OUT1 up to the supply's limit, as planned for Test 4"),
    "mot_ft_scan": ("mot_ft", "Tran_12ms", "12ms", {"ol_kcounts": "4", "ol_step": "4", "t_ol_step": "200us", "t_ol_ramp": "11ms", "trip_amps": "200"}, "PREDICTION: the same with the blocking diode"),
    "mot_ft_bridge_open_100A_nodamper": ("mot_ft", "Tran_3ms", "3ms", {"ol_kcounts": "6.04", "trip_amps": "150", "t_en_off": "600us", **NO_DAMPER}, "PREDICTION: bridge opened at ~100 A, no damper"),
}


def defaults(config):
    v = {k: val for k, (val, _) in COMMON_VARS.items()}
    v.update({k: val for k, (val, _) in CONFIGS[config]["vars"].items()})
    return v


def run(names):
    from ansys.aedt.core import Circuit
    os.makedirs(RESULTS, exist_ok=True)
    cir = None
    done = []
    try:
        for name in names:
            design, setup, stop, over, doc = SCENARIOS[name][:5]
            if len(SCENARIOS[name]) > 5:        # values that depend on an earlier run
                from simdata import Sim
                over = {**over, **SCENARIOS[name][5](lambda n: Sim(os.path.join(RESULTS, n + ".csv")))}
            if cir is None or cir.design_name != design:
                if cir is not None:        # leave the design we are done with at its default values
                    for k, v in defaults(cir.design_name).items():
                        if str(cir[k]) != v:
                            cir[k] = v
                if cir is not None and project_of(design) != project_of(cir.design_name):
                    cir.save_project()      # going on to the other project file
                cir = Circuit(project=project_of(design), design=design, version=AEDT_VERSION, non_graphical=True,
                              new_desktop=cir is None, student_version=True)
            base = defaults(design)
            for k, v in {**base, **over}.items():
                if str(cir[k]) != v:
                    cir[k] = v
            # Ansys reuses an old result when only the model file changed. A design
            # variable that is different on every run forces a fresh solve.
            cir["run_id"] = str(int(time.time()) % 1000000)
            t0 = time.time()
            ok = cir.analyze(setup)
            exprs = [f"V({n})" for n in NETS]
            avail = set(cir.post.available_report_quantities(solution=setup))
            exprs = [e for e in exprs if e in avail]
            rep = cir.post.reports_by_category.standard(exprs, setup)
            rep.domain = "Time"
            rep.time_start = "0s"
            rep.time_stop = stop
            rep.create(name)
            path = cir.post.export_report_to_csv(RESULTS, name)
            cir.post.delete_report(name)
            print(f"{name}: analyze={ok} {time.time() - t0:.1f} s -> {path}  [{len(exprs)}/{len(NETS)} nets]")
            if not exprs:
                print("   no data. Solver messages:", list(cir.odesktop.GetMessages("", "", 2))[-8:])
            done.append(name)
        if cir is not None:
            for k, v in defaults(cir.design_name).items():
                if str(cir[k]) != v:
                    cir[k] = v
            cir.save_project()
    finally:
        if cir is not None:
            cir.release_desktop(close_projects=True, close_desktop=True)
    return done


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        for k, v in SCENARIOS.items():
            print(f"{k:22s} {v[4]}")
        sys.exit(0)
    # default: the scenarios of the as-built project; name the ft_ ones to run those
    run(args or [n for n, v in SCENARIOS.items() if not v[0].endswith("_ft")])
