"""Plot the zero-setpoint snap traces written by the integration bench.

Run the bench with COIL_SNAP_TRACES set to a directory, e.g. (PowerShell)

    $env:COIL_SNAP_TRACES = "sim\\traces"; $env:COCOTB_TEST_FILTER = "snap_turns"
    python -m pytest sim/test_hdl.py -k coil_servo_top

then:  python sim/plot_snap_traces.py sim/traces out.png
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plotfit import check_overlaps, place_legend, settle_legends   # sim/plotfit.py

src = Path(sys.argv[1] if len(sys.argv) > 1 else "sim/traces")
out = sys.argv[2] if len(sys.argv) > 2 else "snap_sim.png"
T = 1.024                                       # us per PI tick
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "legend.frameon": False,
                     "axes.labelpad": 6})
# constrained layout; legends inside the panels on free space only (sim/plotfit.py)
fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex="col", layout="constrained")
cols = {"off": "#C0504D", "on": "#1F497D"}
for c, (name, title, xmax) in enumerate((
        ("bench", "Bench model: 2 A, one 45 µH test coil", 2000),
        ("mot", "MOT model: 80 A, 16 µH pair", 1600))):
    for s in ("off", "on"):
        d = json.load(open(src / f"snap_{name}_{s}.json"))
        k = d["k_off"]
        t = [(j - k) * T for j in range(len(d["i"]))]
        lab = "snap on (rev 5)" if s == "on" else "snap off (rev 4 behaviour)"
        axes[0, c].plot(t, d["i"], color=cols[s], lw=1.6, label=lab)
        axes[1, c].plot(t, d["out1"], color=cols[s], lw=1.2, label=lab)
    axes[0, c].axhline(0, color="k", lw=0.6)
    axes[0, c].axvline(0, color="k", lw=0.5, ls=":")
    axes[1, c].axvline(0, color="k", lw=0.5, ls=":")
    axes[0, c].set_title(title, fontsize=11, loc="left")
    axes[0, c].set_ylabel("coil current (A)")
    axes[1, c].set_ylabel("OUT1 command (counts)")
    axes[1, c].set_xlabel("time from setpoint → 0 (µs)")
    axes[1, c].set_xlim(-100, xmax)
    axes[0, c].grid(alpha=0.3)
    axes[1, c].grid(alpha=0.3)
fig.suptitle("Zero-setpoint snap, HDL in the loop (cocotb + Icarus, plant model)",
             fontsize=12)
for c in (0, 1):
    place_legend(axes[0, c])
settle_legends(fig)
check_overlaps(fig, out)
fig.savefig(out, dpi=110)
print("wrote", out)
