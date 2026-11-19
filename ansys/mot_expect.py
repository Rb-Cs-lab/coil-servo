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
    k = np.where(i >= 0.02 * target)[0]
    n["t_start"] = float(t[k[0]]) if len(k) else np.nan        # the wait before any current flows
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
    v = m & (t > 0.03)        # leave out the model's own start-up (bridge enabled at 10 us)
    ax.plot(t[v], s["sup_p"][v], color=C["grey"], lw=1.4, label="supply terminals")
    ax.plot(t[v], s["rail"][v], color=C["blue"], lw=1.4, label="rail bar (after the blocking diode)")
    ax.plot(t[v], s["ret"][v], color=C["red"], lw=1.4, label="drain bar (what is left for the pass bank)")
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



UNKNOWN = ("L12", "L20", "Rlo", "Rhi", "lead_lo", "lead_hi", "soft_supply", "Csup1m", "Csup47m", "K10", "K30", "hot", "cgd17")     # values we cannot choose


def spread(fn, key, names=UNKNOWN):
    v = [fn("m100_" + n)[key] for n in ("nominal",) + tuple(names) if have("m100_" + n)]
    v = [x for x in v if np.isfinite(x)]
    return min(v), max(v)


def rng(lo, hi, fmt):
    a, b = fmt(lo), fmt(hi)
    return a if a == b else f"{a} to {b}"


def pct(v):
    return f"{v * 100:.1f} %"


def write_report():
    figs = {}
    fig_ramps(figs); fig_anatomy(figs); fig_sensitivity(figs); fig_drops(figs)
    img = lambda k: f"<p><img src='{figs[k][0]}'><br><span class='note'>{html.escape(figs[k][1])}</span></p>"
    R = lambda n: ramp_numbers("m100_" + n)
    D = lambda n: drop_numbers("m100_" + n, t_off("m100_" + n))
    nom, dn = R("nominal"), D("nominal")
    fo = drop_numbers("m100_fastoff", T_DOWN)
    amp = lambda v: f"{v:.1f} A"
    volts = lambda v: f"{v:.0f} V"
    volts1 = lambda v: f"{v:.1f} V"
    r90, r1, r01, rov = (spread(ramp_numbers, k) for k in ("t90", "t1", "t01", "over"))
    dz = spread(lambda n: drop_numbers(n, t_off(n)), "tzero")
    rpk = spread(lambda n: drop_numbers(n, t_off(n)), "ret_peak")
    fast = {a: drop_numbers(f"m100_fast_{a}A", T_FAST) for a in (25, 50, 75)}
    fl = [drop_numbers(f"m100_fast_L{l}", T_FAST) for l in (12, 20)]
    c52, cop = R("clamp_5200"), R("clamp_open")
    u_lo, u_hi = spread(ramp_numbers, "u_hold")
    d9850, d9850s = down_numbers("m100_down_98_50"), down_numbers("m100_down_98_50_step")

    out = ["<html><head><meta charset='utf-8'><title>100 A setup: what to expect</title><style>",
           "body { font-family: Calibri, Arial, sans-serif; font-size: 11pt; max-width: 980px; margin: 24px auto; }",
           "h1, h2, h3 { color: #1F497D; } table { border-collapse: collapse; margin: 8px 0; }",
           "th { background: #DDEBF7; text-align: left; } th, td { border: 1px solid #9DB6D3; padding: 3px 8px; vertical-align: top; }",
           "img { max-width: 100%; } .note { color: #444; } td.g { background: #E2F0D9; } td.y { background: #FFF2CC; } td.r { background: #FBE5D6; }",
           "</style></head><body>",
           "<h1>100 A setup: ramps and drops to expect, and how far to trust them</h1>",
           "<p>Circuit model (Ansys, design <i>mot_ft</i>): the two MOT coils on the SE-600-5, blocking diode and 5KP30A fitted, damper 1.6 Ω + 22 µF, "
           "controller gains kp 3.7 counts/A, ki 111 counts/(A·ms), firmware ramp limit 200 A/ms, no feedforward. "
           "<b>Nothing here is measured: the 100 A setup is not built.</b> 41 runs; in most of them one assumed value is changed to see how much the answer moves. "
           "Made 2026-11-19 with <i>run_sims.py --m100</i> and <i>mot_expect.py</i>.</p>",

           "<h2>1. In short</h2><ul>",
           f"<li><b>Ramp 0 → 98 A:</b> 90 % after about {ms(nom['t90'])} ({rng(*r90, ms)} over the unknown values), within 1 % after {ms(nom['t1'])}, "
           f"within 0.1 % after {ms(nom['t01'])} ({rng(*r01, ms)}). The 5 V supply sets this, not the controller: for most of the ramp the pass bank is fully open "
           f"and the current rises as fast as the supply can push it (up to about {nom['rate_max'] / 1e3:.0f} A/ms). The target of 0.1 % in under 1 ms is not met for a full-size ramp in any run.</li>",
           f"<li><b>Overshoot at 98 A: {pct(nom['over'])}</b> ({rng(*rov, pct)}), peak {amp(nom['peak'])}. That is above the 100 A trip level, so <b>a ramp straight to 98 A may trip</b>. "
           "No overshoot in the model for 75 A and below, or with the ramp slowed to 50 A/ms, or with the gains halved. The bench showed more overshoot than the model (1–2.4 % against 0), so expect at least this much.</li>",
           f"<li><b>Setpoint to zero, bridge kept closed:</b> from 98 A the current reaches the ±0.5 A zero window after {ms(dn['tzero'])} ({rng(*dz, ms)}); "
           f"from 25 A after {ms(D('to25')['tzero'])}. The drain bar jumps to {dn['ret_peak']:.0f} V for a few microseconds (supply-cable energy, caught by the 5KP12CA).</li>",
           f"<li><b>Fast switch-off (bridge opened):</b> zero after {ms(fo['tzero'])} from 98 A, {ms(fast[25]['tzero'])} from 25 A; then a reversed current of {fo['rev_peak']:.0f} A "
           f"(98 A) to {fast[25]['rev_peak']:.1f} A (25 A) through the damper, back inside ±0.5 A after {ms(fo['t_quiet'])}. Rail bar {fo['rail_peak']:.0f} V.</li>",
           f"<li><b>Going down to a lower current</b> under the firmware's 50 A/ms limit is clean in the model (98 → 50 A: within 1 % after {ms(d9850['t1'])}, no undershoot). "
           f"Without that limit the current would dip {d9850s['under']:.0f} A too low first.</li>",
           f"<li><b>The OUT1 clamp must be measured.</b> With the pass-transistor law taken from the bench, 98 A needs {nom['u_hold']:.0f} counts and the provisional clamp of 5200 counts "
           f"would stop the current at {c52['i_hold']:.0f} A; with a datasheet-like law 98 A needs only {R('K30')['u_hold']:.0f} counts. Left wide open, the clamp lets the overshoot grow to {pct(cop['over'])}.</li>",
           "</ul>",

           "<h2>2. How far to trust each number</h2>",
           "<p>Three things limit the accuracy, and they are different for each number: (a) how well this model did on the bench for the same kind of measurement, "
           "(b) values of the 100 A setup that are not measured yet (the range column: the spread when each is changed over what seems plausible), "
           "(c) effects that are not in the model at all. Green: expect it within about 25 %. Yellow: within about 30 %, or right direction and size. Red: do not rely on it.</p>",
           "<table><tr><th>Number</th><th>Model</th><th>Range over the unknown values</th><th>What it rests on</th><th>Same kind of number on the bench (model / measured)</th><th>Trust</th></tr>"]

    def row(what, val, span, rests, bench, cls, trust):
        out.append(f"<tr><td>{what}</td><td>{val}</td><td>{span}</td><td>{rests}</td><td>{bench}</td><td class='{cls}'>{trust}</td></tr>")

    row("Setpoint to zero from 98 A, bridge closed: time to the zero window", ms(dn["tzero"]), rng(*dz, ms),
        "Coil inductance (computed, taken ±25 %), the bridge diode's voltage at 100 A (0.8 V, from the datasheet), coil and cable resistance",
        "2 A → 0: 159 µs / about 170 µs", "g", "±25 %, mostly the inductance")
    row("Fast switch-off from 98 A: time to zero", ms(fo["tzero"]), f"{ms(fl[0]['tzero'])} to {ms(fl[1]['tzero'])} (12 to 20 µH, from 100 A)",
        "Coil inductance and the 5KP30A's clamping voltage (45–49 V at this current, datasheet)", "2 A: 21 µs / 25 µs; clamp 42 V / 49 V", "g", "±25 %")
    row("Fast switch-off: reversed current and how long it lasts", f"{fo['rev_peak']:.0f} A, quiet after {ms(fo['t_quiet'])}", f"{fl[0]['rev_peak']:.0f} to {fl[1]['rev_peak']:.0f} A",
        "The damper actually fitted (values not chosen yet) and the coil inductance", "2 A: −0.33 A / −0.31 A, but one third too fast", "y", "±30 %; follows the damper")
    row("Rail bar and drain bar peaks at a switch-off", f"{fo['rail_peak']:.0f} V (fast), {dn['ret_peak']:.0f} V (bridge closed)", f"{rng(*rpk, volts)} (bridge closed)",
        "Datasheet curves of the two surge protectors, typed from memory; supply-cable inductance (1 µH assumed)", "Fast: 42 V / 49 V. Bridge closed: 14.9 V / 12.1 V", "y", "±20 %; check the datasheets")
    row("Ramp 0 → 98 A: time to 90 %", ms(nom["t90"]), rng(*r90, ms),
        "Supply voltage left after the blocking diode, cable and coil resistance; coil inductance; how long the controller takes to get the pass bank conducting "
        f"(about {ms(nom['t_start'])} here, with no feedforward)", "0 → 2 A: 0.48 ms / 0.45–0.47 ms (set by the controller there, by the supply here)", "y", "±30 %")
    row("Ramp 0 → 98 A: time to within 1 % / 0.1 %", f"{ms(nom['t1'])} / {ms(nom['t01'])}", f"{rng(*r1, ms)} / {rng(*r01, ms)}",
        "As above, plus the controller gains (to be re-tuned on the hardware) and the overshoot", "0 → 2 A, 98 %: 0.69 ms / 0.62–0.65 ms", "y", "A lower limit. Noise, warming and ringing can only add")
    row("Overshoot at 98 A", pct(nom["over"]), rng(*rov, pct),
        "Where the OUT1 clamp sits above the hold value, the gains, the pass-transistor law", "0.0 % / 1–2.4 %: the model showed less than the bench", "r", "Size not reliable; that there is one, yes")
    row("OUT1 command at 98 A (sets the clamp)", f"{nom['u_hold']:.0f} counts", f"{u_lo:.0f} to {u_hi:.0f} counts",
        "The pass-transistor law, measured only up to 2.5 A and stretched 40 times", "Within 2–8 % inside the measured range", "r", "Not predicted. Measure it first")
    row("Voltage left across the pass bank at 98 A (heat)", f"{nom['ret_hold']:.1f} V, {nom['ret_hold'] * 98 / 6:.0f} W per transistor with its ballast",
        rng(*spread(ramp_numbers, "ret_hold"), volts1), "Supply setting, blocking-diode drop (0.8 V), every resistance in the loop (cable and contacts not measured)",
        "Rail bar at 2 A: 4.61 V / 4.60 V", "y", "±0.5 V")
    row("Small wobble, ringing or noise at a hold; the 0.1 % stability", "none", "none", "Not in the model: it has no noise, and on the bench it did not keep the 10–20 kHz ringing going",
        "Ringing: dies away / stays at ±0.1 A", "r", "Cannot say")
    out += ["</table>",
            "<p><b>Not in the model, and able to change the picture:</b></p><ul>",
            "<li><b>How the SE-600-5 reacts to a load that goes from 0 to 100 A in half a millisecond.</b> The model's supply holds 5 V perfectly behind 0.5 mΩ, so its output capacitance makes no difference here "
            f"(1 mF and 47 mF give the same ramp). A real switching supply may dip for a millisecond or go into current limit. A supply that sags 0.5 V costs {ms(R('soft_supply')['t90'] - nom['t90'])} on the 90 % time; "
            "a worse one costs more. Watch the rail bar on the first ramps.</li>",
            "<li><b>Eddy currents in the chamber and mounts.</b> They make the coil look like a smaller inductance at first (faster current, as on the bench: 37 µH against 45 µH computed) "
            "and make the field settle later than the current.</li>",
            "<li><b>Warming of the pass transistors during a pulse</b> (about 50 W each at 98 A): the current at a fixed command rises as they warm; the controller corrects it, at its own speed.</li>",
            "<li><b>The firmware's digital side:</b> 1 µs steps, 14-bit numbers, dither, the exact ramp generator. Checked separately in the firmware test bench; small against the effects above.</li>",
            "<li><b>The 229 kHz burst on IN1 and the 7–8 kHz ring after a ramp</b> seen on the bench on 11-18: not reproduced by the model, so not predicted for 100 A either.</li>",
            "<li><b>Feedforward</b> (tried on the bench on 11-18): not in the model. It would remove most of the wait before the current starts.</li></ul>",

            "<h2>3. Ramps up</h2>", img("ramps"),
            "<table><tr><th>Ramp</th><th>Wait before current flows</th><th>90 %</th><th>Within 1 %</th><th>Within 0.1 %</th><th>Peak</th><th>OUT1 at the hold</th><th>Across the pass bank at the hold</th><th>Heat per pass transistor with its ballast resistor</th></tr>"]
    for n, lab in (("to25", "0 → 25 A, 200 A/ms"), ("to50", "0 → 50 A, 200 A/ms"), ("to75", "0 → 75 A, 200 A/ms"), ("nominal", "0 → 98 A, 200 A/ms"),
                   ("slew100", "0 → 98 A, 100 A/ms"), ("slew50", "0 → 98 A, 50 A/ms"), ("step", "0 → 98 A, no ramp limit")):
        r = R(n)
        out.append(f"<tr><td>{lab}</td><td>{ms(r['t_start'])}</td><td>{ms(r['t90'])}</td><td>{ms(r['t1'])}</td><td>{ms(r['t01'])}</td><td>{amp(r['peak'])} ({r['over'] * 100:+.1f} %)</td>"
                   f"<td>{r['u_hold']:.0f} counts</td><td>{r['ret_hold']:.2f} V</td><td>{r['ret_hold'] * r['i_hold'] / 6:.0f} W</td></tr>")
    out += ["</table>",
            f"<p>Times count from the moment the setpoint starts to rise. Small ramps are not faster than large ones: the first {ms(R('to25')['t_start'])} or so go by before any current flows, "
            "while the controller raises OUT1 from its resting value to where the pass bank starts to conduct, and near that point the bank answers weakly, so the controller is slow there. "
            "Large ramps then run into the supply's limit instead.</p>", img("anatomy"),
            f"<p>At 98 A the supply terminals are at {nom['sup_min']:.2f} V, the rail bar at {nom['rail_hold']:.2f} V (the blocking diode takes 0.8 V) and {nom['ret_hold']:.1f} V are left across the pass bank. "
            "During the ramp that last voltage falls to about 1 V: the bank is then fully open and the controller's command sits on the clamp. The overshoot comes from the time the controller needs to come back off the clamp.</p>",

            "<h2>4. One assumed value changed at a time (0 → 98 A, 200 A/ms)</h2>", img("sens"),
            "<table><tr><th>Changed</th><th>90 %</th><th>Within 1 %</th><th>Within 0.1 %</th><th>Peak</th><th>OUT1 at the hold</th><th>Across the pass bank</th><th>Zero window after setpoint → 0</th></tr>"]
    order = ("nominal", "L12", "L20", "Rlo", "Rhi", "lead_lo", "lead_hi", "soft_supply", "Csup1m", "Csup47m", "V4p75", "V5p25", "V5p5", "K10", "K30", "K30_gain0p5", "hot", "cgd17",
             "gain0p5", "gain2", "gain4", "clamp_open", "clamp_5200", "nodamper", "damper100")
    for n in order:
        r, d = R(n), D(n)
        lab = html.escape(M100_VARIANTS[n][1]) if n != "nominal" else "<b>nothing (every value as assumed)</b>"
        lab = lab.replace("uH", "µH").replace("mOhm", "mΩ").replace("uF", "µF").replace(" ohm", " Ω").replace(" x ", " × ")
        peak = f"{amp(r['peak'])} ({r['over'] * 100:+.1f} %)" if r["reached"] else f"stops at {r['i_hold']:.0f} A"
        out.append(f"<tr><td>{lab[0].upper() + lab[1:]}</td><td>{ms(r['t90'])}</td><td>{ms(r['t1'])}</td><td>{ms(r['t01'])}</td><td>{peak}</td><td>{r['u_hold']:.0f}</td>"
                   f"<td>{r['ret_hold']:.2f} V</td><td>{ms(d['tzero'])}</td></tr>")
    out += ["</table>",
            "<p>The gains are per count, so a steeper pass-transistor law acts like higher gains: the two rows with the 8 times steeper law show that halving the gains then brings back a ramp without overshoot. "
            "The gains will be tuned on the hardware; the table shows which way each change pushes.</p>",

            "<h2>5. Drops</h2>", img("drops"),
            "<table><tr><th>Setpoint to zero, bridge kept closed</th><th>Down to 10 %</th><th>Inside ±0.5 A</th><th>Fall rate</th><th>Drain bar peak</th></tr>"]
    for n in ("to25", "to50", "to75", "nominal"):
        d = D(n)
        out.append(f"<tr><td>From {d['i0']:.0f} A</td><td>{ms(d['t10'])}</td><td>{ms(d['tzero'])}</td><td>{d['rate'] / 1e3:.0f} A/ms</td><td>{d['ret_peak']:.0f} V</td></tr>")
    out.append("</table><table><tr><th>Fast switch-off (bridge opened), damper 1.6 Ω + 22 µF</th><th>Zero after</th><th>Reversed current</th><th>Inside ±0.5 A for good</th><th>Rail bar peak</th></tr>")
    for lab, d in [(f"From {a} A", fast[a]) for a in (25, 50, 75)] + [("From 98 A", fo), ("From 100 A, coil pair 12 µH", fl[0]), ("From 100 A, coil pair 20 µH", fl[1])]:
        out.append(f"<tr><td>{lab}</td><td>{ms(d['tzero'])}</td><td>{d['rev_peak']:.1f} A</td><td>{ms(d['t_quiet'])}</td><td>{d['rail_peak']:.0f} V</td></tr>")
    out.append("</table><table><tr><th>Down to a lower current</th><th>90 % of the change</th><th>Within 1 % of the new value</th><th>Within 0.1 %</th><th>Dip below the new value</th></tr>")
    for n, lab in (("down_98_50", "98 → 50 A, 50 A/ms"), ("down_98_20", "98 → 20 A, 50 A/ms"), ("down_50_20", "50 → 20 A, 50 A/ms"), ("down_98_50_step", "98 → 50 A, setpoint stepped (no ramp limit)")):
        d = down_numbers("m100_" + n)
        out.append(f"<tr><td>{lab}</td><td>{ms(d['t90'])}</td><td>{ms(d['t1'])}</td><td>{ms(d['t01'])}</td><td>{max(d['under'], 0):.1f} A</td></tr>")
    out += ["</table><ul>",
            f"<li>With the bridge closed the coil's current falls only against one diode drop and the coil's own resistance, {D('to25')['rate'] / 1e3:.0f}–{dn['rate'] / 1e3:.0f} A/ms. "
            "The fast switch-off is about 50 times quicker but leaves the reversed current; the damper report (damper_study.html) covers how the damper values change it.</li>",
            f"<li>After a switch-off with the bridge closed the rail bar is left near {dn['rail_peak']:.0f} V at first in the model: the supply cable's current has nowhere to go when the bank shuts, "
            "the 5KP12CA catches it, and the blocking diode then keeps the charge on the rail bar. Harmless for the parts (all rated far higher), and one more reason for the bleeder resistor proposed earlier.</li>",
            "<li>A ramp down to a lower current follows the 50 A/ms limit closely: the controller, not the coil, sets it.</li></ul>",

            "<h2>6. What to measure first in Test 4, to replace the red and yellow rows</h2><ol>",
            "<li>Current against OUT1, slowly, up to the highest current used: gives the clamp, and the real pass-transistor law for this model.</li>",
            "<li>Rail bar and supply terminals during the first ramps (how the SE-600-5 takes the load step).</li>",
            "<li>First ramps to 75 A or less, or at 50 A/ms, until the overshoot is known; then decide how close to the 100 A trip the hold can sit.</li>",
            "<li>Coil pair inductance and resistance (LCR meter, four-wire), then one switch-off with the bridge closed: its fall time checks the diode drop.</li>",
            "<li>Ringing at holds of 5–20 A and near 100 A, with and without the damper.</li></ol>",
            "<p class='note'>With those five in hand the model can be re-run and the ranges in section 2 should shrink toward the bench level (about 10 %).</p>",
            "</body></html>"]
    path = os.path.join(RESULTS, "mot_expectations.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("wrote", path)


if __name__ == "__main__":
    write_report()
