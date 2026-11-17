"""Draw a picture of the schematic layout without Ansys.

    ansys\\.venv\\Scripts\\python.exe ansys\\preview_layout.py

Writes ansys/results/schematic_bench.png and schematic_mot.png from the same
tables the Ansys project is built from (parts table in build_project.py,
positions and wires in layout.py). It is a preview for checking the layout
and for documents; the real schematic is the one in coil_servo.aedt.
"""
from __future__ import annotations

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import layout
from build_project import HERE, parts

OUT = os.path.join(HERE, "results")
BLUE, GREY, RED = "#1F497D", "#666666", "#C0504D"


def pins_of(kind, x, y, angle, pinnames):
    """{pin: (x, y, side)} in sheet coordinates."""
    if kind in layout.SYMBOLS:
        return {p: (x + px, y + py, side) for p, (px, py, side) in layout.SYMBOLS[kind][2].items()}
    if kind in ("VDC", "VPULSE"):
        return {"positive": (x, y + 2, "T"), "negative": (x, y - 2, "B")}
    if angle == 90:
        return {"1": (x, y + 2, "T"), "2": (x, y - 2, "B")}
    return {"1": (x - 2, y, "L"), "2": (x + 2, y, "R")}


def draw(config):
    P = parts(config)
    place, wires = layout.place(config), layout.wires(config)
    fig, ax = plt.subplots(figsize=(26, 15))
    pinloc, pinnet, pinside = {}, {}, {}
    for _, name, kind, pins, props in P:
        x, y, angle = place[name]
        pl = pins_of(kind, x, y, angle, pins)
        for pn, net in pins.items():
            pinloc[f"{name}.{pn}"] = pl[pn][:2]
            pinside[f"{name}.{pn}"] = pl[pn][2]
            pinnet[f"{name}.{pn}"] = net
        if kind in layout.SYMBOLS:
            w, h, sp = layout.SYMBOLS[kind]
            ax.add_patch(Rectangle((x, y), w, h, fill=False, ec=BLUE, lw=1.4))
            ax.text(x + w / 2, y + h / 2, name, ha="center", va="center", fontsize=8, color=BLUE, weight="bold")
            for pn, (px, py, side) in sp.items():
                ex, ey = {"L": (x, y + py), "R": (x + w, y + py), "T": (x + px, y + h), "B": (x + px, y)}[side]
                ax.plot([x + px, ex], [y + py, ey], color=BLUE, lw=1)
                dx, dy, ha, va = {"L": (0.3, 0, "left", "center"), "R": (-0.3, 0, "right", "center"),
                                  "T": (0, -0.3, "center", "top"), "B": (0, 0.3, "center", "bottom")}[side]
                ax.text(ex + dx, ey + dy, pn, fontsize=5.5, ha=ha, va=va, color=GREY)
        elif kind in ("VDC", "VPULSE"):
            ax.add_patch(plt.Circle((x, y), 1.5, fill=False, ec=BLUE, lw=1.2))
            ax.plot([x, x], [y + 1.5, y + 2], color=BLUE, lw=1); ax.plot([x, x], [y - 2, y - 1.5], color=BLUE, lw=1)
            ax.text(x + 1.8, y, name, fontsize=6.5, va="center", color=BLUE)
        else:
            if angle == 90:
                ax.add_patch(Rectangle((x - 0.6, y - 1.3), 1.2, 2.6, fill=False, ec=BLUE, lw=1.2))
                ax.plot([x, x], [y + 1.3, y + 2], color=BLUE, lw=1); ax.plot([x, x], [y - 2, y - 1.3], color=BLUE, lw=1)
                ax.text(x + 0.9, y, f"{name}\n{props.get(kind, '')}", fontsize=6, va="center", color=BLUE)
            else:
                ax.add_patch(Rectangle((x - 1.3, y - 0.6), 2.6, 1.2, fill=False, ec=BLUE, lw=1.2))
                ax.plot([x - 2, x - 1.3], [y, y], color=BLUE, lw=1); ax.plot([x + 1.3, x + 2], [y, y], color=BLUE, lw=1)
                ax.text(x, y + 0.8, f"{name} {props.get(kind, '')}", fontsize=6, ha="center", va="bottom", color=BLUE)
    wired = set()
    for net, paths in wires.items():
        for path in paths:
            pts = []
            for tok in path:
                if isinstance(tok, str):
                    wired.add(tok); pt = pinloc[tok]
                elif tok[0] == "x":
                    pt = (tok[1], pts[-1][1])
                elif tok[0] == "y":
                    pt = (pts[-1][0], tok[1])
                else:
                    pt = tuple(tok)
                pts.append(pt)
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color="k", lw=1.3, solid_capstyle="round")
            for end, tok in ((pts[0], path[0]), (pts[-1], path[-1])):
                if not isinstance(tok, str):
                    ax.plot(*end, "o", color="k", ms=3.5)
        first = paths[0]
        # net name next to the middle of its first wire
    for key, net in pinnet.items():
        if key in wired:
            continue
        x, y = pinloc[key]
        if net == "0":
            ax.plot([x, x], [y, y - 0.8], color="k", lw=1)
            for i, hw in enumerate((0.8, 0.5, 0.2)):
                ax.plot([x - hw, x + hw], [y - 0.8 - 0.3 * i] * 2, color="k", lw=1)
        else:
            dx, dy, ha, va, rot = {"L": (-0.3, 0, "right", "center", 0), "R": (0.3, 0, "left", "center", 0),
                                   "T": (0, 0.3, "center", "bottom", 90), "B": (0, -0.3, "center", "top", 90)}[pinside[key]]
            ax.text(x + dx, y + dy, net, fontsize=6, ha=ha, va=va, rotation=rot, color=RED,
                    bbox=dict(boxstyle="round,pad=0.15", fc="#FDECEA", ec=RED, lw=0.5))
    for x, y, text in layout.texts(config):
        ax.text(x, y, text, fontsize=8.5, color="#333333", style="italic")
    ax.set_aspect("equal"); ax.axis("off"); ax.autoscale_view()
    ax.set_title(f"coil servo power stage, design '{config}'  (black = wires, red tags = net labels, pins with the same tag are connected)", fontsize=11)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"schematic_{config}.png")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    for c in (sys.argv[1:] or ["bench", "mot", "bench_ft", "mot_ft"]):
        print("wrote", draw(c))
