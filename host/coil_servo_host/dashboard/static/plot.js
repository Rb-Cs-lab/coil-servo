// Minimal canvas line plots for the dashboard: strip charts, captures,
// Bode plots, sparklines. No dependencies (the lab LAN may have no
// internet). Long series are reduced to a min/max pair per pixel column.

"use strict";

function niceStep(span, target) {
  const raw = span / Math.max(target, 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const r = raw / mag;
  return (r < 1.5 ? 1 : r < 3.5 ? 2 : r < 7.5 ? 5 : 10) * mag;
}

function linTicks(lo, hi, target) {
  if (!(hi > lo)) return [lo];
  const step = niceStep(hi - lo, target);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step)
    out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

function logTicks(lo, hi) {
  const out = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++)
    for (const m of [1, 2, 5]) {
      const v = m * Math.pow(10, e);
      if (v >= lo && v <= hi) out.push(v);
    }
  return out;
}

function fmtTick(v, step) {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e4 || a < 1e-3) return v.toExponential(0);
  const dec = Math.max(0, -Math.floor(Math.log10(step || a) + 1e-9));
  return v.toFixed(Math.min(dec, 4));
}

function fmtLog(v) {
  return v >= 1000 ? (v / 1000) + "k" : String(v);
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return v.trim() || fallback;
}

class Plot {
  // opts: {series: [{label, color, unit}], xlabel, ylabel, xlog, height,
  //        xfmt(v) for the hover readout, fixedX: [lo, hi]}
  constructor(container, opts) {
    this.opts = Object.assign({height: 220, xlog: false}, opts);
    this.wrap = document.createElement("div");
    this.wrap.className = "plot";
    this.canvas = document.createElement("canvas");
    this.canvas.style.height = this.opts.height + "px";
    this.legend = document.createElement("div");
    this.legend.className = "plot-legend";
    this.wrap.append(this.legend, this.canvas);
    container.append(this.wrap);
    this.x = [];
    this.ys = [];
    this.hover = null;
    this.canvas.addEventListener("mousemove", (e) => {
      const r = this.canvas.getBoundingClientRect();
      this.hover = e.clientX - r.left;
      this.draw();
    });
    this.canvas.addEventListener("mouseleave", () => {
      this.hover = null;
      this.draw();
    });
    new ResizeObserver(() => this.draw()).observe(this.canvas);
    this._legend(null);
  }

  setData(x, ys) {
    this.x = x;
    this.ys = ys;
    this.draw();
  }

  _legend(idx) {
    const o = this.opts;
    const parts = o.series.map((s, k) => {
      let val = "";
      if (idx !== null && this.ys[k] && this.ys[k][idx] != null) {
        const v = this.ys[k][idx];
        val = " " + (Math.abs(v) >= 100 ? v.toFixed(2) : v.toPrecision(4))
          + (s.unit ? " " + s.unit : "");
      }
      return `<span><i style="background:${s.color}"></i>${s.label}${val}</span>`;
    });
    if (idx !== null && this.x[idx] != null) {
      const xv = o.xfmt ? o.xfmt(this.x[idx]) : this.x[idx].toPrecision(4);
      parts.push(`<span class="plot-x">${o.xlabel}: ${xv}</span>`);
    }
    this.legend.innerHTML = parts.join("");
  }

  draw() {
    const o = this.opts, c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    if (!W || !H) return;
    if (c.width !== Math.round(W * dpr)) c.width = Math.round(W * dpr);
    if (c.height !== Math.round(H * dpr)) c.height = Math.round(H * dpr);
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const fg = cssVar("--muted", "#666"), grid = cssVar("--grid", "#ddd");
    g.font = "11px system-ui, sans-serif";

    const L = 58, R = 10, T = 8, B = 34;
    const pw = W - L - R, ph = H - T - B;
    const x = this.x;
    const n = x.length;

    // ranges
    let xlo, xhi;
    if (o.fixedX) [xlo, xhi] = o.fixedX;
    else if (n) { xlo = x[0]; xhi = x[n - 1]; }
    else { xlo = 0; xhi = 1; }
    if (o.xlog) { xlo = Math.max(xlo, 1e-9); if (xhi <= xlo) xhi = xlo * 10; }
    else if (xhi <= xlo) xhi = xlo + 1;
    let ylo = Infinity, yhi = -Infinity;
    for (const y of this.ys) for (let i = 0; i < y.length; i++) {
      const v = y[i];
      if (v == null || !isFinite(v)) continue;
      if (o.fixedX && (x[i] < xlo || x[i] > xhi)) continue;
      if (v < ylo) ylo = v;
      if (v > yhi) yhi = v;
    }
    if (!isFinite(ylo)) { ylo = 0; yhi = 1; }
    if (o.yMinSpan && yhi - ylo < o.yMinSpan) {
      const m = (yhi + ylo) / 2;
      ylo = m - o.yMinSpan / 2; yhi = m + o.yMinSpan / 2;
    }
    const pad = (yhi - ylo) * 0.08 || Math.abs(yhi) * 0.1 || 1;
    ylo -= pad; yhi += pad;

    const lx = (v) => o.xlog ? Math.log10(v) : v;
    const X = (v) => L + (lx(v) - lx(xlo)) / (lx(xhi) - lx(xlo)) * pw;
    const Y = (v) => T + (yhi - v) / (yhi - ylo) * ph;

    // grid + ticks
    g.strokeStyle = grid; g.fillStyle = fg; g.lineWidth = 1;
    const yt = linTicks(ylo, yhi, Math.max(2, ph / 40));
    const ystep = yt.length > 1 ? yt[1] - yt[0] : 1;
    g.textAlign = "right"; g.textBaseline = "middle";
    for (const v of yt) {
      const yy = Math.round(Y(v)) + 0.5;
      g.beginPath(); g.moveTo(L, yy); g.lineTo(L + pw, yy); g.stroke();
      g.fillText(fmtTick(v, ystep), L - 6, yy);
    }
    const xt = o.xlog ? logTicks(xlo, xhi) : linTicks(xlo, xhi, Math.max(2, pw / 80));
    const xstep = xt.length > 1 ? xt[1] - xt[0] : 1;
    g.textAlign = "center"; g.textBaseline = "top";
    for (const v of xt) {
      const xx = Math.round(X(v)) + 0.5;
      g.beginPath(); g.moveTo(xx, T); g.lineTo(xx, T + ph); g.stroke();
      g.fillText(o.xlog ? fmtLog(v) : fmtTick(v, xstep), xx, T + ph + 5);
    }
    g.fillText(o.xlabel || "", L + pw / 2, T + ph + 19);
    g.save();
    g.translate(12, T + ph / 2); g.rotate(-Math.PI / 2);
    g.textBaseline = "middle"; g.fillText(o.ylabel || "", 0, 0);
    g.restore();
    g.strokeStyle = fg;
    g.strokeRect(L + 0.5, T + 0.5, pw, ph);

    // series
    g.save();
    g.beginPath(); g.rect(L, T, pw, ph); g.clip();
    this.ys.forEach((y, k) => {
      const s = o.series[k];
      g.strokeStyle = s.color; g.lineWidth = s.width || 1.5;
      g.setLineDash(s.dash || []);
      g.beginPath();
      if (n > 2 * pw && !o.xlog) {
        // min/max per pixel column
        let col = -1, mn = 0, mx = 0, first = true;
        const flush = () => {
          if (col < 0) return;
          if (first) { g.moveTo(col, Y(mn)); first = false; } else g.lineTo(col, Y(mn));
          g.lineTo(col, Y(mx));
        };
        for (let i = 0; i < n; i++) {
          const v = y[i];
          if (v == null) continue;
          const cx = Math.round(X(x[i]));
          if (cx !== col) { flush(); col = cx; mn = mx = v; }
          else { if (v < mn) mn = v; if (v > mx) mx = v; }
        }
        flush();
      } else {
        let pen = false;
        for (let i = 0; i < n; i++) {
          const v = y[i];
          if (v == null || !isFinite(v)) { pen = false; continue; }
          if (pen) g.lineTo(X(x[i]), Y(v)); else g.moveTo(X(x[i]), Y(v));
          pen = true;
        }
        if (s.points) for (let i = 0; i < n; i++)
          if (y[i] != null) g.rect(X(x[i]) - 2, Y(y[i]) - 2, 4, 4);
      }
      g.stroke();
    });
    g.restore();

    // hover readout
    let idx = null;
    if (this.hover !== null && n && this.hover >= L && this.hover <= L + pw) {
      let best = Infinity;
      // binary search on monotonic x
      let lo = 0, hi = n - 1;
      const target = this.hover;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (X(x[mid]) < target) lo = mid; else hi = mid;
      }
      for (const i of [lo, hi]) {
        const d = Math.abs(X(x[i]) - target);
        if (d < best) { best = d; idx = i; }
      }
      const xx = Math.round(X(x[idx])) + 0.5;
      g.strokeStyle = fg; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(xx, T); g.lineTo(xx, T + ph); g.stroke();
      g.setLineDash([]);
    }
    this._legend(idx);
  }
}

function sparkline(canvas, ys, color) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  if (ys.length < 2) return;
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 0.5) { const m = (hi + lo) / 2; lo = m - 0.25; hi = m + 0.25; }
  g.strokeStyle = color; g.lineWidth = 1.5;
  g.beginPath();
  ys.forEach((v, i) => {
    const xx = i / (ys.length - 1) * (W - 2) + 1;
    const yy = 2 + (hi - v) / (hi - lo) * (H - 4);
    if (i) g.lineTo(xx, yy); else g.moveTo(xx, yy);
  });
  g.stroke();
}
