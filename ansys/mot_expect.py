"""What to expect on the 100 A setup: ramps up and drops, and how far to trust each number.

    ansys\\.venv\\Scripts\\python.exe ansys\\run_sims.py --m100      # the runs (about 15 minutes)
    ansys\\.venv\\Scripts\\python.exe ansys\\mot_expect.py           # this report

Reads the m100_* runs (run_sims.py: MOT pair on the SE-600-5 with the blocking diode and the
5KP30A fitted) and writes ansys/results/mot_expectations.html with its plots.

Everything here is a model PREDICTION: the 100 A setup is not built. The accuracy section of
the report says, number by number, what the prediction rests on.
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
sys.path.insert(0, HERE)
from run_sims import M100_VARIANTS, SCENARIOS  # noqa: E402
from simdata import Sim  # noqa: E402

T_ON, T_DOWN = 100e-6, 6.1e-3        # setpoint applied / ramp down or fast switch-off starts (run_sims.py M100)
T_FAST = 2e-3                        # when the bridge opens in the fixed-OUT1 fast switch-off runs
C = {"blue": "#1F497D", "red": "#C0504D", "grey": "#7F7F7F", "orange": "#E08A00", "green": "#4F8F4F", "light": "#8FA9CC", "purple": "#7B5EA7"}
SERIES = [C["blue"], C["red"], C["orange"], C["green"], C["purple"], C["grey"]]

_cache = {}


def sim(name):
    """A run, put on an even time grid (Ansys saves uneven steps): 0.5 us for the 12 ms runs, 0.1 us for the 3 ms ones."""
    if name not in _cache:
        q = Sim(os.path.join(RESULTS, name + ".csv"))
        t = np.arange(0.0, q.t[-1], 0.5e-6 if q.t[-1] > 5e-3 else 0.1e-6)
        for net in q.nets():
            q._v[net] = np.interp(t, q.t, q._v[net])
        q.t = t
        _cache[name] = q
    return _cache[name]


def t_off(name):
    """When the pulse of a run ends: the setpoint source holds for 6 ms after its ramp is complete."""
    return T_ON + var(name, "sp_amps") / var(name, "slew_up") + 6e-3


def have(name):
    return os.path.exists(os.path.join(RESULTS, name + ".csv"))


def var(name, key):
    return float(str(SCENARIOS[name][3].get(key, {"slew_up": "200e3", "sp_amps": "98"}.get(key, "nan"))).rstrip("VuHmOhm"))


def ms(v, digits=2):
    if v is None or not np.isfinite(v):
        return "—"
    return f"{v * 1e3:.{digits}f} ms" if v >= 0.995e-3 else f"{v * 1e6:.0f} µs"


# ---------------------------------------------------------------------------
# numbers from the traces
# ---------------------------------------------------------------------------
def ramp_numbers(name):
    """The ramp up and the hold of one pulse run. Times count from the moment the setpoint starts to rise."""
    s = sim(name)
    target, slew = var(name, "sp_amps"), var(name, "slew_up")
    t_end = min(t_off(name), T_DOWN) - 20e-6
    w = (s.t >= T_ON) & (s.t < t_end)
    t, i = s.t[w] - T_ON, s.i_coil[w]
    n = {"target": target, "slew": slew, "t_ideal": target / slew}
    k = np.where(i >= 0.9 * target)[0]
    n["t90"] = float(t[k[0]]) if len(k) else np.nan
    for key, band in (("t1", 0.01), ("t01", 0.001)):
        out = np.where(np.abs(i - target) > band * target)[0]
        n[key] = np.nan if (len(out) and out[-1] >= len(t) - 3) else (float(t[out[-1]]) if len(out) else 0.0)
    n["peak"] = float(i.max())
    n["over"] = (n["peak"] - target) / target
    hold = t > t[-1] - 0.5e-3
    n["i_hold"] = float(i[hold].mean())
    n["ripple"] = float(i[hold].max() - i[hold].min())
    n["u_hold"] = float(s["u_kcounts"][w][hold].mean() * 1000)
    n["u_max"] = float(s["u_kcounts"][w].max() * 1000)
    n["sup_min"] = float(s["sup_p"][w].min())
    n["rail_hold"] = float(s["rail"][w][hold].mean())
    n["ret_hold"] = float(s["ret"][w][hold].mean())
    n["gate_hold"] = float(s["gate_bus"][w][hold].mean())
    # steepest rise the supply managed, over any 50 us
    step = max(int(50e-6 / (t[1] - t[0])), 1)
    n["rate_max"] = float(((i[step:] - i[:-step]) / (t[step:] - t[:-step])).max())
    n["reached"] = bool(abs(n["i_hold"] - target) < 0.01 * target)
    return n


def drop_numbers(name, t_off, zero=0.5):
    """A drop to zero: OUT1 zeroed with the bridge closed (freewheel) or the bridge opened (fast switch-off)."""
    s = sim(name)
    i = s.i_coil
    i0 = float(np.interp(t_off - 5e-6, s.t, i))
    w = s.t >= t_off
    t, i = s.t[w] - t_off, i[w]
    n = {"i0": i0}
    for key, lvl in (("t10", 0.1 * i0), ("t2", 0.02 * i0), ("tzero", zero)):
        k = np.where(i <= lvl)[0]
        n[key] = float(t[k[0]]) if len(k) else np.nan
    n["rev_peak"] = min(float(i.min()), 0.0)
    out = np.where(np.abs(i) > zero)[0]
    n["t_quiet"] = float(t[out[-1]]) if len(out) and out[-1] < len(t) - 3 else np.nan      # last time outside +-0.5 A
    n["rail_peak"] = float(s["rail"][w].max())
    n["ret_peak"] = float(s["ret"][w].max())
    n["bar_peak"] = float((s["rail"][w] - s["ret"][w]).max())
    k = np.where(i <= 0.9 * i0)[0]
    k2 = np.where(i <= 0.1 * i0)[0]
    n["rate"] = float(0.8 * i0 / (t[k2[0]] - t[k[0]])) if len(k) and len(k2) and k2[0] > k[0] else np.nan
    return n


def down_numbers(name, t_sp2=T_DOWN):
    """A ramp down to a lower, non-zero current."""
    s = sim(name)
    a = var(name, "sp_amps")
    b = a + float(SCENARIOS[name][3]["sp2_amps"])
    w = s.t >= t_sp2
    t, i = s.t[w] - t_sp2, s.i_coil[w]
    n = {"from": a, "to": b, "t_ideal": (a - b) / float(SCENARIOS[name][3]["slew2"])}
    k = np.where(i <= b + 0.1 * (a - b))[0]
    n["t90"] = float(t[k[0]]) if len(k) else np.nan
    for key, band in (("t1", 0.01), ("t01", 0.001)):
        out = np.where(np.abs(i - b) > band * b)[0]
        n[key] = np.nan if (len(out) and out[-1] >= len(t) - 3) else (float(t[out[-1]]) if len(out) else 0.0)
    n["under"] = float(b - i.min())
    return n


# ---------------------------------------------------------------------------
# plots
# ---------------------------------------------------------------------------
def legend(ax, loc, **kw):
    ax.legend(loc=loc, fontsize=8, framealpha=0.9, **kw)


def setpoint(name, t):
    """The requested current of a pulse run (amps) on the time axis t (seconds from the start of the ramp)."""
    target, slew = var(name, "sp_amps"), var(name, "slew_up")
    return np.clip(t * slew, 0, target)


def fig_ramps(figs):
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.8))
    ax = axs[0]
    for col, name in zip(SERIES, ("m100_nominal", "m100_to75", "m100_to50", "m100_to25")):
        if not have("" + name):
            continue
        s = sim(name)
        t = (s.t - T_ON) * 1e3
        m = (t > -0.1) & (t < 3.0)
        ax.plot(t[m], s.i_coil[m], color=col, lw=1.6, label=f"0 → {var(name, 'sp_amps'):.0f} A")
        ax.plot(t[m], setpoint(name, t[m] * 1e-3), color=col, lw=0.9, ls="--")
    ax.plot([], [], color="k", lw=0.9, ls="--", label="requested (200 A/ms ramp)")
    ax.set_xlabel("time after the ramp starts (ms)"); ax.set_ylabel("coil current (A)"); ax.grid(alpha=0.3)
    ax.set_title("Ramps up to four currents, firmware ramp 200 A/ms", fontsize=10)
    ax.set_xlim(-0.1, 3.0); ax.set_ylim(-3, 112); legend(ax, "lower right")
    ax = axs[1]
    for col, (name, lab) in zip(SERIES, (("m100_step", "no ramp (step)"), ("m100_nominal", "200 A/ms"), ("m100_slew100", "100 A/ms"), ("m100_slew50", "50 A/ms"))):
        if not have(name):
            continue
        s = sim(name)
        t = (s.t - T_ON) * 1e3
        m = (t > -0.1) & (t < 3.5)
        ax.plot(t[m], s.i_coil[m], color=col, lw=1.6, label=lab)
        ax.plot(t[m], setpoint(name, t[m] * 1e-3), color=col, lw=0.9, ls="--")
    ax.set_xlabel("time after the ramp starts (ms)"); ax.set_ylabel("coil current (A)"); ax.grid(alpha=0.3)
    ax.set_title("0 → 98 A at different ramp rates (dashed: requested)", fontsize=10)
    ax.set_xlim(-0.1, 3.5); ax.set_ylim(-3, 112); legend(ax, "lower right")
    fig.tight_layout(); fig.savefig(os.path.join(RESULTS, "mot_expect_ramps.png"), dpi=110); plt.close(fig)
    figs["ramps"] = ("mot_expect_ramps.png", "Model prediction. Solid: coil current. Dashed: the current the firmware asks for.")


def fig_anatomy(figs):
    """Where the 5 V goes during the nominal ramp."""
    s = sim("m100_nominal")
    t = (s.t - T_ON) * 1e3
    m = (t > -0.1) & (t < 3.0)
    fig, axs = plt.subplots(3, 1, figsize=(9, 8.2), sharex=True)
    ax = axs[0]
    ax.plot(t[m], s.i_coil[m], color=C["blue"], lw=1.6, label="coil current")
    ax.plot(t[m], setpoint("m100_nominal", t[m] * 1e-3), color="k", lw=0.9, ls="--", label="requested")
    ax.set_ylabel("current (A)"); ax.grid(alpha=0.3); ax.set_ylim(-3, 112); legend(ax, "lower right")
    ax.set_title("0 → 98 A, every value as assumed: what limits the ramp", fontsize=10)
    ax = axs[1]
    ax.plot(t[m], s["sup_p"][m], color=C["grey"], lw=1.4, label="supply terminals")
    ax.plot(t[m], s["rail"][m], color=C["blue"], lw=1.4, label="rail bar (after the blocking diode)")
    ax.plot(t[m], s["ret"][m], color=C["red"], lw=1.4, label="drain bar (what is left for the pass bank)")
    ax.set_ylabel("voltage (V)"); ax.grid(alpha=0.3); ax.set_ylim(-0.3, 7.6); legend(ax, "upper right", ncol=3)
    ax = axs[2]
    ax.plot(t[m], s["u_kcounts"][m] * 1000, color=C["orange"], lw=1.4, label="OUT1 command")
    clamp = float(SCENARIOS["m100_nominal"][3]["u_clamp"])
    ax.axhline(clamp, color="k", lw=0.9, ls=":", label=f"OUT1 clamp ({clamp:.0f} counts)")
    ax.set_ylabel("OUT1 (counts)"); ax.set_xlabel("time after the ramp starts (ms)"); ax.grid(alpha=0.3)
    ax.set_ylim(3000, clamp + 900); legend(ax, "lower right")
    ax.set_xlim(-0.1, 3.0)
    fig.tight_layout(); fig.savefig(os.path.join(RESULTS, "mot_expect_anatomy.png"), dpi=110); plt.close(fig)
    figs["anatomy"] = ("mot_expect_anatomy.png", "Model prediction, the same run as the blue trace above. Middle: the three voltages along the power path. "
                       "Bottom: the controller's command to the pass bank.")


GROUPS = (
    ("Coil pair: inductance and resistance", (("m100_L12", "12 µH"), ("m100_L20", "20 µH"), ("m100_Rlo", "5.4 mΩ"), ("m100_Rhi", "10.4 mΩ"))),
    ("Supply and its cable", (("m100_soft_supply", "sags 0.5 V at 100 A"), ("m100_lead_hi", "cable 4 mΩ, 3 µH"), ("m100_V4p75", "set to 4.75 V"), ("m100_V5p5", "set to 5.5 V"))),
    ("Pass transistors", (("m100_K10", "law 2.7× steeper"), ("m100_K30", "law 8× steeper"), ("m100_hot", "hot (threshold −0.24 V)"), ("m100_cgd17", "17 nF drain-gate"))),
    ("Controller settings", (("m100_gain0p5", "gains × 0.5"), ("m100_gain2", "gains × 2"), ("m100_gain4", "gains × 4"), ("m100_clamp_open", "OUT1 clamp wide open"))),
)


def fig_sensitivity(figs):
    fig, axs = plt.subplots(2, 2, figsize=(13, 8.4))
    for ax, (title, runs) in zip(axs.ravel(), GROUPS):
        s = sim("m100_nominal")
        t = (s.t - T_ON) * 1e3
        m = (t > -0.1) & (t < 3.0)
        ax.plot(t[m], s.i_coil[m], color="k", lw=2.0, label="as assumed")
        for col, (name, lab) in zip(SERIES, runs):
            if not have(name):
                continue
            q = sim(name)
            tq = (q.t - T_ON) * 1e3
            mq = (tq > -0.1) & (tq < 3.0)
            ax.plot(tq[mq], q.i_coil[mq], color=col, lw=1.3, label=lab)
        ax.axhline(98, color="k", lw=0.7, ls=":")
        ax.set_title(title, fontsize=10); ax.grid(alpha=0.3)
        ax.set_xlim(-0.1, 3.0); ax.set_ylim(-3, 118)
        ax.set_xlabel("time after the ramp starts (ms)"); ax.set_ylabel("coil current (A)")
        legend(ax, "lower right")
    fig.tight_layout(); fig.savefig(os.path.join(RESULTS, "mot_expect_sensitivity.png"), dpi=110); plt.close(fig)
    figs["sens"] = ("mot_expect_sensitivity.png", "Model prediction: the 0 → 98 A ramp again, one assumed value changed at a time. Black: every value as assumed.")


def fig_drops(figs):
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.8))
    ax = axs[0]
    for col, name in zip(SERIES, ("m100_nominal", "m100_to75", "m100_to50", "m100_to25")):
        if not have(name):
            continue
        s = sim(name)
        t = (s.t - t_off(name)) * 1e3
        m = (t > -0.1) & (t < 3.0)
        ax.plot(t[m], s.i_coil[m], color=col, lw=1.6, label=f"from {var(name, 'sp_amps'):.0f} A")
    ax.set_title("Setpoint to zero, bridge kept closed (the coil freewheels)", fontsize=10)
    ax.set_xlabel("time after the setpoint goes to zero (ms)"); ax.set_ylabel("coil current (A)"); ax.grid(alpha=0.3)
    ax.set_xlim(-0.1, 3.0); ax.set_ylim(-5, 112); legend(ax, "upper right")
    ax = axs[1]
    for col, (name, lab) in zip(SERIES, (("mot_ft_off_damper", "from 100 A"), ("m100_fast_75A", "from 75 A"), ("m100_fast_50A", "from 50 A"), ("m100_fast_25A", "from 25 A"))):
        if not have(name):
            continue
        s = sim(name)
        t = (s.t - T_FAST) * 1e6
        m = (t > -10) & (t < 250)
        ax.plot(t[m], s.i_coil[m], color=col, lw=1.6, label=lab)
    ax.set_title("Bridge opened (fast switch-off), damper 1.6 Ω + 22 µF", fontsize=10)
    ax.set_xlabel("time after the bridge opens (µs)"); ax.set_ylabel("coil current (A)"); ax.grid(alpha=0.3)
    ax.set_xlim(-10, 250); ax.set_ylim(-20, 112); legend(ax, "upper right")
    ax = axs[2]
    for col, (name, lab) in zip(SERIES, (("m100_down_98_50_step", "98 → 50 A, setpoint stepped"), ("m100_down_98_50", "98 → 50 A at 50 A/ms"),
                                         ("m100_down_98_20", "98 → 20 A at 50 A/ms"), ("m100_down_50_20", "50 → 20 A at 50 A/ms"))):
        if not have(name):
            continue
        s = sim(name)
        t = (s.t - T_DOWN) * 1e3
        m = (t > -0.1) & (t < 3.0)
        ax.plot(t[m], s.i_coil[m], color=col, lw=1.6, label=lab)
    ax.set_title("Ramps down to a lower current", fontsize=10)
    ax.set_xlabel("time after the ramp down starts (ms)"); ax.set_ylabel("coil current (A)"); ax.grid(alpha=0.3)
    ax.set_xlim(-0.1, 3.0); ax.set_ylim(0, 125); legend(ax, "upper right")
    fig.tight_layout(); fig.savefig(os.path.join(RESULTS, "mot_expect_drops.png"), dpi=110); plt.close(fig)
    figs["drops"] = ("mot_expect_drops.png", "Model prediction. Left: today's switch-off. Middle: the fast switch-off with the new parts (note the microsecond scale). "
                     "Right: going down to a lower current under the controller.")


if __name__ == "__main__":
    for n in sorted(k for k in SCENARIOS if k.startswith("m100_")):
        if not have(n):
            print("missing", n)
            continue
        if n.startswith("m100_fast_"):
            d = drop_numbers(n, T_FAST)
            print(f"{n:22s} FAST i0 {d['i0']:6.1f} t10 {ms(d['t10'])} tzero {ms(d['tzero'])} rev {d['rev_peak']:6.2f} quiet {ms(d['t_quiet'])} bar {d['bar_peak']:.1f} rail {d['rail_peak']:.1f}")
            continue
        r = ramp_numbers(n)
        line = (f"{n:22s} t90 {ms(r['t90'])} t1% {ms(r['t1'])} t0.1% {ms(r['t01'])} peak {r['peak']:6.2f} ({r['over'] * 100:+.1f}%) hold {r['i_hold']:.2f} rip {r['ripple'] * 1e3:.0f}mA "
                f"u {r['u_hold']:.0f}/{r['u_max']:.0f} supmin {r['sup_min']:.2f} rail {r['rail_hold']:.2f} ret {r['ret_hold']:.2f} gate {r['gate_hold']:.2f} rate {r['rate_max'] / 1e3:.0f}A/ms")
        if "sp2_amps" in SCENARIOS[n][3]:
            d = down_numbers(n)
            line += f"\n      DOWN {d['from']:.0f}->{d['to']:.0f}: t90 {ms(d['t90'])} t1% {ms(d['t1'])} t0.1% {ms(d['t01'])} under {d['under']:.2f} A"
        else:
            d = drop_numbers(n, T_DOWN if n == 'm100_fastoff' else t_off(n))
            line += (f"\n      DROP i0 {d['i0']:.1f} t10 {ms(d['t10'])} t2 {ms(d['t2'])} t0.5A {ms(d['tzero'])} rev {d['rev_peak']:.2f} quiet {ms(d['t_quiet'])} "
                     f"rate {d['rate'] / 1e3:.0f} A/ms ret pk {d['ret_peak']:.1f} rail pk {d['rail_peak']:.1f} bar {d['bar_peak']:.1f}")
        print(line)
