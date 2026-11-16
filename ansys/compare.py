"""Compare the Ansys results (ansys/results/*.csv) with the bench measurements.

    ansys\\.venv\\Scripts\\python.exe ansys\\compare.py

Writes plots (ansys/results/*.png) and a report, ansys/results/comparison.html,
with a table "model / bench" for every number that was measured on the bench.
Bench traces are overlaid when the raw logs are on this PC (logs/ is not in
git); without them only the model curves are drawn.
"""
from __future__ import annotations

import csv
import html
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "results")
LOGS = os.path.join(REPO, "logs")
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, HERE)
from plotfit import place_legend, settle_legends  # noqa: E402
from simdata import Sim  # noqa: E402

LATENCY = 3.6e-6          # controller latency in the model
C_SIM, C_BENCH, C_AUX = "#1F497D", "#C0504D", "#7F7F7F"
rows: list[tuple[str, str, str, str, str]] = []   # (test, quantity, model, bench, note)
figs: list[tuple[str, str]] = []                   # (file, caption)


def sim(name):
    p = os.path.join(RESULTS, name + ".csv")
    return Sim(p) if os.path.exists(p) else None


def bench_json(name):
    p = os.path.join(LOGS, name)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def bench_csv(name):
    p = os.path.join(LOGS, name)
    if not os.path.exists(p):
        return None
    d = np.genfromtxt(p, delimiter=",", names=True)
    return d


def smooth(y, n):
    y = np.asarray(y, float)
    if n <= 1:
        return y
    k = np.ones(n) / n
    out = np.convolve(y, k, mode="same")
    out[: n // 2] = np.nan      # the average is not valid at the ends of the record
    out[-(n // 2):] = np.nan
    return out


def cross(t, y, level, t0, rising):
    m = t >= t0
    tt, yy = t[m], y[m]
    k = np.where((yy >= level) if rising else (yy <= level))[0]
    return float(tt[k[0]] - t0) if len(k) else float("nan")


def finish(fig, fname, caption):
    settle_legends(fig)
    fig.savefig(os.path.join(RESULTS, fname), dpi=130)
    plt.close(fig)
    figs.append((fname, caption))


def add(test, quantity, model, bench, note=""):
    rows.append((test, quantity, model, bench, note))


# ---------------------------------------------------------------------------
def static_points():
    pts = [("ol_3800", 3800, 0.71), ("ol_3870", 3870, 1.23), ("ol_3935", 3935, 1.93)]
    fig, ax = plt.subplots(figsize=(6.2, 4.0), layout="constrained")
    xs, ys, yb = [], [], []
    for name, counts, i_bench in pts:
        s = sim(name)
        if s is None:
            continue
        i = float(s.i_coil[s.t > 0.6 * s.t[-1]].mean())
        xs.append(counts); ys.append(i); yb.append(i_bench)
        add("Fixed OUT1 (open loop)", f"current at {counts} counts", f"{i:.2f} A", f"{i_bench:.2f} A", "bench 11-12, coil lifted")
    s = sim("turnoff")
    if s is not None:
        i = s.at("coil_m", 190e-6)
        i = float(np.interp(190e-6, s.t, s.i_coil))
        xs.append(3950); ys.append(i); yb.append(2.0)
        add("Fixed OUT1 (open loop)", "current at 3950 counts", f"{i:.2f} A", "2.0 A", "")
        add("Fixed OUT1 (open loop)", "rail bar at 2 A", f"{s.at('rail', 190e-6):.2f} V", "4.60 V", "supply leads drop 0.2 ohm x 2 A")
    if xs:
        ax.plot(xs, yb, "o", color=C_BENCH, label="bench")
        ax.plot(xs, ys, "s-", color=C_SIM, mfc="none", label="Ansys model")
        ax.set_xlabel("OUT1 command [counts]"); ax.set_ylabel("coil current [A]")
        ax.set_title("Pass bank: current at a fixed OUT1")
        place_legend(ax)
        finish(fig, "cmp_static.png", "Current for a fixed OUT1 command (controller off). The model uses the bank law fitted to the 11-11/12 bench scans, so this mainly checks that the circuit around the bank is right.")
    s = sim("ol_3935_nodamper")
    if s is not None:
        m = s.t > 0.5 * s.t[-1]
        pp = float(np.ptp(s.i_coil[m])) * 1e3
        add("Fixed OUT1, damper removed, datasheet capacitances", "10-20 kHz ringing at 1.9 A", f"none ({pp:.1f} mA p-p)", "+-116 to +-165 mA at 12 kHz",
            "see the ringing rows: appears when the drain-gate capacitance is raised")


def turnoff():
    s = sim("turnoff")
    if s is None:
        return
    t0 = s.var_si("t_run_off") + LATENCY
    i = s.i_coil
    i0 = float(np.interp(t0 - 5e-6, s.t, i))
    tz = cross(s.t, i, 0.0, t0, False)
    m = s.t > t0
    imin = float(i[m].min())
    add("OUT1 -> 0 at 2 A, bridge closed", "time to zero current", f"{tz*1e6:.0f} us", "~170 us", "freewheel through the bridge diodes")
    add("OUT1 -> 0 at 2 A, bridge closed", "fall rate", f"{i0/tz*1e-3:.1f} mA/us", "12-13 mA/us", "")
    add("OUT1 -> 0 at 2 A, bridge closed", "swing below zero (into the damper)", f"{imin:.2f} A", "~ -0.2 A", "bench: for ~100 us")
    add("OUT1 -> 0 at 2 A, bridge closed", "drain bar while the current falls", f"{s.at('ret', t0+80e-6):.2f} V", "~5.9 V", "rail + one diode drop")
    add("OUT1 -> 0 at 2 A, bridge closed", "drain bar peak at switch-off", f"{s['ret'][m].max():.1f} V", "12.1 V", "model: the supply-lead inductance, caught by the 5KP12CA")
    b = bench_json("t3c_snap_3950_lem_inside.json")
    fig, axs = plt.subplots(2, 1, figsize=(7.2, 6.4), sharex=True, layout="constrained")
    ts = (s.t - t0) * 1e6
    if b is not None:
        # the scope did not trigger on the switch-off: find it from the drain bar stepping up
        tb = np.array(b["t"])
        drain = np.array(b["drain"], float)
        k = np.where(drain > drain[:50].mean() + 0.4)[0]
        tb = (tb - (tb[k[0]] if len(k) else 0.0)) * 1e6
        axs[0].plot(tb, smooth(b["in1_amps"], 20), color=C_BENCH, label="bench, IN1 (10 us average)")
        # this scope channel reads the idle rail 0.24 V high (5.30 V for 5.06 V): shown as recorded
        axs[1].plot(tb, drain, color=C_BENCH, label="bench, drain bar (scope clipped at 7.4 V)")
        fall = (tb > 20) & (tb < 120)
        vcoil = float(np.mean(np.array(b["nodeA"])[fall] - np.array(b["nodeB"])[fall]))
        rate = -float(np.polyfit(tb[fall], np.array(b["in1_amps"])[fall], 1)[0]) * 1e3
        add("OUT1 -> 0 at 2 A, bridge closed", "voltage across the coil while it falls", f"{s.at('ret', t0+60e-6) - s.at('rail', t0+60e-6):.2f} V", f"{vcoil:.2f} V",
            f"bench: {vcoil:.2f} V and {rate:.1f} mA/us give L = {(vcoil-0.03)/rate*1e3:.0f} uH")
    axs[0].plot(ts, i, color=C_SIM, label="Ansys model, coil current")
    axs[1].plot(ts, s["ret"], color=C_SIM, label="Ansys model, drain bar")
    axs[1].plot(ts, s["rail"], color=C_AUX, lw=0.8, label="Ansys model, rail bar")
    axs[0].set_ylabel("coil current [A]"); axs[1].set_ylabel("voltage [V]"); axs[1].set_xlabel("time after OUT1 -> 0 [us]")
    axs[0].set_xlim(-50, 390); axs[0].axhline(0, color="k", lw=0.5)
    axs[0].set_title("Switch-off from 2 A with the bridge closed (Test 3c)")
    for ax in axs:
        place_legend(ax)
    finish(fig, "cmp_turnoff.png", "OUT1 set to zero at 2 A with the bridge left closed: the coil current circulates through the bridge and dies away.")
    s2 = sim("turnoff_nodamper")
    if s2 is not None:
        i2 = s2.i_coil
        i20 = float(np.interp(t0 - 5e-6, s2.t, i2))
        tz2 = cross(s2.t, i2, 0.0, t0, False)
        add("OUT1 -> 0 at 1.5 A, no damper", "time to zero / rate", f"{tz2*1e6:.0f} us / {i20/tz2*1e-3:.1f} mA/us", "100-160 us / 8-12 mA/us", "Test 3f")


def bridge_open():
    for name, bfile, title, ref in (
        ("bridge_open", "t3c_bridge_open_2A_20us.json", "Bridge opened at 2 A, damper fitted", ("~25 us", "~ -0.3 A")),
        ("bridge_open_nodamper", "t3_hb_trip_2A_20us.json", "Bridge opened at 2 A, no damper", ("10-13 us", "-")),
    ):
        s = sim(name)
        if s is None:
            continue
        t0 = s.var_si("t_en_off")
        i = s.i_coil
        m = s.t > t0
        tz = cross(s.t, i, 0.0, t0, False)
        add(title, "time to zero current", f"{tz*1e6:.0f} us", ref[0], "")
        if ref[1] != "-":
            add(title, "swing below zero", f"{float(i[m].min()):.2f} A", ref[1], "")
        add(title, "drain bar while the coil discharges", f"{s.at('ret', t0 + 0.5*tz):.2f} V", "~ -0.5 V", "model: the bank conducts backwards through its channel, hardly any drop")
        if name == "bridge_open_nodamper":
            add(title, "rail bar jump", f"+{s['rail'][m].max() - s.at('rail', t0-5e-6):.0f} V for < 1 us", "+2.5 to +3.2 V for ~10 us",
                "DISAGREES: see 'What does not match' below")
        b = bench_json(bfile)
        fig, axs = plt.subplots(2, 1, figsize=(7.2, 6.4), sharex=True, layout="constrained")
        ts = (s.t - t0) * 1e6
        if b is not None:
            tb = np.array(b["t"]) * 1e6
            axs[0].plot(tb, smooth(b["in1_amps"], 10), color=C_BENCH, label="bench, IN1 (2 us average)")
            axs[1].plot(tb, b["nodeA"], color=C_BENCH, label="bench, coil's low-side node")
            axs[1].plot(tb, b["drain"], color="#E6A29F", label="bench, drain bar")
        axs[0].plot(ts, i, color=C_SIM, label="Ansys model, coil current")
        # the bench ran in the other bridge direction: its "node A" is the model's node B
        axs[1].plot(ts, s["B"], color=C_SIM, label="Ansys model, coil's low-side node")
        axs[1].plot(ts, s["ret"], color="#8FA9CC", label="Ansys model, drain bar")
        axs[0].set_ylabel("coil current [A]"); axs[1].set_ylabel("voltage [V]"); axs[1].set_xlabel("time after the bridge opens [us]")
        axs[0].set_xlim(-20, 150); axs[0].axhline(0, color="k", lw=0.5)
        axs[1].set_ylim(-3, 14)
        axs[0].set_title(title)
        for ax in axs:
            place_legend(ax)
        finish(fig, f"cmp_{name}.png", title + ": all four bridge switches turned off while current flows (what the safety board does).")


def steps():
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 4.2), layout="constrained")
    for ax, name, sp, bfile, ref in (
        (axs[0], "step_1A", 1.0, "t3c_step_1A_lem_inside.csv", "0.61-0.73 / 0.81-1.04 ms"),
        (axs[1], "step_2A", 2.0, "t3_step_2A_kp31.csv", "0.45-0.47 / 0.62-0.65 ms"),
    ):
        s = sim(name)
        if s is None:
            continue
        t0 = s.var_si("t_sp_on")
        i = s.i_coil
        t90, t98 = cross(s.t, i, 0.9 * sp, t0, True), cross(s.t, i, 0.98 * sp, t0, True)
        test = f"Controller on, step 0 -> {sp:g} A"
        add(test, "time to 90 % / 98 %", f"{t90*1e3:.2f} / {t98*1e3:.2f} ms", ref, "bench gains kp 31.2, ki 942")
        add(test, "overshoot", f"{max(0.0, (float(i.max())/sp - 1))*100:.1f} %", "1-2.4 %", "")
        add(test, "OUT1 at the hold", f"{s['u_kcounts'][-1]*1e3:.0f} counts", {1.0: "~3857 (fit)", 2.0: "3942-3952"}[sp], "")
        b = bench_csv(bfile)
        if b is not None:
            k = np.where(b["setpoint_amps"] > 0.05)[0]
            tb0 = b["t_s"][k[0]] if len(k) else 0.0
            ax.plot((b["t_s"] - tb0) * 1e3, smooth(b["i_amps"], 20), color=C_BENCH, label="bench (20 us average)")
        ax.plot((s.t - t0) * 1e3, i, color=C_SIM, label="Ansys model")
        ax.set_xlim(-0.2, 2.5); ax.set_xlabel("time after the setpoint step [ms]"); ax.set_ylabel("coil current [A]")
        ax.set_title(f"Controller on: 0 -> {sp:g} A")
        place_legend(ax)
    finish(fig, "cmp_steps.png", "Setpoint steps with the controller on, bench gains (kp 31.2 counts/A, ki 942 counts/(A ms)).")

    s = sim("pulse_snap")
    if s is not None:
        t0 = s.var_si("t_sp_on") + s.var_si("t_sp_len") + 2.0 / 200e3 + LATENCY
        i = s.i_coil
        add("Setpoint 2 A -> 0 (zero snap)", "time to 2 % (0.04 A)", f"{cross(s.t, i, 0.04, t0, False)*1e6:.0f} us", "130-140 us", "rev 5 firmware")
    s = sim("trip")
    if s is not None:
        i = s.i_coil
        tripped = s["u_kcounts"][-1] < 0.1
        add("Step to 2.9 A, trip at 2.8 A", "trip fires / peak current", f"{'yes' if tripped else 'NO'} / {float(i.max()):.2f} A", "yes / ~2.85 A", "")


def faults():
    s = sim("hb_pull")
    if s is not None:
        t_pull = s.var_si("t_hb_off")
        # last rising heartbeat edge before the pull (period 1048 us, first edge at 1 us)
        last_edge = 1e-6 + np.floor((t_pull - 1e-6) / 1048e-6) * 1048e-6
        k = np.where((s["dis"] > 1.65) & (s.t > t_pull))[0]
        if len(k):
            t_cut = float(s.t[k[0]])
            add("Heartbeat wire pulled at a 2 A hold", "bridge cut after the last heartbeat edge", f"{(t_cut-last_edge)*1e3:.1f} ms", "7.6 ms", "monostable 220 kOhm / 100 nF")
            add("Heartbeat wire pulled at a 2 A hold", "OUT1 afterwards", f"winds up to {s['u_kcounts'][-1]*1e3:.0f} counts", "4100 (the clamp)",
                "the firmware cannot see the safety board")
            fig, axs = plt.subplots(2, 1, figsize=(7.2, 6.0), sharex=True, layout="constrained")
            tm = s.t * 1e3
            axs[0].plot(tm, s.i_coil, color=C_SIM, label="coil current")
            axs[0].set_ylabel("coil current [A]")
            axs[1].plot(tm, s["u_kcounts"] * 1e3, color=C_SIM, label="OUT1 command [counts]")
            axs[1].plot(tm, s["dis"] * 1000, color=C_BENCH, label="driver DISABLE line (x1000)")
            axs[1].plot(tm, s["hb_ok"] * 500, color=C_AUX, lw=0.8, label="heartbeat OK (x500)")
            axs[1].set_ylabel("counts"); axs[1].set_xlabel("time [ms]")
            axs[0].set_title("Model: heartbeat wire pulled at 1.2 ms during a 2 A hold")
            for ax in axs:
                place_legend(ax)
            finish(fig, "cmp_hb_pull.png", "Model only. The safety board opens the bridge 7.6 ms after the last heartbeat edge; the controller, seeing no current, turns OUT1 up to its clamp. Pressing reset now would re-close the bridge at full drive (the known operating rule).")
    s = sim("in1_lost")
    if s is not None:
        t0 = s.var_si("t_in1_off")
        i = s.i_coil
        m = s.t > t0
        add("IN1 signal lost at a 2 A hold", "peak coil current", f"{float(i[m].max()):.1f} A", "4.7-5 A (11-12 surge)", "supports the lost-IN1 explanation")
        add("IN1 signal lost at a 2 A hold", "rail sag", f"{s.at('rail', t0-1e-5):.1f} -> {float(s['rail'][m].min()):.1f} V", "4.3 -> 1.9 V", "bench supply in current limit")
        fig, axs = plt.subplots(2, 1, figsize=(7.2, 6.0), sharex=True, layout="constrained")
        tm = (s.t - t0) * 1e3
        axs[0].plot(tm, i, color=C_SIM, label="true coil current")
        axs[0].plot(tm, s["i_meas"], color=C_BENCH, label="what the firmware sees (IN1)")
        axs[0].axhline(float(s.var["trip_amps"][0]), color=C_AUX, ls="--", lw=0.8, label="trip level")
        axs[0].set_ylabel("current [A]")
        axs[1].plot(tm, s["rail"], color=C_SIM, label="rail bar")
        axs[1].plot(tm, s["u_kcounts"], color=C_BENCH, label="OUT1 command [thousand counts]")
        axs[1].set_ylabel("V / thousand counts"); axs[1].set_xlabel("time after IN1 is lost [ms]")
        axs[0].set_xlim(-0.3, 1.4)
        axs[0].set_title("Model: IN1 cable loses contact during a 2 A hold")
        for ax in axs:
            place_legend(ax)
        finish(fig, "cmp_in1_lost.png", "Model only. With IN1 lost the controller sees the current fall and turns the bank up; the true current surges until the bench supply runs out. The trip cannot fire because it uses the same lost signal.")


def mot():
    s = sim("mot_turnoff_100A")
    if s is not None:
        t0 = s.var_si("t_run_off") + LATENCY
        i = s.i_coil
        i0 = float(np.interp(t0 - 5e-6, s.t, i))
        m = s.t > t0
        add("MOT PREDICTION: OUT1 -> 0, bridge closed", "start current / time to zero", f"{i0:.0f} A / {cross(s.t, i, 0.0, t0, False)*1e3:.2f} ms", "(not built) repo model ~1.3 ms from 100 A", "bank law extrapolated")
        add("MOT PREDICTION: OUT1 -> 0, bridge closed", "drain bar peak", f"{float(s['ret'][m].max()):.1f} V", "-", "5KP12CA clamps")
    s2 = sim("mot_bridge_open_100A")
    if s2 is not None:
        t0 = s2.var_si("t_en_off")
        i = s2.i_coil
        m = s2.t > t0
        add("MOT PREDICTION: bridge opened at current", "start current / time to zero", f"{float(np.interp(t0-5e-6, s2.t, i)):.0f} A / {cross(s2.t, i, 0.0, t0, False)*1e6:.0f} us", "(not built) offline estimate 182 us", "")
        add("MOT PREDICTION: bridge opened at current", "rail bar peak", f"{float(s2['rail'][m].max()):.0f} V", "(not built) offline estimate 111-113 V",
            "do not trust: the same model over-predicts this spike on the bench")
    if s is not None and s2 is not None:
        fig, axs = plt.subplots(1, 2, figsize=(10.5, 4.2), layout="constrained")
        axs[0].plot((s.t - s.var_si("t_run_off")) * 1e3, s.i_coil, color=C_SIM, label="OUT1 -> 0, bridge closed")
        axs[0].plot((s2.t - s2.var_si("t_en_off")) * 1e3, s2.i_coil, color=C_BENCH, label="bridge opened")
        axs[0].set_xlim(-0.1, 2.0); axs[0].set_xlabel("time after the event [ms]"); axs[0].set_ylabel("coil current [A]")
        axs[0].set_title("MOT pair, prediction")
        axs[1].plot((s.t - s.var_si("t_run_off")) * 1e3, s["ret"], color=C_SIM, label="drain bar, OUT1 -> 0")
        axs[1].plot((s2.t - s2.var_si("t_en_off")) * 1e3, s2["rail"], color=C_BENCH, label="rail bar, bridge opened")
        axs[1].set_xlim(-0.02, 0.3); axs[1].set_xlabel("time after the event [ms]"); axs[1].set_ylabel("voltage [V]")
        for ax in axs:
            place_legend(ax)
        finish(fig, "cmp_mot_prediction.png", "PREDICTION for the two MOT coils on the SE-600-5 (not built). The pass-bank law is extrapolated from 2.5 A to 100 A and the supply's output capacitance is assumed.")


# ---------------------------------------------------------------------------
# second set: the remaining bench tests
# ---------------------------------------------------------------------------
def test0_parts():
    lamp = lambda v: "on" if v > 1.65 else "off"
    s = sim("safety_board")
    if s is not None:
        # (time, what was done, bench: green, amber, red)
        steps = [(2.6e-6, "1 power on, heartbeat running", "on", "off", "on"), (8e-6, "2 press reset", "on", "off", "off"),
                 (500e-6, "3 enable the bridge", "on", "on", "off"), (9.0e-3, "4 pull the heartbeat wire", "off", "off", "on"),
                 (11.0e-3, "5 re-plug the heartbeat", "on", "off", "on"), (11.9e-3, "6 press reset", "on", "on", "off")]
        for t, what, g, a, r in steps:
            mg, ma, mr = lamp(s.at("hb_ok", t)), lamp(3.3 - s.at("dis", t)), lamp(s.at("latch_trip", t))
            add("Test 0d: safety-board lights (green / amber / red)", what, f"{mg} / {ma} / {mr}", f"{g} / {a} / {r}",
                "" if (mg, ma, mr) == (g, a, r) else "DISAGREES")
    s = sim("reset_no_heartbeat")
    if s is not None:
        add("Reset pressed while the heartbeat is missing", "bridge after the heartbeat returns",
            "enabled again, no new cut-off" if s.at("dis", 11.9e-3) < 1.65 else "stays cut off", "enabled again (known weakness)", "press reset only with the green light on")
    f, r = sim("hand_forward"), sim("hand_reverse")
    if f is not None and r is not None:
        add("Test 0f: current through the coil, both directions", "coil current forward / reversed", f"{f.i_coil[-1]:+.2f} / {r.i_coil[-1]:+.2f} A", "+1.12 / -1.12 A",
            "bench: driven by hand, before the pass bank was in use; the model uses OUT1 = 3850 counts")
        add("Test 0f: current through the coil, both directions", "IN1 voltage forward / reversed", f"{f['in1'][-1]*1e3:+.1f} / {r['in1'][-1]*1e3:+.1f} mV", "5 mV per amp, sign follows the current", "")


def gate_stage():
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 4.2), layout="constrained")
    for ax, name, bfile, title in ((axs[0], "gate_sweep_term", "t1_gate_sweep_terminated.json", "with the 50 ohm terminator"),
                                   (axs[1], "gate_sweep_unterm", "t1_gate_sweep.json", "without terminator")):
        s = sim(name)
        if s is None:
            continue
        u = s["u_kcounts"] * 1e3
        ok = (s.t > 150e-6) & (u > 80)
        ax.plot(u[ok], s["gate_bus"][ok], color=C_SIM, label="Ansys model, gate voltage")
        ax.plot(u[ok], s["out1"][ok], color="#8FA9CC", label="Ansys model, OUT1 at the amplifier")
        b = bench_json(bfile)
        if b is not None:
            c = np.array([r["counts"] for r in b])
            ax.plot(c, [r["ch"][3][0] for r in b], "o", color=C_BENCH, label="bench, gate")
            ax.plot(c, [r["ch"][0][0] for r in b], "s", color="#E6A29F", label="bench, OUT1")
            for cc in (2000, 4000):
                k = int(np.argmin(np.abs(u - cc)))
                row = min(b, key=lambda r: abs(r["counts"] - cc))
                add("Test 1: gate stage, " + title, f"OUT1 / gate at {cc} counts", f"{s['out1'][k]:.3f} / {s['gate_bus'][k]:.2f} V",
                    f"{row['ch'][0][0]:.3f} / {row['ch'][3][0]:.2f} V", "op-amp on 5.18 V: the gate cannot go above that")
        ax.set_xlabel("OUT1 command [counts]"); ax.set_ylabel("volts"); ax.set_title("Gate stage, " + title)
        ax.set_xlim(0, 4700)
        place_legend(ax)
    finish(fig, "cmp_gate_stage.png", "Test 1: OUT1 swept upward, voltage at the amplifier input and at the gates. Without the terminator OUT1 doubles and the gate runs into the amplifier's 5.18 V supply.")
    s = sim("turnoff")
    if s is not None:
        t0 = s.var_si("t_run_off") + LATENCY
        g0 = s.at("pg1", t0 - 2e-6)
        t90, t10 = cross(s.t, s["pg1"], 0.9 * g0, t0, False), cross(s.t, s["pg1"], 0.1 * g0, t0, False)
        add("Test 3f: gate when OUT1 goes to zero", "gate 4 V -> 0", f"{t10*1e6:.1f} us (90-10 %: {(t10-t90)*1e6:.1f} us)", "~4 us", "set by the amplifier's 125 mA output limit into six gates")


def bank_scan():
    b = bench_json("t3q5_bank.json")
    fig, ax = plt.subplots(figsize=(6.6, 4.3), layout="constrained")
    for name, key, col_s, col_b in (("bank_scan_cold", "cold", C_SIM, C_BENCH), ("bank_scan_warm", "warm", "#8FA9CC", "#E6A29F")):
        s = sim(name)
        if s is None:
            continue
        u = s["u_kcounts"] * 1e3
        ok = s.t > 250e-6
        ax.plot(u[ok], s.i_coil[ok], color=col_s, label=f"Ansys model, {key}")
        if b is not None:
            rows = np.array(b[key]["up"], float)
            ax.plot(rows[:, 0], rows[:, 1], ".", ms=4, color=col_b, label=f"bench, {key}")
            for cc in (3700, 3800, 3900):
                k = int(np.argmin(np.abs(u - cc))); j = int(np.argmin(np.abs(rows[:, 0] - cc)))
                add(f"Q5: current against OUT1, {key} transistors", f"current at {cc} counts", f"{s.i_coil[k]:.2f} A", f"{rows[j, 1]:.2f} A",
                    "the model's law was fitted to the 11-11/12 scans; Q5 (11-13) is an independent check")
    ax.set_xlabel("OUT1 command [counts]"); ax.set_ylabel("coil current [A]"); ax.set_title("Q5: pass bank, cold and warm")
    ax.set_xlim(3400, 4000)
    place_legend(ax)
    finish(fig, "cmp_bank_scan.png", "Q5: slow OUT1 sweep with the controller off. Warm = the same transistors after 20 minutes at ~1.5-1.8 A (in the model: threshold 24 mV lower).")


def _step_metrics(s, sp, t0):
    i = s.i_coil
    return cross(s.t, i, 0.9 * sp, t0, True), cross(s.t, i, 0.98 * sp, t0, True)


def _osc(s, a, b):
    """(peak-to-peak mA, frequency kHz) of the coil current between times a and b."""
    tt = np.linspace(a, b, 8000)
    y = np.interp(tt, s.t, s.i_coil)
    y = y - y.mean()
    f = np.fft.rfftfreq(len(tt), tt[1] - tt[0])
    Y = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    return float(np.ptp(y)) * 1e3, float(f[Y[2:].argmax() + 2]) / 1e3


def steps_gains():
    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.0), layout="constrained")
    for ax, name, bfile, kp, ref in ((axs[0], "step_1A_kp15", "t3_step_1A_kp15.csv", "15.6 / 471", "1.41 / 1.85 ms"),
                                     (axs[1], "step_1A_kp31", "t3_step_1A_kp31.csv", "31.2 / 942", "0.73 / 1.04 ms"),
                                     (axs[2], "step_1A_kp62", "t3_step_1A_kp62.csv", "62.4 / 1884", "0.38 / 0.53 ms")):
        s = sim(name)
        if s is None:
            continue
        t0 = s.var_si("t_sp_on")
        t90, t98 = _step_metrics(s, 1.0, t0)
        pp, fk = _osc(s, 2.4e-3, 3.0e-3)
        test = f"3d: 0 -> 1 A, gains {kp}, no damper"
        add(test, "time to 90 % / 98 %", f"{t90*1e3:.2f} / {t98*1e3:.2f} ms", ref, "")
        bench_hold = {"15.6 / 471": "clean (40 mA noise)", "31.2 / 942": "clean (40 mA noise)", "62.4 / 1884": "oscillates at 21 kHz (86 mA)"}[kp]
        add(test, "at the hold", f"{'steady' if pp < 10 else f'ripple +-{pp/2:.0f} mA at {fk:.0f} kHz'}", bench_hold,
            "" if pp < 10 else "model: through the pass transistors' drain-gate capacitance (1 nF assumed); see the ringing section")
        b = bench_csv(bfile)
        if b is not None:
            k = np.where(b["setpoint_amps"] > 0.05)[0]
            tb0 = b["t_s"][k[0]] if len(k) else 0.0
            ax.plot((b["t_s"] - tb0) * 1e3, smooth(b["i_amps"], 20), color=C_BENCH, label="bench (20 us average)")
        ax.plot((s.t - t0) * 1e3, s.i_coil, color=C_SIM, label="Ansys model")
        ax.set_xlim(-0.2, 2.8); ax.set_xlabel("time after the step [ms]"); ax.set_ylabel("coil current [A]"); ax.set_title(f"kp / ki = {kp}")
        place_legend(ax)
    finish(fig, "cmp_steps_gains.png", "3d: the same 0 -> 1 A step at half, chosen and double controller gains (before the damper was fitted).")


def steps_range():
    runs = [("step_0p5A", "t3b_step_0_0.5A.csv", 0.0, 0.5, "0 -> 0.5 A", "1.08 / 1.36"), ("step_1A_kp31", "t3b_step_0_1A.csv", 0.0, 1.0, "0 -> 1 A", "0.72 / 0.95"),
            ("step_1p5A", "t3b_step_0_1.5A.csv", 0.0, 1.5, "0 -> 1.5 A", "0.55 / 0.70"), ("step_2p2A", "t3b_step_0_2.2A.csv", 0.0, 2.2, "0 -> 2.2 A", "0.44 / 0.63"),
            ("step_2p5A", "t3_step_2p5A_kp31_table.csv", 0.0, 2.5, "0 -> 2.5 A", "- / 0.59"),
            ("down_2p2_to_1A", "t3b_step_2.2_1A.csv", 2.2, 1.0, "2.2 -> 1 A", "0.24 / 0.34"), ("down_1_to_0_nosnap", "t3b_step_1_0A.csv", 1.0, 0.0, "1 -> 0 A, no zero snap", "0.68 / 1.22"),
            ("down_2_to_0_nosnap", "t3b_step_2_0A.csv", 2.0, 0.0, "2 -> 0 A, no zero snap", "0.47 / 1.49")]
    fig, axs = plt.subplots(2, 4, figsize=(15, 7), layout="constrained")
    for ax, (name, bfile, a, bfin, label, ref) in zip(axs.ravel(), runs):
        s = sim(name)
        if s is None:
            continue
        i = s.i_coil
        if a == 0.0:
            t0 = s.var_si("t_sp_on")
        elif name == "down_2p2_to_1A":
            t0 = s.var_si("t_sp2_on")
        else:
            t0 = s.var_si("t_sp_on") + a / 200e3 + s.var_si("t_sp_len")
        rising = bfin > a
        l90, l98 = a + 0.9 * (bfin - a), a + 0.98 * (bfin - a)
        t90, t98 = cross(s.t, i, l90, t0, rising), cross(s.t, i, l98, t0, rising)
        fmt = lambda v: "> 1.5" if not np.isfinite(v) else f"{v*1e3:.2f}"
        add("Steps over the range (firmware before the zero snap, no damper)", f"{label}: time to 90 % / 98 % of the change",
            f"{fmt(t90)} / {fmt(t98)} ms", f"{ref} ms", "the slow last part (the reason for the zero snap): see the plot" if not np.isfinite(t98) else "")
        b = bench_csv(bfile)
        if b is not None:
            sp = b["setpoint_amps"]
            k = np.where(np.abs(sp - sp[0]) > 0.05)[0]
            tb0 = b["t_s"][k[0]] if len(k) else 0.0
            ax.plot((b["t_s"] - tb0) * 1e3, smooth(b["i_amps"], 20), color=C_BENCH, label="bench (20 us avg)")
        ax.plot((s.t - t0) * 1e3, i, color=C_SIM, label="Ansys model")
        ax.set_xlim(-0.2, 1.5 if a else 2.5); ax.set_title(label); ax.set_xlabel("time after the step [ms]"); ax.set_ylabel("coil current [A]")
        place_legend(ax)
    finish(fig, "cmp_steps_range.png", "Setpoint steps up and down over the bench range, controller on, no damper. The model's hold shows a small fast ripple that the bench does not (see the ringing section).")


def reversal():
    T = 1.4e-3
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.3), layout="constrained")
    for ax, name, title, ref, bfile, pick in (
        (axs[0], "reversal", "current settings (66 us wait, 100 us settle)", ("141 us", "740 us", "0.95 ms"), "t3p_fast_reversal_block.json", "prod"),
        (axs[1], "reversal_fast", "trimmed (8 us wait, no settle)", ("130 us", "585 us", "0.81 ms"), "t3p_fast_reversal_block.json", "fast")):
        s = sim(name)
        if s is None:
            continue
        i = s.i_coil
        tz, t90 = cross(s.t, i, 0.0, T, False), cross(s.t, i, -1.8, T, False)
        late = np.where((s.t > T) & (np.abs(i + 2) > 0.04))[0]
        t2 = float(s.t[late[-1]] - T) if len(late) else float("nan")
        test = "3g: reversal +2 -> -2 A, " + title
        add(test, "current reaches zero", f"{tz*1e6:.0f} us", ref[0], "sign of the setpoint and flip sent together")
        add(test, "90 % of -2 A", f"{t90*1e6:.0f} us", ref[1], "")
        add(test, "within 2 % of -2 A", f"{t2*1e3:.2f} ms", ref[2], "")
        ax.plot((s.t - T) * 1e3, i, color=C_SIM, label="Ansys model")
        b = bench_json(bfile)
        if b is not None:
            recs = [r for r in b if r.get("s_from") == 1 and r.get("config") == pick]
            if recs:
                r = recs[0]
                tb = np.array(r["t_us"]) * 1e-3 - r["t_sp_ms"]
                ax.plot(tb, smooth(r["i"], 10), color=C_BENCH, label="bench (10 us average)")
        ax.axhline(0, color="k", lw=0.5)
        ax.set_xlim(-0.3, 1.6); ax.set_xlabel("time after the reversal is requested [ms]"); ax.set_ylabel("coil current [A]"); ax.set_title(title)
        place_legend(ax)
    finish(fig, "cmp_reversal.png", "3g: full reversal +2 -> -2 A. The model does not contain the firmware's flip sequence; the bridge, polarity and enable lines are played with the firmware's timing (zero window +-0.49 A, wait, 1 us off before and after the polarity change, settling pause).")
    s = sim("reversal")
    if s is not None:
        tg = s.var_si("t_en_off")
        vq1 = s["g1"] - s["A"]; vq2 = s["g2"] - s["B"]
        t_off = cross(s.t, vq1, 3.5, tg, False); t_on = cross(s.t, vq2, 3.5, tg, True)
        add("3g: dead time at the bridge transistors", "outgoing pair below threshold / incoming pair above threshold",
            f"{t_off*1e6:.2f} us / {t_on*1e6:.2f} us after enable drops", "not measured yet (gate voltages need a floating probe)",
            f"PREDICTION: {((t_on - t_off)*1e6):.1f} us with no pair conducting; 1.0 us each side was measured at the Red Pitaya pins")
        add("3g: dead time at the bridge transistors", "current at the moment of the flip", f"{float(np.interp(tg, s.t, s.i_coil)):+.2f} A", "+0.13 / -0.29 A", "inside the +-0.49 A window")


def small_signal():
    ref = {"ss_0p5A": ("0.5 A", "5.1 (Q1), 3.6 (Q5)", "16.3 kHz, 0.48"), "ss_1A": ("1 A", "7.7 (Q1), 8.6 (Q5)", "9.7 kHz, 0.36"), "ss_2A": ("2 A", "13.4 (Q1), 13.2 (Q5)", "7.7 kHz, 0.22")}
    fig, ax = plt.subplots(figsize=(6.6, 4.3), layout="constrained")
    for name, col in (("ss_0p5A", "#8FA9CC"), ("ss_1A", C_SIM), ("ss_2A", "#0B2545")):
        s = sim(name)
        if s is None:
            continue
        t0 = s.var_si("t_ol_step") + LATENCY
        i = s.i_coil
        i0, i1 = float(np.interp(t0 - 1e-5, s.t, i)), float(i[-1])
        m = s.t > t0
        y = (i[m] - i0) / (i1 - i0); tt = s.t[m] - t0
        over = float(y.max()) - 1
        tpk = float(tt[y.argmax()])
        zeta = -np.log(over) / np.sqrt(np.pi ** 2 + np.log(over) ** 2) if over > 1e-3 else 1.0
        fn = 1 / (2 * tpk * np.sqrt(1 - zeta ** 2)) if zeta < 1 else float("nan")
        cur, g_ref, r_ref = ref[name]
        add("Q1: +10 counts of OUT1 at a hold (controller off, damper fitted)", f"at {cur}: current per count", f"{(i1-i0)/10*1e3:.1f} mA", g_ref + " mA", "")
        add("Q1: +10 counts of OUT1 at a hold (controller off, damper fitted)", f"at {cur}: resonance, damping", f"{fn/1e3:.1f} kHz, {zeta:.2f}", r_ref,
            "model: the same at every current; bench: falls with current")
        ax.plot(tt * 1e6, y, color=col, label=f"Ansys model, {cur}")
    ax.set_xlim(0, 400); ax.set_xlabel("time after the +10 count step [us]"); ax.set_ylabel("change of current, 1 = final")
    ax.set_title("Q1: response to a small OUT1 step")
    place_legend(ax)
    finish(fig, "cmp_small_signal.png", "Q1, model only: response of the coil current to a 10-count step of OUT1 with the controller off and the damper fitted.")


def ringing():
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.3), layout="constrained")
    for name, counts, fb, ab, col in (("ring_3800", 3800, 20, 80, "#8FA9CC"), ("ring_3870", 3870, 15, 110, C_SIM), ("ring_3935", 3935, 11.5, 150, "#0B2545")):
        s = sim(name)
        if s is None:
            continue
        pp, fk = _osc(s, 0.15e-3, 0.6e-3)
        pp_late, _ = _osc(s, 2.0e-3, 3.0e-3)
        i_mean = float(s.i_coil[s.t > 2e-3].mean())
        add("Ringing at a fixed OUT1, no damper, drain-gate capacitance set to 17 nF", f"{counts} counts ({i_mean:.2f} A): frequency", f"{fk:.1f} kHz", f"{fb} kHz", "frequency and its fall with current reproduced")
        add("Ringing at a fixed OUT1, no damper, drain-gate capacitance set to 17 nF", f"{counts} counts: does it keep going?", f"no: dies away in ~1 ms (+-{pp_late/2:.0f} mA after 2 ms)", f"yes: +-{ab} mA all the time",
            "DISAGREES: the model has the resonance but nothing keeps it going")
        m = (s.t > 0.05e-3) & (s.t < 1.0e-3)
        axs[0].plot(s.t[m] * 1e3, s.i_coil[m], color=col, label=f"{counts} counts")
    s = sim("ring_3935_damper")
    if s is not None:
        m = (s.t > 0.05e-3) & (s.t < 1.0e-3)
        axs[0].plot(s.t[m] * 1e3, s.i_coil[m], color=C_BENCH, lw=1, label="3935 counts, damper fitted")
        pp, fk = _osc(s, 0.3e-3, 0.8e-3)
        add("Ringing at a fixed OUT1, no damper, drain-gate capacitance set to 17 nF", "3935 counts with the damper", f"+-{pp/2:.0f} mA after 0.3 ms", "gone (tone 124 -> 4.9 mA)", "")
    axs[0].set_xlabel("time after switch-on [ms]"); axs[0].set_ylabel("coil current [A]"); axs[0].set_title("Model: switch-on at a fixed OUT1, 17 nF")
    place_legend(axs[0])
    cur_b, f_b = np.array([0.71, 1.23, 1.93]), np.array([20, 15, 11.5])
    cur_m, f_m = [], []
    for name in ("ring_3800", "ring_3870", "ring_3935"):
        s = sim(name)
        if s is not None:
            cur_m.append(float(s.i_coil[s.t > 2e-3].mean())); f_m.append(_osc(s, 0.15e-3, 0.6e-3)[1])
    axs[1].plot(cur_b, f_b, "o", color=C_BENCH, label="bench (sustained ringing)")
    if cur_m:
        axs[1].plot(cur_m, f_m, "s-", color=C_SIM, mfc="none", label="Ansys model, 17 nF (decaying)")
    s5 = sim("ring_3935_cgd5n")
    if s5 is not None:
        axs[1].plot([float(s5.i_coil[s5.t > 2e-3].mean())], [_osc(s5, 0.15e-3, 0.6e-3)[1]], "^", color=C_AUX, label="Ansys model, 5 nF")
    axs[1].set_xlabel("coil current [A]"); axs[1].set_ylabel("ringing frequency [kHz]"); axs[1].set_title("Ringing frequency against current")
    axs[1].set_ylim(0, 26)
    place_legend(axs[1])
    finish(fig, "cmp_ringing.png", "The 10-20 kHz ringing. With the pass transistors' drain-gate capacitance raised from the 1 nF datasheet value (given at 25 V) to 17 nF, the model rings at the bench's frequencies and follows the same fall with current, but the ringing dies away instead of continuing.")


# every bench test in docs/benchtop_test_log.md, and whether the circuit model can do it
COVERAGE = [
    ("0a", "Transistor thresholds", "no", "single-transistor bench measurement; its results are inputs of the model"),
    ("0b, 0d", "Safety-board light sequence", "yes", "all six steps"),
    ("0c", "First load of the new firmware", "no", "firmware and software"),
    ("0e", "Controller test on a cable", "no", "firmware only, no power stage (tested in sim/)"),
    ("0f", "Bridge and current sensor driven by hand", "yes", "both current directions"),
    ("1, 1b", "Gate stage: OUT1 to gate voltage, with and without terminator", "yes", ""),
    ("1", "Gate voltage after the amplifier loses power", "no", "the amplifier's supply is an ideal source in the model"),
    ("2", "Current sensor zero and offsets", "no", "offsets, drift and noise are not modelled"),
    ("3 day 1, Q5", "Current against OUT1, cold and warm", "yes", "warm = lower threshold; warming up itself is not modelled"),
    ("3c", "Current scale against the ammeter", "no", "a calibration of the real sensor chain"),
    ("3d", "Controller settings: steps at three gains, 2 A, 2.5 A", "yes", ""),
    ("3e, Q6", "Over-current trip", "yes", "the trip level and delay; not the switch-off spikes on IN1 (pickup)"),
    ("3f", "Switch-off with the bridge closed", "yes", "with and without damper"),
    ("3g", "Flips and reversals", "partly", "one ordering (sign and flip together) and the trimmed fast one; the firmware's own sequence logic is not in the model"),
    ("3g", "Dead time", "partly", "the Red Pitaya timing is an input; the model predicts the bridge gate voltages, not yet measured"),
    ("3h", "Safety-board cut-off while current flows", "yes", "bridge opening and the 7.6 ms time-out"),
    ("3i", "Steadiness of a held current, 10 minutes", "no", "needs noise, drift and heating"),
    ("extra", "Steps over the range, up and down", "yes", "eight steps"),
    ("damper", "Ringing with and without the damper", "partly", "frequencies reproduced with a larger drain-gate capacitance; not sustained"),
    ("LEM inside", "Switch-off, bridge opening, pulse", "yes", ""),
    ("LEM inside", "The 5 A surge", "yes", "as a lost IN1 signal (one possible cause)"),
    ("rev 5", "Zero snap on a zero setpoint", "yes", ""),
    ("day 3 D1", "Stop and trip around a flip", "partly", "stop = the zero snap run; not the firmware's handling of orderings"),
    ("day 3 D2-D6", "Arm signal, ramp limits, 50 shots, two programs, PC connection lost", "no", "firmware and software behaviour"),
    ("noise", "Zero-current noise reference", "no", "no noise in the model"),
    ("fastest", "Fastest reversal", "yes", ""),
    ("Q1", "Safety margin of the controller (small steps)", "partly", "current per count yes; the measured resonance moving with current no"),
    ("Q2, Q3, Q4", "45 min hold, 50 min zero reading, 1000 shots", "no", "drift, heating and statistics"),
    ("Q7, Q8", "Earlier computer models (bank law, MOT damper, surge)", "yes", "the bank law is built in; MOT predictions in the 'mot' design"),
]


CSS = """
body { font-family: Calibri, Arial, sans-serif; font-size: 11pt; max-width: 900px; margin: 24px auto; }
h1, h2, h3 { color: #1F497D; }
table { border-collapse: collapse; }
th { background: #DDEBF7; text-align: left; }
th, td { border: 1px solid #9DB6D3; padding: 3px 8px; vertical-align: top; }
td.bad { background: #FBE5D6; }
img { max-width: 100%; }
p.cap { color: #444; margin-top: 2px; }
"""


def report():
    out = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>Coil servo: Ansys model vs bench</title>",
           f"<style>{CSS}</style></head><body>",
           "<h1>Coil servo power stage: Ansys circuit model compared with the bench</h1>",
           "<p>Model: <b>ansys/coil_servo.aedt</b>, design <b>bench</b> (3 A bench supply, 45 &micro;H test coil, "
           "3.2 &Omega; + 10 &micro;F damper). Bench numbers: docs/benchtop_test_log.md and docs/lab_notes_2026-11.md "
           "(tests of 11-11 to 11-13). Regenerate with run_sims.py then compare.py.</p>",
           "<h2>Model against measurement</h2>",
           "<table><tr><th>Test</th><th>Quantity</th><th>Ansys model</th><th>Bench</th><th>Note</th></tr>"]
    last = None
    for test, q, mval, bval, note in rows:
        bad = " class='bad'" if ("NOT reproduced" in note or "DISAGREES" in note or "do not trust" in note) else ""
        out.append(f"<tr><td>{html.escape(test) if test != last else ''}</td><td>{html.escape(q)}</td>"
                   f"<td{bad}>{html.escape(mval)}</td><td>{html.escape(bval)}</td><td>{html.escape(note)}</td></tr>")
        last = test
    out.append("</table>")
    out.append("<h2>Which bench tests are simulated</h2>"
               "<table><tr><th>Log section</th><th>Bench test</th><th>In the model?</th><th>Remark</th></tr>")
    for sec, what, yn, why in COVERAGE:
        out.append(f"<tr><td>{html.escape(sec)}</td><td>{html.escape(what)}</td><td>{yn}</td><td>{html.escape(why)}</td></tr>")
    out.append("</table>")
    out.append("<h2>What does not match</h2><ul>"
               "<li><b>The 10&ndash;20 kHz ringing.</b> With datasheet capacitances the model does not ring at a fixed OUT1. When the "
               "pass transistors' drain-to-gate capacitance is raised from 1 nF (the datasheet value, given at 25 V) to 17 nF, "
               "the model shows a resonance at 17 / 15 / 12 kHz for 0.77 / 1.26 / 1.87 A &mdash; the bench rang at 20 / 15 / 11.5 kHz, "
               "with the same fall with current &mdash; and the damper removes it. The path: the coil turns a change of drain "
               "current into a drain voltage, the drain-gate capacitance passes that to the gate behind its 24.9 &Omega; resistor, "
               "and the gate changes the drain current. <b>But in the model the ringing dies away within about 1 ms; on the "
               "bench it continued.</b> So this is a candidate explanation, not a confirmed one. A direct check: probe one "
               "transistor's own gate pin (after the 24.9 &Omega; resistor), not the gate bus, during ringing.</li>"
               "<li><b>Closed loop without the damper:</b> the model's hold carries a 40&ndash;50 kHz ripple of "
               "&plusmn;30&ndash;50 mA at every gain setting from 31.2 up (same path, with the 1 nF value); the bench showed a "
               "21 kHz oscillation only at double gain. With the damper the model is steady, as the bench.</li>"
               "<li><b>Rail bar when the bridge opens at 2 A:</b> the model gives a jump of about +25 V lasting less than "
               "1 &micro;s; the scope showed +2.5 to +3.2 V lasting about 10 &micro;s. In the model the only capacitance on "
               "the rail bar is that of the transistors (tens of nF). The bench behaves as if a few &micro;F sat on the rail "
               "bar, or as if the lead inductance were smaller than the 3.6 &micro;H used. Do not trust the model's (or the "
               "earlier offline estimate's) rail voltage for a bridge opening at 100 A.</li>"
               "<li><b>Small-step response (Q1):</b> the bench resonance moved from 16 to 8 kHz between 0.5 and 2 A and became "
               "less damped; the model's is the same at every current (about 14 kHz, damping 0.48).</li>"
               "<li><b>Smaller points:</b> swing below zero after a switch-off (model &minus;0.09 A, bench about &minus;0.2 A); "
               "drain bar with the bridge open (model 0 V, bench &minus;0.5 V); the model's 0.5 A step is slower than the bench's.</li>"
               "<li><b>Test coil inductance:</b> the bench switch-off trace gives 37 &micro;H (0.52 V across the coil, "
               "13.3 mA/&micro;s); the calculated value in the repo is 45 &micro;H. The model uses 37 &micro;H.</li></ul>")
    out.append("<h2>Plots</h2>")
    for fname, cap in figs:
        out.append(f"<p><img src='{fname}' alt='{html.escape(cap)}'></p><p class='cap'>{html.escape(cap)}</p>")
    out.append("</body></html>")
    with open(os.path.join(RESULTS, "comparison.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    w = [max(len(r[k]) for r in rows) for k in range(4)]
    for r in rows:
        print("  ".join(r[k].ljust(w[k]) for k in range(4)) + "  " + r[4])


if __name__ == "__main__":
    test0_parts(); gate_stage(); bank_scan(); static_points(); steps_gains(); steps_range(); steps(); turnoff(); bridge_open()
    reversal(); small_signal(); ringing(); faults(); mot()
    report()
    print("\nwrote", os.path.join(RESULTS, "comparison.html"), "and", len(figs), "plots")
