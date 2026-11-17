"""The reversed current after a bridge-opened switch-off, and what the damper should be.

    ansys\\.venv\\Scripts\\python.exe ansys\\damper_study.py

Reads the Ansys runs made for this question (run_sims.py: ft_fastoff_*, ft_ring_kick_*,
mot_ft_off_*, mot_ft_ring*), the bench measurement of 2026-11-17
(logs/fastoff/f5_rev6_cases.json, if it is there) and the small stand-alone model in
damper_model.py, and writes ansys/results/damper_study.html with its plots.

Bench numbers are measured; everything about the MOT pair is a model prediction.
"""
from __future__ import annotations

import html
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, HERE)
import damper_model as dm  # noqa: E402
from plotfit import place_legend, settle_legends  # noqa: E402
from run_sims import SWEEP_C_UF, SWEEP_K, _tag, sweep_r  # noqa: E402
from simdata import Sim  # noqa: E402

BENCH_JSON = os.path.join(REPO, "logs", "fastoff", "f5_rev6_cases.json")
T_BENCH, T_MOT = 1.5e-3, 2e-3            # when the bridge opens in the bench and MOT runs
L_MOT, R_MOT = 16e-6, 7.4e-3             # MOT pair as in the Ansys design (2 x 8 uH, 2 x 3.7 mOhm)
C_MEAS, C_SIM, C_FIT, C_AUX, C_GREEN = "#C0504D", "#1F497D", "#7F7F7F", "#E08A00", "#4F8F4F"
C_FINE = np.logspace(np.log10(0.3), np.log10(300), 34)      # capacitor values (uF) for the stand-alone model's curves
PICKS = ((100, 2), (100, 4))      # (capacitor uF, resistor as a multiple of critical): the two plain dampers put forward
K_COLORS = {0.5: "#8FA9CC", 1: "#1F497D", 2: "#E08A00", 4: "#C0504D"}


def sim(name):
    return Sim(os.path.join(RESULTS, name + ".csv"))


def have(name):
    return os.path.exists(os.path.join(RESULTS, name + ".csv"))


def us(v):
    return "—" if not np.isfinite(v) else (f"{v * 1e3:.2f} ms" if v >= 1e-3 else f"{v * 1e6:.0f} µs")


# ---------------------------------------------------------------------------
# numbers from a switch-off trace (t = 0 at the switch, i in amps)
# ---------------------------------------------------------------------------
def trace_numbers(t, i, i0, floor=None):
    """floor: currents smaller than this count as zero (default 1 % of the starting current)."""
    floor = 0.01 * abs(i0) if floor is None else floor
    a = t >= 0
    t, i = t[a], i[a]
    k90 = np.where(i <= 0.9 * i0)[0]
    k10 = np.where(i <= 0.10 * i0)[0]            # first time down to 10 % / 2 % (the swing below zero comes after)
    k2 = np.where(i <= 0.02 * i0)[0]
    out = np.where(np.abs(i) > 0.02 * i0)[0]
    kz = np.where(i <= 0)[0]
    n = {"i0": i0, "t10": t[k10[0]] if len(k10) else np.nan,
         "t90_2": t[k2[0]] - t[k90[0]] if len(k2) and len(k90) else np.nan,
         "t2": t[out[-1]] if len(out) and out[-1] < len(t) - 2 else (0.0 if not len(out) else np.nan),
         "rev_peak": min(float(i.min()), 0.0), "t_zero": t[kz[0]] if len(kz) else np.nan}
    o5 = np.where(np.abs(i) > 0.005 * i0)[0]
    n["t05"] = t[o5[-1]] if len(o5) and o5[-1] < len(t) - 2 else (0.0 if not len(o5) else np.nan)
    kmin = int(np.argmin(i))
    n["rev_at"] = t[kmin] - n["t_zero"] if n["rev_peak"] < -floor else np.nan      # after the zero crossing
    neg = np.where(i < -floor)[0]
    n["rev_until"] = t[neg[-1]] - n["t_zero"] if len(neg) and neg[-1] < len(t) - 2 else (0.0 if not len(neg) else np.nan)
    return n


def ansys_off(name, t0):
    """Switch-off numbers of an Ansys run, plus the energies in the 5KP30A and the damper resistor."""
    s = sim(name)
    i = s.i_coil
    pre = (s.t > t0 - 50e-6) & (s.t < t0 - 2e-6)
    n = trace_numbers(s.t - t0, i, float(i[pre].mean()), floor=0.02 if i[pre].mean() < 10 else None)
    a = s.t >= t0
    t = s.t[a]
    v_bar = (s["rail"] - s["ret"])[a]
    i_t = np.asarray(dm.i_tvs(v_bar), float)
    n["tvs_peak"] = float(i_t.max())
    n["tvs_energy"] = float(np.trapezoid(i_t * v_bar, t))
    r_d = s.var_si("R_damp1") + (s.var_si("R_damp2") if "R_damp2" in s.var else 0.0)
    v_r = (s["A"] - s["dmp2"])[a]
    p = v_r ** 2 / r_d
    n["res_energy"] = float(np.trapezoid(p, t)) if r_d < 1e5 else 0.0
    n["res_peak_power"] = float(p.max()) if r_d < 1e5 else 0.0
    n["cap_peak"] = float(np.abs(s["dmp2"] - s["B"])[a].max()) if r_d < 1e5 else 0.0
    n["bar_peak"] = float(v_bar.max())
    n["coil_peak"] = float(np.abs(s["A"] - s["B"])[a].max())
    return n


def ring_numbers(name, t_kick=1.5e-3):
    """How fast the ringing after a small OUT1 step dies: each half-swing as a fraction of the one before."""
    s = sim(name)
    i = s.i_coil
    before = float(i[(s.t > t_kick - 0.2e-3) & (s.t < t_kick)].mean())
    m = s.t > t_kick
    t, y = s.t[m] - t_kick, i[m]
    final = float(y[t > 1.3e-3].mean())
    step = final - before
    y = (y - final) / step
    zc = np.where(np.diff(np.sign(y)) != 0)[0]
    peaks = np.array([np.abs(y[zc[k]:zc[k + 1] + 1]).max() for k in range(len(zc) - 1)])
    big = peaks[: 8][peaks[: 8] > 0.01]          # half-swings larger than 1 % of the step
    if len(big) >= 2:
        ratio = float(np.exp(np.mean(np.diff(np.log(big)))))
        freq = (len(big)) / 2 / (t[zc[len(big)]] - t[zc[0]])
    else:
        ratio, freq = 0.0, np.nan
    zeta = 1.0 if ratio <= 0 else float(-np.log(ratio) / np.hypot(np.pi, np.log(ratio)))
    return {"current": final, "ratio": ratio, "zeta": zeta, "freq": freq, "overshoot": float(peaks[0]) if len(peaks) else 0.0,
            "t": t, "y": y}


# ---------------------------------------------------------------------------
# bench measurement
# ---------------------------------------------------------------------------
def measured():
    """The bridge-opened switch-offs measured on 11-17: [(name, t, i, i0)], t = 0 at the switch."""
    if not os.path.exists(BENCH_JSON):
        return []
    out = []
    with open(BENCH_JSON) as f:
        cases = json.load(f)
    for c in cases:
        if c["name"][:2] not in ("R3", "R4"):
            continue
        t = np.array(c["t_us"]) * 1e-6
        i = np.array(c["i"]) * c["sign"]
        k0 = np.where((t > -1e-4) & (i < 1.0))[0][0]          # first sample well below the 2 A hold
        i0 = float(np.median(i[(t > t[k0] - 200e-6) & (t < t[k0] - 10e-6)]))
        m = (t > t[k0] - 40e-6) & (t < t[k0] + 300e-6)
        out.append((c["name"], t[m] - t[k0] + 1e-6, i[m], i0))
    return out


def fit_rlc(t, i, i0):
    """Fit a plain series coil - resistor - capacitor swing to a measured trace. Returns (decay rate, natural frequency, time shift)."""
    def curve(tt, alpha, w0):
        wd = np.sqrt(complex(w0 ** 2 - alpha ** 2 + 1.0))
        y = i0 * np.exp(-alpha * tt) * (np.cos(wd * tt) - alpha / wd * np.sin(wd * tt))
        return np.where(tt < 0, i0, y.real)
    use = t > 6e-6                               # skip the pickup burst at the switch
    best = (np.inf, None)
    for shift in np.arange(-6e-6, 6.1e-6, 1e-6):
        for alpha in np.arange(15e3, 50e3, 500):
            for w0 in np.arange(20e3, 60e3, 500):
                r = np.clip(curve(t - shift, alpha, w0) - i, -0.25, 0.25)[use]      # single spikes do not steer the fit
                c = float(np.sum(r ** 2))
                if c < best[0]:
                    best = (c, (alpha, w0, shift))
    alpha, w0, shift = best[1]
    return alpha, w0, shift, lambda tt: curve(tt - shift, alpha, w0)


# ---------------------------------------------------------------------------
def fig_save(fig, name):
    settle_legends(fig)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, name), dpi=110)
    plt.close(fig)


def headroom(ax, frac=0.3):
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi + frac * (hi - lo))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    figs, out = {}, []

    # ===================== 1. bench =========================================
    meas = measured()
    bench_rows = []
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.6))
    ax = axs[0]
    fits = []
    for k, (name, t, i, i0) in enumerate(meas):
        ax.plot(t * 1e6, i, color=C_MEAS, lw=0.8, alpha=0.55 if k else 0.9, label="measured 11-17 (two switch-offs)" if k == 0 else None)
        alpha, w0, shift, curve = fit_rlc(t, i, i0)
        fits.append((alpha, w0))
        tf = np.linspace(0, 400e-6, 8001)
        n = trace_numbers(tf, curve(tf + shift), i0, floor=0.02)
        bench_rows.append((f"Measured, {name[:2]} (smooth curve through the trace)", n))
    for name, label, color, ls in (("ft_fastoff_2A", "model, values as in the project (37 µH, 3.2 Ω, 10 µF)", C_SIM, "-"),
                                   ("ft_fastoff_2A_L45", "model, coil 45 µH", C_SIM, ":"),
                                   ("ft_fastoff_2A_fit", "model, 57 µH and 17 µF", C_GREEN, "-")):
        if not have(name):
            continue
        s = sim(name)
        t = (s.t - T_BENCH) * 1e6
        m = (t > -40) & (t < 300)
        ax.plot(t[m], s.i_coil[m], color=color, ls=ls, lw=1.6, label=label)
        bench_rows.append((label[0].upper() + label[1:], ansys_off(name, T_BENCH)))
    ax.set_xlim(-40, 300); ax.set_ylim(-0.6, 2.4)
    ax.set_xlabel("time after the bridge opens (µs)"); ax.set_ylabel("coil current (A)")
    ax.set_title("Bench coil, bridge opened at 2 A, damper fitted", fontsize=10)
    ax.grid(alpha=0.3); ax.legend(loc="upper right", fontsize=8)
    ax = axs[1]
    nd = None
    if have("ft_fastoff_2A_nodamper"):
        s = sim("ft_fastoff_2A_nodamper")
        t = (s.t - T_BENCH) * 1e6
        m = (t > -5) & (t < 40)
        ax.plot(t[m], s.i_coil[m], color=C_SIM, lw=1.4, label="coil current (A)")
        ax2 = ax.twinx()
        ax2.plot(t[m], (s["rail"] - s["ret"])[m], color=C_AUX, lw=1.2, label="rail bar minus return bar (V)")
        ax2.set_ylabel("rail bar minus return bar (V)"); ax2.set_ylim(-20, 75)
        ax.set_ylim(-1.6, 3.4)
        nd = ansys_off("ft_fastoff_2A_nodamper", T_BENCH)
        y = s.i_coil[(t > 10) & (t < 40)]; tt = t[(t > 10) & (t < 40)]
        zc = np.where(np.diff(np.sign(y - y.mean())) > 0)[0]
        nd["ring_f"] = (len(zc) - 1) / ((tt[zc[-1]] - tt[zc[0]]) * 1e-6)
        nd["ring_amp"] = float(np.ptp(y) / 2)
        h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)
    ax.set_xlabel("time after the bridge opens (µs)"); ax.set_ylabel("coil current (A)")
    ax.set_title("Prediction for test F4: the same with the damper removed", fontsize=10)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "damper_bench.png"), dpi=110); plt.close(fig)
    figs["bench"] = ("damper_bench.png", "Left: the measured switch-off (red) against the circuit model. The depth of the negative swing is reproduced; "
                     "with the values in the project the model's swing is about a third faster than the bench, and it matches when the coil is taken as "
                     "57 µH and the damper capacitor as 17 µF. Right: the model with the damper removed (not yet measured).")

    # ===================== 2. MOT pair, the main cases =======================
    mot_rows = []
    for name, label in (("mot_ft_off_damper", "(a) proposed damper, 1.6 Ω + 22 µF across the pair"), ("mot_ft_off_nodamper", "(b) no damper")):
        if have(name):
            mot_rows.append((label, ansys_off(name, T_MOT), "Ansys"))

    # ===================== 3. sweep ==========================================
    grid = {}
    for c in SWEEP_C_UF:
        for k in SWEEP_K:
            tag = _tag(c, k)
            if not have(f"mot_ft_off_{tag}"):
                continue
            g = {"R": sweep_r(c, k), "C": c, "k": k, "off": ansys_off(f"mot_ft_off_{tag}", T_MOT)}
            for a in (5, 20):
                if have(f"mot_ft_ring{a}_{tag}"):
                    g[f"ring{a}"] = ring_numbers(f"mot_ft_ring{a}_{tag}")
            grid[(c, k)] = g
    ref = {n: ring_numbers(n) for n in ("ft_ring_kick_nodamper", "ft_ring_kick_damper", "mot_ft_ring_nodamper_5A", "mot_ft_ring_nodamper_20A",
                                        "mot_ft_ring_nodamper_100A", "mot_ft_ring_damper_5A", "mot_ft_ring_damper_20A", "mot_ft_ring_damper_100A") if have(n)}

    # the stand-alone model over a fine grid, for the curves
    c_fine = C_FINE
    fine = {}
    for k in SWEEP_K:
        r_fine = k * 2 * np.sqrt(L_MOT / (c_fine * 1e-6))
        fine[k] = dm.numbers(dm.switch_off(L_MOT, R_MOT, 100.8, r_fine, c_fine * 1e-6), 100.8, r_fine)

    fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
    for k in SWEEP_K:
        pts = [grid[(c, k)] for c in SWEEP_C_UF if (c, k) in grid]
        lab = f"R = {k:g} × 2√(L/C)"
        axs[0].plot(c_fine, -fine[k]["rev_peak"], color=K_COLORS[k], lw=1.2, alpha=0.7)
        axs[0].plot([p["C"] for p in pts], [-p["off"]["rev_peak"] for p in pts], "o", color=K_COLORS[k], label=lab)
        axs[1].plot([p["C"] for p in pts], [p["off"]["rev_until"] * 1e6 for p in pts], "o-", color=K_COLORS[k], label=lab)
        for a, mk, ls in ((20, "o", "-"), (5, "s", "--")):
            q = [p for p in pts if f"ring{a}" in p]
            axs[2].plot([p["C"] for p in q], [p[f"ring{a}"]["ratio"] for p in q], mk + ls, color=K_COLORS[k], ms=5,
                        label=f"{lab}, at {a} A")
    axs[0].set_ylabel("largest reversed current (A)"); axs[0].set_title("Reversed current after opening the bridge at 100 A", fontsize=10)
    axs[1].set_ylabel("reversed for this long (µs)"); axs[1].set_title("How long the current stays reversed by more than 1 A", fontsize=10)
    axs[2].set_ylabel("each half-swing as a fraction of the one before"); axs[2].set_title("Hold ringing after a small OUT1 step (smaller = better damped)", fontsize=10)
    if "mot_ft_ring_nodamper_20A" in ref:
        axs[2].axhline(ref["mot_ft_ring_nodamper_20A"]["ratio"], color="#555", lw=0.9, ls=":")
        axs[2].text(40, ref["mot_ft_ring_nodamper_20A"]["ratio"] + 0.012, "no damper, 20 A", fontsize=8, color="#555")
    if "ft_ring_kick_damper" in ref:
        axs[2].axhline(ref["ft_ring_kick_damper"]["ratio"], color=C_GREEN, lw=0.9, ls=":")
        axs[2].text(0.5, ref["ft_ring_kick_damper"]["ratio"] + 0.012, "bench coil with its damper", fontsize=8, color=C_GREEN)
    for ax in axs:
        ax.set_xscale("log"); ax.set_xlabel("damper capacitor (µF)"); ax.grid(alpha=0.3, which="both")
    axs[2].set_ylim(-0.02, 1.3)
    for ax in axs[:2]:
        headroom(ax, 0.35); place_legend(ax)
    axs[2].legend(fontsize=7, ncol=2, loc="upper center")
    fig_save(fig, "damper_sweep.png")
    figs["sweep"] = ("damper_sweep.png", "MOT pair (model): damper capacitor and resistor varied. The resistor is given as a multiple of the "
                     "critical-damping value 2√(L/C) for 16 µH. Dots: the Ansys circuit; thin lines in the left plot: the small stand-alone model. "
                     "Right: how fast the model's 10–20 kHz hold ringing dies with each damper (circles at 20 A, squares at 5 A).")

    # ===================== 4. other damper circuits (stand-alone model) ======
    alt = []
    r0, c0 = np.array([1.6]), np.array([22e-6])
    cases = [("proposed damper across the pair, 1.6 Ω + 22 µF", dict(R_d=r0, C_d=c0), C_FIT, "-"),
             ("no damper", dict(R_d=np.array([np.inf]), C_d=c0), "#000000", ":")]
    best = [grid[key] for key in PICKS if key in grid]
    for g, color in zip(best, (C_SIM, C_MEAS)):
        cases.append((f"{g['R']:.1f} Ω + {g['C']:g} µF across the pair", dict(R_d=np.array([g["R"]]), C_d=np.array([g["C"] * 1e-6])), color, "-"))
    cases += [("proposed damper + a 6 V two-way limiter across its capacitor", dict(R_d=r0, C_d=c0, v_lim=6.0), C_AUX, "-"),
              ("proposed damper moved to rail bar – return bar", dict(R_d=r0, C_d=c0, where="rail"), C_GREEN, "--")]
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.6))
    for label, kw, color, ls in cases:
        r = dm.switch_off(L_MOT, R_MOT, 100.8, t_end=1.5e-3, **kw)
        n = {k: float(v[0]) for k, v in dm.numbers(r, 100.8, kw["R_d"]).items()}
        n["t05"] = trace_numbers(r["t"], r["i"][:, 0], 100.8)["t05"]
        alt.append((label, n))
        for ax in axs:
            ax.plot(r["t"] * 1e6, r["i"][:, 0], color=color, ls=ls, lw=1.6, label=label)
    if have("mot_ft_off_damper"):
        s = sim("mot_ft_off_damper")
        t = (s.t - T_MOT) * 1e6
        m = (t > 0) & (t < 400)
        for ax in axs:
            ax.plot(t[m][::3], s.i_coil[m][::3], ".", color=C_FIT, ms=3, label="Ansys circuit, proposed damper")
    axs[0].set_xlim(0, 300); axs[0].set_ylim(-20, 150)
    axs[1].set_xlim(20, 250); axs[1].set_ylim(-15, 12)
    axs[0].set_title("MOT pair, bridge opened at 100 A (stand-alone model)", fontsize=10)
    axs[1].set_title("the same, the reversed part enlarged", fontsize=10)
    for ax in axs:
        ax.set_xlabel("time after the bridge opens (µs)"); ax.set_ylabel("coil current (A)"); ax.grid(alpha=0.3)
    place_legend(axs[0])
    fig_save(fig, "damper_options.png")
    figs["options"] = ("damper_options.png", "MOT pair (model): coil current after the bridge opens at 100 A, for the damper circuits compared in the table.")

    write_report(out, figs, meas, fits, bench_rows, nd, mot_rows, grid, ref, alt, best, fine)


def rail_section(figs):
    """Test F4 (damper removed, measured 11-17): what is left on the rail bar, and closing the bridge onto it."""
    scope = {}
    for key, f in (("fast", "f4_nodamper_5e-6_2A_scope.npz"), ("slow", "f4_nodamper_2e-4_2A_scope.npz")):
        path = os.path.join(REPO, "logs", "fastoff", f)
        if os.path.exists(path):
            scope[key] = np.load(path)
    if not have("ft_f4"):
        return []

    def aligned(name, t0):
        """Time (us) with zero where the rail bar passes 15 V on its way up (as good as the scope's trigger)."""
        q = sim(name)
        k = np.where((q.t > t0) & (q["rail"] > 15))[0]
        return q, (q.t - (q.t[k[0]] if len(k) else t0)) * 1e6

    fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
    runs = (("ft_f4", "model as built (rail bar: about 40 nF of parts)", C_SIM, "-"), ("ft_f4_c0p3", "model with 0.3 µF on the rail bar", C_AUX, "-"))
    for ax, key, lim in ((axs[0], "fast", (-2, 12)), (axs[1], "slow", (-100, 1700))):
        if key in scope:
            z = scope[key]
            ax.plot(z["t3"] * 1e6, z["v3"], color=C_MEAS, lw=0.9, label="measured, rail bar")
        for name, label, color, ls in runs:
            if have(name):
                q, t = aligned(name, T_BENCH)
                m = (t > lim[0]) & (t < lim[1])
                ax.plot(t[m], q["rail"][m], color=color, ls=ls, lw=1.5, label=label)
        ax.set_xlim(*lim); ax.set_ylim(0, 75)
        ax.set_xlabel("time after the bridge opens (µs)"); ax.set_ylabel("rail bar (V)"); ax.grid(alpha=0.3)
        ax.legend(loc="upper right", fontsize=8)
    axs[0].set_title("Test F4, bench, 2 A, no damper: the first microseconds", fontsize=10)
    axs[1].set_title("the same, the next 1.7 ms", fontsize=10)
    ax = axs[2]
    rows = []
    for name, label, color, ls in (("ft_f4_reclose", "bench, as built", C_SIM, "-"), ("ft_f4_reclose_bleed", "bench, 1 kΩ across the blocking diode", C_SIM, ":"),
                                   ("ft_f4_c0p3_reclose", "bench, if 0.3 µF were on the rail bar", C_AUX, "-"),
                                   ("mot_ft_rail_nodamper_reclose", "MOT pair after 100 A, no damper", C_GREEN, "-"),
                                   ("mot_ft_rail_nodamper_reclose_bleed", "MOT, no damper, 1 kΩ across the blocking diode", C_GREEN, ":"),
                                   ("mot_ft_rail_nodamper_c0p3_reclose", "MOT, no damper, if 0.3 µF were on the rail bar", C_AUX, "--"),
                                   ("mot_ft_rail_damper_reclose", "MOT, proposed damper", C_FIT, "-"),
                                   ("mot_ft_rail_damper_reclose_bleed", "MOT, proposed damper, 1 kΩ across the blocking diode", C_FIT, ":")):
        if not have(name):
            continue
        q = sim(name)
        t0 = (T_BENCH if name.startswith("ft_") else T_MOT)
        t = (q.t - t0 - 300e-6) * 1e6
        m = (t > -10) & (t < 150)
        i = q.i_coil
        before = float(np.interp(-5, t, q["rail"]))
        k = int(np.argmax(np.abs(i[m])))
        over = np.where(np.abs(i[m]) > 0.1)[0]
        rows.append((label, before, float(i[m][k]), float(t[m][over[-1]]) if len(over) else 0.0))
        if "MOT" not in label or "no damper" in label:
            ax.plot(t[m], i[m], color=color, ls=ls, lw=1.5, label=label)
    ax.set_xlim(-10, 150); ax.set_xlabel("time after the bridge closes again (µs)"); ax.set_ylabel("coil current (A)")
    ax.set_title("Model: bridge closed again 300 µs later, OUT1 at zero", fontsize=10); ax.grid(alpha=0.3)
    headroom(ax, 0.6); ax.legend(loc="upper right", fontsize=7)
    fig.tight_layout(); fig.savefig(os.path.join(RESULTS, "damper_rail.png"), dpi=110); plt.close(fig)
    figs["rail"] = ("damper_rail.png", "Left and middle: the rail bar in test F4 (red, measured) against the model as built (blue) and with 0.3 µF added on the rail bar (orange). "
                    "Right: model only, the coil current when the bridge is closed again onto the charged rail bar with the pass bank off.")

    f4 = ansys_off("ft_f4", T_BENCH)
    q, t = aligned("ft_f4", T_BENCH)
    held = lambda us_: float(np.interp(us_, t, q["rail"]))
    mot_held = None
    if have("mot_ft_rail_nodamper"):
        qm, tm = aligned("mot_ft_rail_nodamper", T_MOT)
        mot_held = [float(np.interp(u, tm, qm["rail"])) for u in (100, 300, 900)]
    out = ["<h2>4. Test F4 measured (damper removed): what stays on the rail bar</h2>",
           "<p>Measured on 11-17 after the prediction in section 1 was written: rail bar up to 49 V within half a microsecond, back under 30 V after about 3 µs, "
           "then <b>23 V at 35 µs, 22 V at 0.3 ms, 20 V at 1.5 ms</b>. A fast ringing at about 400 kHz dies in about 30 µs.</p>",
           "<table><tr><th></th><th>Measured (F4)</th><th>Model as built</th><th>Model with 0.3 µF on the rail bar</th></tr>"]
    c03 = ansys_off("ft_f4_c0p3", T_BENCH) if have("ft_f4_c0p3") else None
    q3, t3 = aligned("ft_f4_c0p3", T_BENCH) if c03 else (None, None)
    h3 = (lambda us_: f"{float(np.interp(us_, t3, q3['rail'])):.0f} V") if c03 else (lambda us_: "—")
    out += [f"<tr><td>Rail bar peak</td><td>49 V, reached in 0.5 µs</td><td>{f4['bar_peak'] + 0.5:.0f} V, reached in under 1 µs</td><td>{(c03['bar_peak'] if c03 else 0):.0f} V, reached in 5 µs: no spike</td></tr>",
            f"<tr><td>Coil current at zero after</td><td>about 3 µs (rail bar back under the clamp level)</td><td>{us(f4['t_zero'])}</td><td>{us(c03['t_zero']) if c03 else '—'}</td></tr>",
            f"<tr><td>Rail bar at 35 µs / 0.3 ms / 1.5 ms</td><td>23 / 22 / 20 V</td><td>{held(35):.0f} / {held(300):.0f} / {held(1450):.0f} V</td><td>{h3(35)} / {h3(300)} / {h3(1450)}</td></tr>",
            f"<tr><td>Ringing afterwards</td><td>about 400 kHz, gone in about 30 µs</td><td>{f4.get('ring_f', 170e3) / 1e3:.0f} kHz, does not die (no loss in the model)</td><td>—</td></tr>",
            "</table><ul>",
            "<li><b>The clamp itself is as predicted:</b> the 5KP30A takes the 2 A for about 3 µs at 40–50 V (model: 42 V, 2.6 µs).</li>",
            "<li><b>The rail bar does not carry 0.3 µF.</b> It rises 40 V in half a microsecond at 2 A, which only a capacitance of about 25 nF or less allows "
            "(voltage rise per second = current / capacitance). With 0.3 µF the model shows a slow rise to 21 V and no spike. The coil's energy (about 0.1 mJ) "
            "is fully accounted for by the 5KP30A carrying about 1 A at 40 V for 3 µs; none needs to be stored. What stays on the rail bar is the charge on the "
            "parts' own capacitance (5KP30A, bridge transistors, blocking diode; about 40 nF in the model): about 1 µC, 10 µJ.</li>",
            "<li><b>Why 23 V and not the model's 33 V</b> is not answered by the model. A surge protector stops conducting just under its threshold (33–37 V), "
            "so something takes about a third of the charge back off the rail bar during the 30 µs of ringing: most likely the bridge transistors' body diodes, "
            "which conduct backwards for a moment each time the ringing reverses them (the model's diodes do not). A scope probe that is not trimmed would give a "
            "similar slow droop, so that is worth excluding. The slow fall afterwards (3 V in 1.5 ms) is leakage: about 50 µA from 40 nF.</li>",
            "<li><b>Closing the bridge again onto the charged rail bar</b> (model, pass bank off) empties that charge through the coil: a short pulse, see the table. "
            "Small on the MOT pair, but on the 2 A bench it is nearly 1 A, so it should be visible on IN1 when the bridge closes again after a fast switch-off "
            "(a way to check the 40 nF: with 0.3 µF it would be 1.8 A).</li>",
            "<li><b>A bleeder is cheap and worth fitting.</b> 1 kΩ empties the rail bar in about 40 µs (40 nF) and removes most of the pulse. It can go from the "
            "rail bar to the supply's 0 V as proposed, or <b>across the blocking diode</b>, which is what was simulated: the rail bar ends at the supply voltage "
            "either way (the diode holds it there), and across the diode the resistor carries nothing in normal running instead of 5 mA all the time. "
            "During a clamp it sees about 45 V for tens of microseconds; ¼ W is enough.</li>"]
    if mot_held:
        out.append(f"<li><b>MOT pair after a 100 A switch-off (model):</b> the rail bar is held at the clamp level while the current falls (48 V), then sits at "
                   f"{mot_held[0]:.0f} V after 0.1 ms, {mot_held[1]:.0f} V after 0.3 ms and {mot_held[2]:.0f} V after 0.9 ms. Going by the bench, the real one will sit "
                   "lower (20–30 V). The stored charge is the same microcoulomb as on the bench, not more: it does not grow with the current.</li>")
    out += ["</ul>", "<table><tr><th>Bridge closed again 300 µs after a fast switch-off, pass bank off (model)</th><th>Rail bar just before</th><th>Coil current pulse</th>"
            "<th>Above 0.1 A for</th></tr>"]
    for label, before, peak, dur in rows:
        out.append(f"<tr><td>{html.escape(label[0].upper() + label[1:])}</td><td>{before:.0f} V</td><td>{peak:.2f} A</td><td>{dur:.0f} µs</td></tr>")
    out.append("</table>")
    out.append("<p><b>Measured afterwards on the bench (11-17, damper off, logs/fastoff/reclose.json):</b> rail bar at 22 V before the bridge closed again; "
               "a current pulse of 0.7–1.0 A, above 0.1 A for about 16 µs; rail bar back at the supply voltage after 5 µs. The size matches the model as built "
               "(0.93 A, or about 0.74 A scaled to 22 V) and not the 0.3 µF case (1.8 A), so the rail bar carries tens of nanofarads. The measured pulse is "
               "shorter than the model's 69 µs.</p>")
    out.append(f"<p><img src='{figs['rail'][0]}'><br><span class='note'>{html.escape(figs['rail'][1])}</span></p>")
    for label, before, peak, dur in rows:
        print(f"RECLOSE {label:55s} rail {before:5.1f} V  pulse {peak:6.2f} A  >0.1 A for {dur:5.0f} us")
    print("F4 model", {k: round(float(v), 7) for k, v in f4.items() if isinstance(v, (float, np.floating))}, "held", held(35), held(300), held(1450), "MOT held", mot_held)
    return out


def write_report(out, figs, meas, fits, bench_rows, nd, mot_rows, grid, ref, alt, best, fine):
    css = ("body{font-family:Calibri,Arial,sans-serif;font-size:11pt;max-width:1100px}h1,h2{color:#1F497D}"
           "table{border-collapse:collapse}th{background:#DDEBF7;text-align:left}th,td{border:1px solid #999;padding:3px 8px;vertical-align:top}"
           "img{max-width:100%}.note{color:#555}")
    img = lambda key: f"<p><img src='{figs[key][0]}'><br><span class='note'>{html.escape(figs[key][1])}</span></p>"
    out += ["<html><head><meta charset='utf-8'><title>Reversed current after a fast switch-off, and the damper</title>", f"<style>{css}</style></head><body>",
            "<h1>Reversed current after a fast switch-off, and the damper</h1>",
            "<p>When the bridge is opened at current, the coil current falls fast and then swings <b>negative</b> for a short time: the damper's capacitor "
            "was charged during the fall and empties back through the coil. Measured on the bench on 2026-11-17 (−0.3 A after a 2 A switch-off). "
            "This page checks the circuit model against that measurement and then asks what happens on the MOT pair at 100 A and which damper keeps "
            "the swing small. <b>Bench numbers marked measured are measured; everything about the MOT pair is a model prediction.</b></p>"]

    # ---- bench -----------------------------------------------------------
    out += ["<h2>1. Bench coil at 2 A: model against measurement</h2>",
            "<table><tr><th>Trace</th><th>Fall, 90 % to 2 %</th><th>Lowest current</th><th>… this long after crossing zero</th>"
            "<th>Back above −0.02 A (after crossing zero)</th></tr>"]
    for label, n in bench_rows:
        out.append(f"<tr><td>{html.escape(label)}</td><td>{us(n['t90_2'])}</td><td>{n['rev_peak']:.2f} A</td><td>{us(n['rev_at'])}</td><td>{us(n['rev_until'])}</td></tr>")
    out.append("</table>")
    if fits:
        a = float(np.mean([f[0] for f in fits])); w = float(np.mean([f[1] for f in fits]))
        L32 = 3.2 / (2 * a); C32 = 1 / (w ** 2 * L32)
        out.append(f"<p>The measured swing is that of a coil, resistor and capacitor in series with a natural frequency of {w / 2 / np.pi / 1e3:.1f} kHz "
                   f"and a damping ratio of {a / w:.2f}. The nominal parts (45 µH, 3.2 Ω, 10 µF) would give 7.5 kHz. With the resistor taken as its "
                   f"marked 3.2 Ω, the trace implies <b>about {L32 * 1e6:.0f} µH and {C32 * 1e6:.0f} µF</b>: the product of coil inductance and "
                   "capacitance is about twice the nominal one. The 10 µF capacitor (non-polarised) and the coil are worth a check with an LCR meter. "
                   "A coil of 55–60 µH also fits the 11-12 freewheel slope (2 A in 170 µs) if the body diode drops 0.65 V rather than the 0.5 V assumed then.</p>")
    if nd:
        out.append(f"<p><b>Prediction for test F4 (damper removed):</b> the current reaches zero in {us(nd['t_zero'])}; the 5KP30A clamps the rail bar at "
                   f"<b>{nd['bar_peak']:.0f} V</b> above the return bar (peak {nd['tvs_peak']:.1f} A, {nd['tvs_energy'] * 1e6:.0f} µJ). No slow negative swing, "
                   f"but the coil then rings with the stray capacitance of the bridge and varistor: about ±{nd['ring_amp']:.1f} A at {nd['ring_f'] / 1e3:.0f} kHz in the model. "
                   "The model has no loss at that frequency, so there it never dies; on the bench it should die within a few cycles, and the current "
                   "sensor (100 kHz) and the 1 µs sampling will show it smaller than it is. A scope on the rail bar is the better view.</p>")
    out.append(img("bench"))

    # ---- MOT main cases -----------------------------------------------------
    def mot_row(label, n, src):
        return (f"<tr><td>{html.escape(label)}</td><td>{src}</td><td>{us(n['t10'])}</td><td>{us(n['t2'])}</td><td>{us(n['t05'])}</td><td>{n['rev_peak']:.1f} A</td>"
                f"<td>{us(n['rev_until']) if 'rev_until' in n else us(n['rev_time'])}</td><td>{n['tvs_peak']:.0f} A</td><td>{n['tvs_energy'] * 1e3:.0f} mJ</td>"
                f"<td>{n['res_energy'] * 1e3:.0f} mJ</td><td>{n['res_peak_power']:.0f} W</td><td>{n['cap_peak']:.0f} V</td></tr>")
    head = ("<table><tr><th>Damper</th><th>From</th><th>First below 10 %</th><th>Stays within 2 % (2 A)</th><th>Stays within 0.5 % (0.5 A)</th><th>Largest reversed current</th>"
            "<th>Reversed (more than 1 A) for</th><th>5KP30A peak current</th><th>5KP30A energy</th><th>Damper resistor energy</th>"
            "<th>Damper resistor peak power</th><th>Damper capacitor peak voltage</th></tr>")
    out += ["<h2>2. MOT pair, bridge opened at 100 A</h2>",
            "<p>16 µH, 7.4 mΩ including the coil cable, 100.8 A before the switch. “Ansys” = the full circuit "
            "(coil_servo_fast_turnoff.aedt, design mot_ft); “small model” = damper_model.py, which has only the coil, the damper, the body diodes "
            "and the 5KP30A and is used for circuits that are not drawn in the Ansys project.</p>", head]
    for label, n, src in mot_rows:
        out.append(mot_row(label, n, src))
    for g in best:
        out.append(mot_row(f"(c) {g['R']:.1f} Ω + {g['C']:g} µF across the pair", g["off"], "Ansys"))
    for label, n in alt:
        if "limiter" in label or "rail bar" in label:
            out.append(mot_row("(d) " + label, n, "small model"))
    out.append("</table>")
    out.append(img("options"))

    g2, g4 = (best + [None, None])[:2]
    lim = next((n for l, n in alt if "limiter" in l), None)
    rail = next((n for l, n in alt if "rail bar" in l), None)
    a_, b_ = (mot_rows + [(None, None, None)] * 2)[0][1], (mot_rows + [(None, None, None)] * 2)[1][1]
    if a_ and b_ and g2 and g4 and lim and rail:
        out += ["<h2>What to take from this</h2><ul>",
                f"<li><b>The estimate is confirmed.</b> With the proposed damper (1.6 Ω + 22 µF) the capacitor charges to {a_['cap_peak']:.0f} V while the 5KP30A "
                f"holds the coil at about 50 V, then pushes <b>{-a_['rev_peak']:.0f} A</b> back through the coils; the current is reversed for about "
                f"{us(a_['rev_until'])} and within 2 % only after {us(a_['t2'])}. Without a damper the fall takes {us(b_['t10'])} and leaves only a fast "
                f"ringing of about {-b_['rev_peak']:.1f} A, below 1 A after about 0.1 ms and below 0.5 A after {us(b_['t05'])} in the model "
                "(which has little loss at that frequency, so the real one should die sooner).</li>",
                "<li><b>With a plain resistor + capacitor across the pair, the size of the reversed current is set by the resistor alone</b>, counted in units of "
                "the critical value 2√(L/C): about 29 A at half of it, 12 A at it, 4 A at twice and 1.2 A at four times, almost whatever the capacitor. "
                "A larger resistor damps the hold ringing less, and a larger capacitor wins that back. So the way out is a <b>larger capacitor with a resistor well above "
                "critical</b>, not a smaller damper. The price is a longer, shallower tail (time constant R × C).</li>",
                "<li><b>If Test 4 shows no ringing at a hold with no damper fitted: fit none.</b> That is the cleanest switch-off. Look at 5–20 A, not only 100 A: "
                "the model rings most there (10–14 kHz) and hardly at all at 100 A.</li>",
                "<li><b>If a damper is needed, first choice 1.6 Ω + 100 µF</b> (same resistor as proposed, capacitor 22 → 100 µF): damps the model's ringing as well "
                f"as the proposed damper, reversed current {-g2['off']['rev_peak']:.1f} A instead of {-a_['rev_peak']:.0f} A, but it lasts: within 2 A after "
                f"{us(g2['off']['t2'])}, within 0.5 A after {us(g2['off']['t05'])}.</li>",
                f"<li><b>3.2 Ω + 100 µF</b> brings the reversed current to {-g4['off']['rev_peak']:.1f} A (within 2 A after {us(g4['off']['t2'])}, within 0.5 A after "
                f"{us(g4['off']['t05'])}) but damps the ringing clearly less (see the table in section 3). Worth trying only if the lighter damping turns out to be enough.</li>",
                "<li><b>A two-way voltage limiter across the damper capacitor</b> (a bidirectional TVS of about 6 V) keeps the "
                f"proposed 1.6 Ω + 22 µF and stops the capacitor charging beyond about {lim['cap_peak']:.0f} V: reversed current {-lim['rev_peak']:.1f} A, gone "
                f"(within 0.5 A) after {us(lim['t05'])}. At a hold and in normal ramps the capacitor sees at most about 4 V (coil resistance at 100 A plus 16 µH × 200 A/ms), "
                "so the limiter does nothing there and the damping is unchanged. Small model only; not drawn in the Ansys project.</li>",
                "<li><b>Damper moved to rail bar – return bar</b> (outside the bridge): with the bridge closed it is electrically the same place, so it should damp "
                "the same; with the bridge open the body diodes stop its capacitor emptying back through the coils, so <b>no reversed current at all</b>. "
                f"But the capacitor is left charged at about {rail['cap_end']:.0f} V and empties through the coils when the bridge closes again (by a hand estimate a kick of up to "
                "about 10 A in the new direction, larger in a fast flip), unless a bleed resistor across it (about 100 Ω) has had a few milliseconds. "
                "Small model only; the hold damping and the re-closing need their own check before this is built.</li>",
                "<li><b>A damper that charges one way only (a diode in series) is not useful:</b> the bridge runs the coil in both directions, and a series diode "
                "also stops the damper acting on the small hold ringing.</li>",
                f"<li><b>Parts:</b> the damper resistor takes about {min(a_['res_energy'], g4['off']['res_energy']) * 1e3:.0f}–{g2['off']['res_energy'] * 1e3:.0f} mJ per switch-off with a peak of "
                f"{g4['off']['res_peak_power'] / 1e3:.1f}–{g2['off']['res_peak_power'] / 1e3:.1f} kW for about 30 µs, so it must be a pulse-rated type; the capacitor "
                "should be a film type rated 63 V or more (the coil sees about 50 V while the 5KP30A clamps). The 5KP30A takes "
                f"{g2['off']['tvs_energy'] * 1e3:.0f}–{b_['tvs_energy'] * 1e3:.0f} mJ per switch-off. Its peak current in the full circuit is {a_['tvs_peak']:.0f}–{b_['tvs_peak']:.0f} A "
                "for the first few microseconds, more than the coil's 100 A, because the current in the supply cable (1 µH assumed) also has to stop and goes through "
                "the 5KP30A and the pass bank while the bank is still turning off. That is a short pulse for this part, but worth a scope look in Test 4.</li>",
                "<li><b>In a reversal</b> (fastoff_flip) the same swing appears right after zero, as measured on the bench (−0.35 A). There it goes the way the "
                "current is being sent anyway, so it does no harm; the swing only matters for a switch-off to zero.</li>",
                "</ul>"]
    # ---- sweep ---------------------------------------------------------------
    out += ["<h2>3. Damper values: reversed current against how well the hold ringing is damped</h2>",
            "<p>The ringing test: OUT1 fixed, a +10 count step, and how fast the ringing that follows dies, written as each half-swing divided by the "
            "one before (1 = never dies, 0.3 = gone in two or three swings). As in the bench ringing runs the pass transistors' drain-gate capacitance "
            "is set to 17 nF, the value that makes the model ring at the bench's frequencies. <b>The model's ringing dies by itself while the bench's "
            "did not, so this test ranks dampers; it does not prove one is enough.</b></p>",
            "<table><tr><th>Capacitor</th><th>Resistor</th><th>R / 2√(L/C)</th><th>Largest reversed current</th><th>Reversed for</th>"
            "<th>Stays within 2 %</th><th>Stays within 0.5 %</th><th>Ringing at 20 A: half-swing ratio</th><th>Ringing at 5 A: half-swing ratio</th><th>Resistor energy / peak power</th></tr>"]
    for (c, k), g in sorted(grid.items()):
        o = g["off"]
        r20 = f"{g['ring20']['ratio']:.2f}" if "ring20" in g else "—"
        r5 = f"{g['ring5']['ratio']:.2f}" if "ring5" in g else "—"
        mark = " style='background:#E2EFDA'" if any(g is b for b in best) else ""
        out.append(f"<tr{mark}><td>{c:g} µF</td><td>{g['R']:.2f} Ω</td><td>{k:g}</td><td>{o['rev_peak']:.1f} A</td><td>{us(o['rev_until'])}</td><td>{us(o['t2'])}</td><td>{us(o['t05'])}</td>"
                   f"<td>{r20}</td><td>{r5}</td><td>{o['res_energy'] * 1e3:.0f} mJ / {o['res_peak_power']:.0f} W</td></tr>")
    out.append("</table>")
    refs = []
    for n, label in (("mot_ft_ring_nodamper_5A", "MOT, no damper, 5 A"), ("mot_ft_ring_nodamper_20A", "MOT, no damper, 20 A"), ("mot_ft_ring_nodamper_100A", "MOT, no damper, 100 A"),
                     ("mot_ft_ring_damper_5A", "MOT, proposed damper, 5 A"), ("mot_ft_ring_damper_20A", "MOT, proposed damper, 20 A"), ("mot_ft_ring_damper_100A", "MOT, proposed damper, 100 A"),
                     ("ft_ring_kick_nodamper", "bench coil, no damper, 1.9 A"), ("ft_ring_kick_damper", "bench coil with its damper, 1.9 A")):
        if n in ref:
            f = ref[n]["freq"]
            refs.append(f"{label}: {ref[n]['ratio']:.2f}" + (f" ({f / 1e3:.0f} kHz)" if np.isfinite(f) else ""))
    out.append("<p>For reference, the same test: " + "; ".join(refs) + ".</p>")
    out.append(img("sweep"))
    out += rail_section(figs)
    out.append("</body></html>")
    path = os.path.join(RESULTS, "damper_study.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("wrote", path)

    # ---- console summary -------------------------------------------------------
    for label, n in bench_rows:
        print(f"BENCH {label[:55]:55s} 90-2% {us(n['t90_2']):>8s}  min {n['rev_peak']:6.2f} A  {us(n['rev_at']):>8s} after zero  back {us(n['rev_until']):>8s}")
    if fits:
        print("fit", [(round(a), round(w), round(a / w, 2)) for a, w in fits])
    if nd:
        print("F4", {k: (round(float(v), 7) if isinstance(v, (float, np.floating)) else v) for k, v in nd.items()})
    for label, n, src in mot_rows + [(f"pick {g['R']:.1f} ohm {g['C']:g} uF", g["off"], "Ansys") for g in best] + [(l, n, "small") for l, n in alt]:
        print(f"MOT {src:6s} {label[:60]:60s} t10 {us(n['t10']):>7s} t2 {us(n['t2']):>8s} t05 {us(n['t05']):>8s} rev {n['rev_peak']:6.1f} A for {us(n.get('rev_until', n.get('rev_time'))):>7s}"
              f"  TVS {n['tvs_peak']:5.0f} A {n['tvs_energy'] * 1e3:5.1f} mJ  R {n['res_energy'] * 1e3:5.1f} mJ {n['res_peak_power']:6.0f} W  cap {n['cap_peak']:5.1f} V")
    print("ring refs", {k: (round(v["ratio"], 3), round(v["freq"]) if np.isfinite(v["freq"]) else None) for k, v in ref.items()})
    for (c, k), g in sorted(grid.items()):
        print(f"C {c:5g} uF k {k:3g} R {g['R']:6.2f}  rev {g['off']['rev_peak']:6.1f} A until {us(g['off']['rev_until']):>7s} t2 {us(g['off']['t2']):>8s}"
              f"  ring20 {g.get('ring20', {}).get('ratio', np.nan):.2f} ring5 {g.get('ring5', {}).get('ratio', np.nan):.2f}"
              f"  fine-model rev {np.interp(c, C_FINE, fine[k]['rev_peak']):6.1f}")


if __name__ == "__main__":
    main()
