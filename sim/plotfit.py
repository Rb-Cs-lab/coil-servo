"""Place legends inside the plot area without covering data or other text.

place_legend(ax) tries the standard positions inside the axes and keeps the first one whose box
touches no data line, marker, error bar or filled area and no other text in the axes. If none is
free it adds headroom above the data (up to +60 % of the y-range) and tries again; only then does
the legend go outside the axes. check_overlaps(fig) reports any text box that still touches data or
another text box in a finished figure (run it after the last layout change, just before saving).
"""
import numpy as np
from matplotlib.collections import LineCollection, PathCollection, PolyCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.text import Text

LOCS = ("upper right", "upper left", "lower right", "lower left", "center right", "center left",
        "upper center", "lower center", "center")
PAD = 8            # px of clearance around a text box


def _dense(xy, step=1.5):
    """Points along a polyline in display coordinates, at most `step` px apart."""
    xy = xy[np.all(np.isfinite(xy), axis=1)]
    if len(xy) < 2:
        return xy
    seg = np.diff(xy, axis=0)
    n = np.maximum(1, np.ceil(np.hypot(seg[:, 0], seg[:, 1]) / step).astype(int))
    if n.max() == 1:
        return xy
    parts = [xy[i] + np.linspace(0, 1, k, endpoint=False)[:, None] * seg[i] for i, k in enumerate(n)]
    return np.vstack(parts + [xy[-1:]])


def _data_geometry(ax, exclude=()):
    """(points in display px, filled paths in display px) of the data drawn in ax."""
    pts, fills = [], []
    for ln in ax.get_lines():
        if ln in exclude or not ln.get_visible():
            continue
        xy = ln.get_transform().transform(np.asarray(ln.get_xydata(), float))
        if ln.get_linestyle() not in ("None", "", " ") and len(xy) > 1:
            pts.append(_dense(xy))
        elif ln.get_marker() not in (None, "None", "", " "):
            pts.append(xy)
        if ln.get_marker() not in (None, "None", "", " "):
            r = ln.get_markersize() * ax.figure.dpi / 72 / 2
            pts.append(np.vstack([xy + [dx, dy] for dx in (-r, r) for dy in (-r, r)]))
    for c in ax.collections:
        if not c.get_visible():
            continue
        if isinstance(c, LineCollection):
            for seg in c.get_segments():
                pts.append(_dense(c.get_transform().transform(np.asarray(seg, float))))
        elif isinstance(c, PolyCollection) and c.get_label() != "_bg":
            for p in c.get_paths():
                fills.append(p.transformed(c.get_transform()))
        elif isinstance(c, PathCollection):
            pts.append(c.get_offset_transform().transform(c.get_offsets()))
    for patch in ax.patches:              # shaded bands (axhspan) and other filled shapes
        if patch.get_visible() and patch.get_label() != "_bg":
            fills.append(patch.get_path().transformed(patch.get_transform()))
    pts = np.vstack(pts) if pts else np.zeros((0, 2))
    return pts, fills


def _hits(bb, pts, fills):
    x0, y0, x1, y1 = bb.x0 - PAD, bb.y0 - PAD, bb.x1 + PAD, bb.y1 + PAD
    if len(pts) and np.any((pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1)):
        return True
    if fills:
        gx, gy = np.meshgrid(np.linspace(x0, x1, 9), np.linspace(y0, y1, 5))
        probe = np.column_stack([gx.ravel(), gy.ravel()])
        if any(p.contains_points(probe).any() for p in fills):
            return True
    return False


def _texts(ax, exclude=()):
    out = [t for t in ax.texts if t.get_visible() and t.get_text() and t not in exclude]
    return out


def _inside(bb, ax):
    a = ax.get_window_extent()
    return bb.x0 >= a.x0 - 1 and bb.x1 <= a.x1 + 1 and bb.y0 >= a.y0 - 1 and bb.y1 <= a.y1 + 1


def place_legend(ax, handles=None, labels=None, fontsize=8.5, ncols=(1, 2), headroom=(0, .15, .3, .45, .6),
                 **kw):
    """Put the legend inside ax where it covers nothing; returns the Legend."""
    fig = ax.figure
    if handles is None:
        handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return None
    y0, y1 = ax.get_ylim()
    span = y1 - y0
    for h in headroom:
        ax.set_ylim(y0, y1 + h * span)
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        pts, fills = _data_geometry(ax)
        others = [t.get_window_extent(r) for t in _texts(ax)]
        for nc in ncols:
            for loc in LOCS:
                leg = ax.legend(handles, labels, loc=loc, ncol=nc, fontsize=fontsize, **kw)
                bb = leg.get_window_extent(r)
                if _inside(bb, ax) and not _hits(bb, pts, fills) and not any(bb.overlaps(o) for o in others):
                    return leg
                leg.remove()
    ax.set_ylim(y0, y1)
    return ax.legend(handles, labels, loc="upper left", bbox_to_anchor=(1.01, 1.0), borderaxespad=0,
                     fontsize=fontsize, **kw)


def legend_covers_data(ax):
    """True when ax's legend sits inside the axes on top of data or another text."""
    leg = ax.get_legend()
    if leg is None:
        return False
    fig = ax.figure
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    bb = leg.get_window_extent(r)
    if not _inside(bb, ax):
        return False
    pts, fills = _data_geometry(ax)
    return _hits(bb, pts, fills) or any(bb.overlaps(t.get_window_extent(r)) for t in _texts(ax))


def settle_legends(fig, rounds=3):
    """Placing one legend can shift the constrained layout and move another panel's data under its
    legend: re-place any legend that now covers something, until none does."""
    for _ in range(rounds):
        bad = [ax for ax in fig.axes if legend_covers_data(ax)]
        if not bad:
            return
        for ax in bad:
            leg = ax.get_legend()
            hh, ll = leg.legend_handles, [t.get_text() for t in leg.get_texts()]
            nc = leg._ncols
            leg.remove()
            place_legend(ax, hh, ll, ncols=(nc, 1, 2))


def check_overlaps(fig, name=""):
    """List text boxes (legends, axes texts, titles, axis labels, tick labels) that touch data or each
    other. Returns the list of problems; prints them."""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    problems = []
    boxes = []
    for ax in fig.axes:
        pts, fills = _data_geometry(ax)
        leg = ax.get_legend()
        items = ([("legend", leg)] if leg else []) + [(f"text '{t.get_text()[:30]}'", t) for t in _texts(ax)]
        for what, art in items:
            bb = art.get_window_extent(r)
            if _inside(bb, ax) and _hits(bb, pts, fills):
                problems.append(f"{name}: {what} covers data")
        for what, art in items + [("title", ax.title), ("xlabel", ax.xaxis.label), ("ylabel", ax.yaxis.label)]:
            if isinstance(art, (Text, Legend)) and art.get_visible() and (not isinstance(art, Text) or art.get_text()):
                boxes.append((what, art.get_window_extent(r)))
        for axis in (ax.xaxis, ax.yaxis):            # only tick labels that are actually drawn
            lo, hi = sorted(axis.get_view_interval())
            for tk in axis.get_major_ticks():
                tl = tk.label1
                if tl.get_visible() and tl.get_text() and lo <= tk.get_loc() <= hi:
                    boxes.append((f"tick '{tl.get_text()}'", tl.get_window_extent(r)))
    for lg in fig.legends:
        boxes.append(("figure legend", lg.get_window_extent(r)))
    if fig._suptitle is not None:
        boxes.append(("suptitle", fig._suptitle.get_window_extent(r)))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (a, ba), (b, bb) = boxes[i], boxes[j]
            if ba.width > 0 and bb.width > 0 and ba.overlaps(bb):
                problems.append(f"{name}: {a} overlaps {b}")
    for p in problems:
        print("  OVERLAP", p)
    return problems
