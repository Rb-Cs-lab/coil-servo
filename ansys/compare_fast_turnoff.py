"""Compare the fast turn-off version of the circuit with the circuit as built.

    ansys\\.venv\\Scripts\\python.exe ansys\\compare_fast_turnoff.py

Reads the waveforms run_sims.py saved (ansys/results/*.csv) for the designs
with the 5KP30A + DSS2X101-015A (bench_ft, mot_ft) and for the same tests
without them, and writes ansys/results/fast_turnoff.html with two plots.
Neither part is fitted yet: every number here is a prediction of the model.
"""
from __future__ import annotations

import html
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sim"))
sys.path.insert(0, HERE)
from plotfit import place_legend, settle_legends  # noqa: E402
from simdata import Sim  # noqa: E402

C_OLD, C_NEW, C_AUX = "#7F7F7F", "#1F497D", "#C0504D"
T_BENCH, T_MOT = 200e-6, 600e-6        # when the switch-off happens in the bench and MOT runs


def sim(name):
    return Sim(os.path.join(RESULTS, name + ".csv"))


def numbers(s, t0):
    """Current before, time to first reach 10 % and to stay within 2 %, largest reverse current, largest voltages."""
    i = s.i_coil
    i0 = float(np.interp(t0 - 2e-6, s.t, i))
    after = s.t > t0

    def within(frac):
        k = np.where(after & (np.abs(i) > frac * abs(i0)))[0]
        if len(k) and k[-1] >= len(i) - 2:
            return float("nan")          # still outside at the end of the run
        return (s.t[k[-1]] - t0) * 1e6 if len(k) else 0.0
    k10 = np.where(after & (np.abs(i) <= 0.10 * abs(i0)))[0]
    return {"i0": i0, "t10": (s.t[k10[0]] - t0) * 1e6 if len(k10) else float("nan"), "t2": within(0.02), "imin": float(i[after].min()),
            "bridge": float((s["rail"] - s["ret"])[after].max()), "coil": float(np.abs(s["A"] - s["B"])[after].max()),
            "rail_end": float(s["rail"][-1]), "sup": float(s["sup_p"][after].max())}


def us(v):
    return "not within the run" if not np.isfinite(v) else (f"{v / 1e3:.2f} ms" if v >= 1000 else f"{v:.0f} µs")


def panel(axs, runs, t0, window_us, title):
    for name, label, color in runs:
        s = sim(name)
        t = (s.t - t0) * 1e6
        m = (t > -window_us * 0.15) & (t < window_us)
        axs[0].plot(t[m], s.i_coil[m], color=color, label=label, lw=1.6)
        axs[1].plot(t[m], (s["rail"] - s["ret"])[m], color=color, label=label, lw=1.6)
    axs[0].set_ylabel("coil current (A)")
    axs[1].set_ylabel("rail bar minus return bar (V)")
    axs[1].set_xlabel("time after the switch-off (µs)")
    axs[0].set_title(title, fontsize=10)
    for ax in axs:
        ax.grid(alpha=0.3)
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi + 0.3 * (hi - lo))      # room for the legend above the curves
        place_legend(ax)


def main():
    rows, figs = [], []

    # ---- MOT pair at ~100 A ------------------------------------------------
    mot = [("mot_turnoff_100A", "as planned for Test 4: OUT1 to zero, bridge closed", C_OLD, "MOT, bridge kept closed (today's method)"),
           ("mot_ft_bridge_open_100A", "new parts, bridge opened, proposed damper", C_NEW, "MOT, new parts, bridge opened, proposed damper (1.6 Ω + 22 µF)"),
           ("mot_ft_bridge_open_100A_nodamper", "new parts, bridge opened, no damper", C_AUX, "MOT, new parts, bridge opened, no damper")]
    fig, axs = plt.subplots(2, 2, figsize=(12, 7), sharex="col")
    panel(axs[:, 0], [m[:3] for m in mot], T_MOT, 1800, "MOT pair, switch-off from the hold current: whole fall")
    panel(axs[:, 1], [m[:3] for m in mot[1:]], T_MOT, 200, "the same, first 200 µs (bridge opened only)")
    settle_legends(fig)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "cmp_fast_turnoff_mot.png"), dpi=110)
    plt.close(fig)
    figs.append(("cmp_fast_turnoff_mot.png", "MOT pair on the SE-600-5 (model only). Grey: the switch-off used today. Blue and red: the bridge opened with the two new parts fitted."))
    for name, _, _, label in mot + [("mot_ft_turnoff_100A", "", "", "MOT, new parts, bridge kept closed"),
                                    ("mot_bridge_open_100A", "", "", "MOT, <b>without</b> the new parts, bridge opened (a safety-board cut-off today)")]:
        rows.append((label, numbers(sim(name), T_MOT)))

    # ---- bench test coil at 2 A -------------------------------------------
    bench = [("bridge_open", "as built, damper fitted", C_OLD, "Bench, as built, bridge opened, damper fitted"),
             ("ft_bridge_open", "new parts, damper fitted", C_NEW, "Bench, new parts, bridge opened, damper fitted"),
             ("ft_bridge_open_nodamper", "new parts, no damper", C_AUX, "Bench, new parts, bridge opened, no damper")]
    fig, axs = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    panel(axs, [b[:3] for b in bench], T_BENCH, 150, "Bench test coil, bridge opened at 2 A")
    settle_legends(fig)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "cmp_fast_turnoff_bench.png"), dpi=110)
    plt.close(fig)
    figs.append(("cmp_fast_turnoff_bench.png", "Bench test coil at 2 A (model only). With the damper fitted the new parts change nothing; without it the coil rings."))
    for name, _, _, label in bench:
        rows.append((label, numbers(sim(name), T_BENCH)))

    # ---- what the blocking diode costs --------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    cost = {}
    for name, label, color in (("mot_scan", "as planned for Test 4", C_OLD), ("mot_ft_scan", "with the blocking diode", C_NEW)):
        s = sim(name)
        u = s["u_kcounts"] * 1e3
        m = s.t > 250e-6
        ax.plot(u[m], s.i_coil[m], color=color, label=label, lw=1.6)
        cost[name] = float(s.i_coil.max())
    ax.axhline(100, color=C_AUX, lw=0.8, ls="--")
    ax.set_xlabel("OUT1 command (counts)")
    ax.set_ylabel("coil current (A)")
    ax.set_title("MOT pair: current against OUT1, slow sweep (the bank law is extrapolated from 2.5 A)", fontsize=10)
    ax.grid(alpha=0.3)
    place_legend(ax)
    settle_legends(fig)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "cmp_fast_turnoff_headroom.png"), dpi=110)
    plt.close(fig)
    figs.append(("cmp_fast_turnoff_headroom.png", "What the blocking diode costs: the largest current the 5 V supply can push through the MOT pair, without and with the diode. Dashed: 100 A."))
    hold = sim("mot_ft_turnoff_100A")
    pre = (hold.t > T_MOT - 50e-6) & (hold.t < T_MOT - 2e-6)
    drop = float((hold["rail_in"] - hold["rail"])[pre].mean())
    i_hold = float(hold.i_coil[pre].mean())

    def at_100(name):
        """Voltage left across the pass bank, and the OUT1 command, when the slow sweep passes 100 A."""
        s = sim(name)
        k = int(np.where(s.i_coil >= 100)[0][0])
        return float(s["ret"][k]), float(s["u_kcounts"][k] * 1e3)

    def rise_90(name):
        """Time for the current to reach 90 A after switch-on at a fixed OUT1 command, microseconds."""
        s = sim(name)
        k = np.where(s.i_coil >= 90)[0]
        return (s.t[k[0]] - 10e-6) * 1e6 if len(k) else float("nan")
    (v_old, u_old), (v_new, u_new) = at_100("mot_scan"), at_100("mot_ft_scan")
    r_old, r_new = rise_90("mot_turnoff_100A"), rise_90("mot_ft_turnoff_100A")
    b2 = sim("ft_step_2A")
    drop2 = float((b2["rail_in"] - b2["rail"])[-50:].mean())

    css = ("body{font-family:Calibri,Arial,sans-serif;font-size:11pt;max-width:1000px}h1,h2{color:#1F497D}"
           "table{border-collapse:collapse}th{background:#DDEBF7;text-align:left}th,td{border:1px solid #999;padding:3px 8px;vertical-align:top}"
           "img{max-width:100%}.note{color:#555}")
    out = ["<html><head><meta charset='utf-8'><title>Fast turn-off parts: model predictions</title>", f"<style>{css}</style></head><body>",
           "<h1>Fast turn-off parts: what the circuit model predicts</h1>",
           "<p>Project file <b>ansys/coil_servo_fast_turnoff.aedt</b> (designs bench_ft and mot_ft): the circuit as built plus a "
           "<b>5KP30A</b> surge protector between the rail bar and the return bar, and a <b>DSS2X101-015A</b> blocking diode in the supply's + lead. "
           "<b>Neither part is fitted yet, so nothing here has been measured.</b> The numbers for the two parts were typed from memory of their "
           "datasheets and must be checked. The MOT results also rest on a pass-bank law measured only up to 2.5 A.</p>",
           "<h2>Switch-off</h2>",
           "<table><tr><th>Case</th><th>Current before</th><th>Time to first reach 10 %</th><th>Time to stay within 2 %</th><th>Largest reverse current</th>"
           "<th>Largest voltage across the bridge</th><th>Largest voltage across the coil(s)</th><th>Rail bar at the end of the run</th></tr>"]
    for label, n in rows:
        out.append(f"<tr><td>{label}</td><td>{n['i0']:.1f} A</td><td>{us(n['t10'])}</td><td>{us(n['t2'])}</td><td>{n['imin']:.2f} A</td>"
                   f"<td>{n['bridge']:.0f} V</td><td>{n['coil']:.0f} V</td><td>{n['rail_end']:.1f} V</td></tr>")
    out += ["</table>",
            "<h2>What the blocking diode costs</h2>",
            f"<p>Voltage across the diode: {drop2:.2f} V at 2 A (bench), {drop:.2f} V at {i_hold:.0f} A (MOT) — about {drop * i_hold:.0f} W of heat at that current. "
            f"100 A is still reached, at the same OUT1 command ({u_old:.0f} and {u_new:.0f} counts): the voltage left across the pass bank at 100 A "
            f"falls from {v_old:.2f} V to {v_new:.2f} V. Above that both curves end at the supply's own current limit (taken as 110 A; the bump is its "
            f"output capacitor emptying). The other cost is a slower rise: switched on at a fixed OUT1 command, the current reaches 90 A after "
            f"{us(r_old)} without the diode and {us(r_new)} with it. (The switch-off runs in the table start after 600 µs, before the current has "
            "finished rising, which is why they start from different currents.)</p>",
            "<h2>What to take from this</h2><ul>"
            "<li><b>MOT pair:</b> opening the bridge with the two parts fitted brings the current down about 15 times faster than today's method, "
            "and the bridge sees about 50 V instead of the 100 V and more the model gives for a bridge opening today.</li>"
            "<li><b>The damper works against it.</b> With the proposed damper the damper capacitor charges to about 50 V and pushes about 12 A back "
            "through the coils in the wrong direction; without a damper the fall is clean but a small fast ringing (about 1 A) remains. "
            "The damper values need to be chosen again for this version. The model has no losses at those frequencies, so real ringing dies sooner.</li>"
            "<li><b>The bench coil with its damper cannot show the effect:</b> at 2 A the damper takes the whole current at about 6 V and the 5KP30A never conducts.</li>"
            "<li><b>The rail bar stays charged</b> (30 V falling slowly) after the bridge has been opened, because the diode stops it emptying into the supply. "
            "The charge is tiny unless a capacitor is added on the rail bar.</li>"
            "<li>Keeping the bridge closed (today's switch-off, and every normal ramp) is unchanged by the new parts.</li></ul>"]
    for f, cap in figs:
        out.append(f"<p><img src='{f}'><br><span class='note'>{html.escape(cap)}</span></p>")
    out.append("</body></html>")
    path = os.path.join(RESULTS, "fast_turnoff.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("wrote", path)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for label, n in rows:
        print(f"{label[:70]:70s} I0 {n['i0']:6.1f}  10% {us(n['t10']):>16s}  2% {us(n['t2']):>16s}  Imin {n['imin']:6.2f}  bridge {n['bridge']:5.0f} V  coil {n['coil']:5.0f} V  rail end {n['rail_end']:.1f}")
    print(f"diode drop {drop2:.2f} V @2A, {drop:.2f} V @ {i_hold:.0f} A; bank V at 100 A {v_old:.2f} -> {v_new:.2f}; counts {u_old:.0f} {u_new:.0f}; rise to 90 A {r_old:.0f} -> {r_new:.0f} us")


if __name__ == "__main__":
    main()
