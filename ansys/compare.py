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
        add("Fixed OUT1, damper removed", "10-20 kHz ringing at 1.9 A", f"none ({pp:.1f} mA p-p)", "+-116 to +-165 mA at 12 kHz",
            "NOT reproduced: the cause is not in this circuit model")


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
    out.append("<h2>What does not match</h2><ul>"
               "<li><b>The 10&ndash;20 kHz ringing without the damper does not appear in the model.</b> With the parts modelled "
               "as they are here (a fixed current-versus-gate-voltage law for the pass transistors, fixed capacitances, an ideal "
               "bench supply with a current limit) the circuit is stable at a fixed OUT1. So the ringing does not come from "
               "the coil, damper, leads and transistors acting as simple parts. Still to try in the model: transistor "
               "capacitances that change with voltage, transistor self-heating, and the bench supply's own regulation.</li>"
               "<li><b>Rail bar when the bridge opens at 2 A:</b> the model gives a jump of about +25 V lasting less than "
               "1 &micro;s; the scope showed +2.5 to +3.2 V lasting about 10 &micro;s. In the model the only capacitance on "
               "the rail bar is that of the transistors (tens of nF), so the current in the supply leads has nowhere to go. "
               "The bench behaves as if a few &micro;F sat on the rail bar, or as if the lead inductance were smaller than "
               "the 3.6 &micro;H used. Until this is understood, do not trust the model's (or the earlier offline "
               "estimate's) rail voltage for a bridge opening at 100 A.</li>"
               "<li><b>Swing below zero after a switch-off:</b> model &minus;0.09 A, bench about &minus;0.2 A.</li>"
               "<li><b>Drain bar with the bridge open:</b> bench about &minus;0.5 V, model about 0 V.</li>"
               "<li><b>No overshoot in the model's steps</b> (bench 1&ndash;2.4 %): the model's controller is smooth in time, "
               "the real one works in 1 &micro;s steps with 14-bit numbers.</li>"
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
    static_points(); turnoff(); bridge_open(); steps(); faults(); mot()
    report()
    print("\nwrote", os.path.join(RESULTS, "comparison.html"), "and", len(figs), "plots")
