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

from build_project import AEDT_VERSION, COMMON_VARS, CONFIGS, PROJECT, HERE

RESULTS = os.path.join(HERE, "results")

# nets saved for every scenario
NETS = ["i_meas", "u_kcounts", "in1", "in2", "out1", "gate_bus", "ret", "rail", "sup_p", "A", "B",
        "coil_a", "coil_m", "dmp2", "lem_m", "g1", "g3", "dis", "hb_ok", "latch_trip", "pg1", "ps1", "ps4"]

NO_DAMPER = {"R_damp1": "1e6ohm"}
HOLD_2A = {"sp_amps": "2", "t_sp_on": "20us"}

# name: (design, setup, stop time, {variable: value}, what it reproduces)
SCENARIOS = {
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
            design, setup, stop, over, doc = SCENARIOS[name]
            if cir is None or cir.design_name != design:
                if cir is not None:        # leave the design we are done with at its default values
                    for k, v in defaults(cir.design_name).items():
                        if str(cir[k]) != v:
                            cir[k] = v
                cir = Circuit(project=PROJECT, design=design, version=AEDT_VERSION, non_graphical=True,
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
    run(args or list(SCENARIOS))
