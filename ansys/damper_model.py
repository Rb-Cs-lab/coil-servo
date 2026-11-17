"""A small stand-alone model of the coil current after the bridge is opened at current.

Only the parts that carry the coil's current once the four bridge transistors are off:

    coil (L, R)  --  two bridge body diodes + the 5KP30A (rail bar <-> return bar)
                 --  the RC damper

It is fast (hundreds of damper values in a few seconds), which the full Ansys
circuit is not, so damper_study.py uses it for the wide sweep and for damper
circuits that are not drawn in the Ansys project. damper_study.py also checks it
against the Ansys runs. Same device laws as models/coil_servo_parts.lib.

Everything is a numpy array over the damper values, so one call does a whole sweep.
"""
from __future__ import annotations

import numpy as np

VT = 0.02585

# clamp path: 5KP30A  I = 5 mA (V/35)^31 ; body diode IXFN420N10T  is 8 nA, n 1, rs 2 mOhm (parts library)
_I = np.logspace(-9, 3.3, 2000)
_V_TVS = 35.0 * (_I / 5e-3) ** (1 / 31)
_V_BODY = VT * np.log(_I / 8e-9 + 1) + 2e-3 * _I
_V_PATH = _V_TVS + 2 * _V_BODY          # coil voltage needed to push current I through diode - TVS - diode


def i_clamp(u):
    """Current through body diode - TVS - body diode for a coil voltage u (either sign)."""
    a = np.abs(u)
    return np.sign(u) * np.where(a < _V_PATH[0], 0.0, np.interp(a, _V_PATH, _I))


def i_tvs(v):
    """5KP30A current for a rail-bar voltage v (reverse direction only)."""
    return np.where(v < _V_TVS[0], 0.0, np.interp(v, _V_TVS, _I))


def v_body(i):
    return np.interp(np.abs(i), _I, _V_BODY)


def i_limiter(v, v_lim):
    """A two-way voltage limiter (bidirectional TVS) across the damper capacitor: 1 mA at v_lim, steep above."""
    return 1e-3 * (v / v_lim) * np.abs(v / v_lim) ** 30


def switch_off(L, R_coil, I0, R_d, C_d, where="coil", v_lim=None, t_end=600e-6, dt=50e-9, keep=10):
    """Bridge opened at t = 0 with the coil carrying I0.

    R_d, C_d   damper resistor and capacitor (arrays of the same shape); R_d = inf for no damper
    where      "coil": damper across the coil (as on the bench and in the MOT proposal)
               "rail": damper from the rail bar to the return bar, i.e. behind the bridge's body diodes
    v_lim      if given: a two-way voltage limiter of this voltage across the damper capacitor
    Returns a dict of arrays, time first: t, i (coil), u (voltage across the coil, positive opposes I0),
    vc (capacitor), i_d (damper current), i_t (5KP30A current).
    """
    R_d = np.atleast_1d(np.asarray(R_d, float)); C_d = np.atleast_1d(np.asarray(C_d, float))
    shape = np.broadcast(R_d, C_d).shape
    R_d, C_d = np.broadcast_to(R_d, shape).copy(), np.broadcast_to(C_d, shape).copy()
    g_d = np.where(np.isfinite(R_d), 1.0 / R_d, 0.0)
    i = np.full(shape, float(I0))
    # before the switch the damper capacitor sits at the coil's own resistive voltage
    vc = np.full(shape, -I0 * R_coil if where == "coil" else I0 * R_coil)
    n = int(round(t_end / dt))
    out = {k: [] for k in ("t", "i", "u", "vc", "i_d", "i_t")}

    for step in range(n + 1):
        if where == "coil":
            # coil node: i = damper current + clamp current, both functions of the coil voltage u
            def cur(u):
                return (u - vc) * g_d + i_clamp(u)
            lo, hi = np.full(shape, -90.0), np.full(shape, 90.0)
            for _ in range(46):          # implicit step: solve for u by bisection (the function rises with u)
                u = 0.5 * (lo + hi)
                c = cur(u)
                res = c - i + dt / L * (u + R_coil * c)
                hi = np.where(res > 0, u, hi); lo = np.where(res > 0, lo, u)
            u = 0.5 * (lo + hi)
            i_new = cur(u)
            i_d = (u - vc) * g_d
            i_t = np.abs(i_clamp(u))
        else:
            # rail node behind the body diodes: coil current (>= 0 only) = 5KP30A current + damper current
            def cur(vr):
                return np.maximum(i_tvs(vr) + (vr - vc) * g_d, 0.0)
            lo, hi = np.full(shape, -5.0), np.full(shape, 90.0)
            for _ in range(46):
                vr = 0.5 * (lo + hi)
                c = cur(vr)
                res = c - i + dt / L * (vr + 2 * v_body(c) + R_coil * c)
                hi = np.where(res > 0, vr, hi); lo = np.where(res > 0, lo, vr)
            vr = 0.5 * (lo + hi)
            i_new = cur(vr)
            # with the coil current gone the rail bar floats: the capacitor can only empty into the 5KP30A
            vr = np.where(i_new > 0, vr, np.maximum(vr, vc - 1e-3))
            i_d = np.where(i_new > 0, (vr - vc) * g_d, 0.0)
            i_t = i_tvs(vr)
            u = vr + 2 * v_body(i_new)
        if step % keep == 0:
            for k, v in (("t", step * dt), ("i", i_new), ("u", u), ("vc", vc), ("i_d", i_d), ("i_t", i_t)):
                out[k].append(np.array(v, float, copy=True))
        i = i_new
        dv = i_d
        if v_lim is not None:
            # limiter across the capacitor, taken implicitly so the step stays stable
            for _ in range(3):
                vc_try = vc + dt / C_d * (i_d - i_limiter(vc, v_lim))
                vc_try = np.clip(vc_try, -1.25 * v_lim, 1.25 * v_lim)
                dv = i_d - i_limiter(0.5 * (vc + vc_try), v_lim)
        vc = vc + dt / C_d * dv
    return {k: np.array(v) for k, v in out.items()}


def numbers(r, I0, R_d):
    """The figures quoted in the report, one value per damper setting."""
    t, i = r["t"], r["i"]
    dt = t[1] - t[0]
    frac = np.abs(i) / abs(I0)

    def first_below(level):
        ok = frac <= level
        k = np.argmax(ok, axis=0)
        return np.where(ok.any(axis=0), t[k], np.nan)

    def stays_within(level):
        bad = frac > level
        last = bad.shape[0] - 1 - np.argmax(bad[::-1], axis=0)
        return np.where(last >= bad.shape[0] - 1, np.nan, t[np.minimum(last + 1, len(t) - 1)])

    rev = np.minimum(i.min(axis=0), 0.0)
    neg = i < -0.01 * abs(I0)                      # reversed by more than 1 % of the starting current
    R = np.broadcast_to(np.where(np.isfinite(np.asarray(R_d, float)), R_d, 0.0), i.shape[1:])
    p_d = r["i_d"] ** 2 * R
    v_t = np.interp(np.maximum(r["i_t"], 1e-9), _I, _V_TVS)
    return {
        "t10": first_below(0.10), "t2": stays_within(0.02),
        "rev_peak": rev, "rev_time": neg.sum(axis=0) * dt,
        "tvs_peak": r["i_t"].max(axis=0), "tvs_energy": (r["i_t"] * v_t).sum(axis=0) * dt,
        "res_energy": p_d.sum(axis=0) * dt, "res_peak_power": p_d.max(axis=0),
        "cap_peak": np.abs(r["vc"]).max(axis=0), "cap_end": r["vc"][-1],
        "coil_v_peak": np.abs(r["u"]).max(axis=0),
    }
