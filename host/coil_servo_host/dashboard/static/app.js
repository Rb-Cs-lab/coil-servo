// Coil servo dashboard: overview of all channels, per-channel detail with
// strip charts, flags, configuration, captures and (on the lab PC only)
// controls. State arrives as server-sent events from /api/stream.

"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmtA = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2);

const SPANS = [[10, "10 s"], [30, "30 s"], [60, "1 min"], [120, "2 min"],
  [300, "5 min"], [600, "10 min"]];
const FLAG_INFO = {
  armed: ["arm (DIO4)", "the arm input is high", false],
  bridge_en: ["bridge enabled", "H-bridge closed: current can flow", false],
  polarity: ["polarity", "bridge polarity (0 = normal)", false],
  tripped: ["tripped", "over-current trip (TRIP or TRIPPED)", true],
  out_sat: ["OUT1 at clamp", "the PI asks for more than the command clamp", true],
  u_floor: ["OUT1 at floor", "PI output on its lower bound: current can only decay", false],
  int_railed: ["integrator railed", "the integrator hit its limit", true],
  sp_sign_mismatch: ["sign mismatch", "setpoint sign disagrees with the bridge polarity", true],
  timeout_hold: ["timeout hold", "a flip never reached the zero window", true],
  fault: ["fault (DIO5)", "fault input high (not wired in the MVP)", true],
};

const S = {
  devices: {},          // name -> {info: describe(), state: snapshot}
  who: null,
  spark: {},            // name -> [i_amps], last ~60 s
  route: null,
  detail: null,         // per-view state of the open channel
};

// ---------------------------------------------------------------- helpers
function toast(msg, isError) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), isError ? 9000 : 4000);
}

function confirmDialog(title, bodyHtml, okLabel) {
  const d = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-body").innerHTML = bodyHtml;
  $("#confirm-ok").textContent = okLabel || "Confirm";
  return new Promise((resolve) => {
    d.addEventListener("close", () => resolve(d.returnValue === "ok"),
      {once: true});
    d.returnValue = "cancel";
    d.showModal();
  });
}

async function post(name, action, params = {}) {
  try {
    const r = await fetch(`/api/devices/${name}/actions/${action}`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Coil-Servo": "1"},
      body: JSON.stringify(params),
    });
    const j = await r.json();
    if (!r.ok) { toast(j.error || r.statusText, true); return null; }
    toast(j.message);
    return j;
  } catch (e) {
    toast("request failed: " + e, true);
    return null;
  }
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

function stateClass(st) {
  if (st.connection !== "online") return "offline";
  return st.state || "unknown";
}

function stateText(st) {
  if (st.connection === "online") return st.state || "…";
  return st.connection === "no profile" ? "NO PROFILE" : st.connection.toUpperCase();
}

function badges(st) {
  let h = "";
  if (st.bench) h += ' <span class="badge bench">BENCH</span>';
  if (st.simulated) h += ' <span class="badge sim">SIM</span>';
  return h;
}

function alarmsHtml(st) {
  let h = "";
  for (const a of st.alarms || []) h += `<div class="alarm-item">${esc(a)}</div>`;
  for (const w of st.warnings || []) h += `<div class="warn-item">${esc(w)}</div>`;
  if (st.connection === "offline")
    h += `<div class="warn-item">offline: ${esc(st.error || "")}</div>`;
  return h;
}

// ---------------------------------------------------------------- stream
function startStream() {
  const pill = $("#stream-state");
  const es = new EventSource("/api/stream");
  es.onopen = () => { pill.textContent = "live"; pill.className = "pill ok"; };
  es.onerror = () => {
    pill.textContent = "dashboard server unreachable — retrying";
    pill.className = "pill bad";
  };
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    for (const [name, st] of Object.entries(data)) {
      if (!S.devices[name]) continue;
      S.devices[name].state = st;
      if (st.t != null && st.i_amps != null) {
        const sp = S.spark[name] || (S.spark[name] = []);
        if (!sp.length || sp[sp.length - 1].t !== st.t) {
          sp.push({t: st.t, i: st.i_amps});
          while (sp.length && sp[0].t < st.t - 60) sp.shift();
        }
      }
    }
    if (S.route.view === "overview") updateOverview();
    else if (S.route.view === "detail") updateDetail();
  };
}

// ---------------------------------------------------------------- overview
function renderOverview() {
  const v = $("#view");
  v.innerHTML = `<div class="cards" id="cards"></div>
    <p class="muted small">Click a channel for its live charts, flags,
    configuration, captures and controls. Viewing works from any PC on the
    lab LAN; control only on the lab PC itself.</p>`;
  const cards = $("#cards");
  for (const name of Object.keys(S.devices)) {
    const c = document.createElement("div");
    c.className = "card";
    c.id = "card-" + name;
    c.onclick = () => (location.hash = "#/ch/" + name);
    c.innerHTML = `
      <div class="head"><span class="dot"></span>
        <span class="name">${esc(name)}</span><span class="badges"></span>
        <span class="host"></span></div>
      <div class="state"></div>
      <div class="current"><span class="val">—</span> <span class="unit">A</span>
        <span class="small muted sp"></span></div>
      <div class="small supply"></div>
      <canvas class="spark"></canvas>
      <div class="alarms"></div>`;
    cards.append(c);
  }
  updateOverview();
}

function updateOverview() {
  for (const [name, d] of Object.entries(S.devices)) {
    const c = $("#card-" + name);
    const st = d.state;
    if (!c || !st) continue;
    c.classList.toggle("alarm", (st.alarms || []).length > 0);
    $(".dot", c).className = "dot " + (st.connection === "online" ? "online" : "offline");
    $(".badges", c).innerHTML = badges(st);
    $(".host", c).textContent = st.host || "";
    const s = $(".state", c);
    s.textContent = stateText(st);
    s.className = "state " + stateClass(st);
    $(".val", c).textContent = st.connection === "online" ? fmtA(st.i_amps) : "—";
    $(".sp", c).textContent = st.connection === "online" && st.sp_amps != null
      ? `setpoint ${fmtA(st.sp_amps)} A` : "";
    $(".supply", c).textContent = st.i_supply_max != null
      ? `bench supply limit ${st.i_supply_max} A · rated ${st.i_rated} A` : "";
    $(".alarms", c).innerHTML = alarmsHtml(st);
    const ys = (S.spark[name] || []).map((p) => p.i);
    const step = Math.max(1, Math.floor(ys.length / 150));
    sparkline($(".spark", c), ys.filter((_, i) => i % step === 0),
      cssVar("--c-i", "#1f5fbf"));
  }
}

// ---------------------------------------------------------------- detail
function actionSpec(name, action) {
  return (S.devices[name].info.actions || []).find((a) => a.name === action);
}

function renderDetail(name) {
  const d = S.devices[name];
  if (!d) { location.hash = "#/"; return; }
  const st = d.state;
  const sim = st.simulated;
  $("#view").innerHTML = `
  <div class="row"><a href="#/">&larr; all channels</a></div>
  <div class="panel" style="margin:10px 0 14px">
    <div class="bigline">
      <span><span class="name" style="font-size:20px;font-weight:700">${esc(name)}</span>
        <span id="d-badges"></span>
        <span class="muted small" id="d-host"></span></span>
      <span class="state" id="d-state"></span>
      <span class="current"><span id="d-i">—</span> <span class="unit">A</span></span>
      <span class="muted">setpoint <b id="d-sp">—</b> A</span>
      <span class="muted">OUT1 <b id="d-u">—</b></span>
      <span class="muted" id="d-supply"></span>
      <span class="spacer" style="flex:1"></span>
      <button class="btn stop" id="b-stop" title="servo_enable = 0: graceful ramp to zero">STOP</button>
    </div>
    <div class="alarms" id="d-alarms" style="margin-top:8px"></div>
    <div id="d-pending" style="margin-top:8px"></div>
    <div id="d-notice" style="margin-top:8px"></div>
  </div>
  <div class="grid">
    <div class="stack">
      <div class="panel">
        <div class="row"><h2 style="margin:0">Live</h2>
          <span class="spacer" style="flex:1"></span>
          <label class="small muted">time span
          <select id="span">${SPANS.map(([s, l]) =>
            `<option value="${s}">${l}</option>`).join("")}</select></label></div>
        <div id="plot-i"></div>
        <div id="plot-u"></div>
      </div>
      <div class="panel">
        <div class="row"><h2 style="margin:0">Waveform capture</h2>
          <span class="spacer" style="flex:1"></span>
          <button class="btn primary" id="b-cap">Capture now</button>
          <label class="small">repeat every
            <input type="number" id="cap-n" min="0" step="1" value="0"> s</label>
          <button class="btn" id="b-auto">Set</button></div>
        <p class="small muted">16.8 ms of measured current and setpoint at
          976.6 kS/s (1.024 µs per point), started by this PC — not
          synchronised to experimental shots. Each capture is saved as CSV.</p>
        <div id="cap-info" class="small muted"></div>
        <div id="plot-cap"></div>
      </div>
      <div class="panel" id="sweep-panel" hidden>
        <h2>Swept-sine transfer function</h2>
        <div id="sweep-info" class="small muted"></div>
        <div id="plot-mag"></div><div id="plot-ph"></div>
      </div>
    </div>
    <div class="stack">
      <div class="panel"><h2>Control</h2><div id="ctl"></div></div>
      <div class="panel"><h2>Flags</h2><div class="flags" id="flags"></div>
        <div class="kv small" id="mode" style="margin-top:10px"></div></div>
      <div class="panel"><h2>Configuration</h2><div id="cfg"></div></div>
    </div>
  </div>`;

  const D = S.detail = {name, span: 60, hist: null, lastT: null,
    capSeq: null, dirty: true, plots: {}};
  $("#span").value = "60";
  $("#span").onchange = (e) => { D.span = +e.target.value; loadHistory(); };
  const xfmtS = (v) => v.toFixed(1) + " s";
  D.plots.i = new Plot($("#plot-i"), {xlabel: "time relative to now (s)",
    ylabel: "current (A)", xfmt: xfmtS, yMinSpan: 0.5, series: [
      {label: "measured", color: cssVar("--c-i"), unit: "A"},
      {label: "setpoint (after ramp)", color: cssVar("--c-sp"), unit: "A"}]});
  D.plots.u = new Plot($("#plot-u"), {xlabel: "time relative to now (s)",
    ylabel: "OUT1 (V)", height: 150, xfmt: xfmtS, yMinSpan: 0.01, series: [
      {label: "OUT1 command", color: cssVar("--c-u"), unit: "V"}]});
  D.plots.cap = new Plot($("#plot-cap"), {xlabel: "time (ms)",
    ylabel: "current (A)", xfmt: (v) => v.toFixed(3) + " ms", yMinSpan: 0.2,
    series: [{label: "measured", color: cssVar("--c-i"), unit: "A"},
      {label: "setpoint", color: cssVar("--c-sp"), unit: "A"}]});

  $("#b-stop").onclick = () => post(name, "stop");
  $("#b-cap").onclick = () => post(name, "capture");
  $("#b-auto").onclick = () => post(name, "auto_capture",
    {interval_s: +$("#cap-n").value});

  renderControls(name, sim);
  loadHistory();
  loadInfo();
  D.infoTimer = setInterval(loadInfo, 5000);
  D.drawTimer = setInterval(drawDetail, 200);
  updateDetail();
}

function renderControls(name, sim) {
  const st = S.devices[name].state;
  const ctl = $("#ctl");
  if (!S.who.control) {
    ctl.innerHTML = `<p class="locked">Viewing only. Control is accepted only
      on the lab PC itself (open <span class="mono">http://localhost:${location.port || 80}</span>
      there). You are ${esc(S.who.client)}.</p>`;
    return;
  }
  const rated = st.i_rated;
  ctl.innerHTML = `
    <p class="small muted">Look only while an experimental sequence is running.
      Every action that can drive current asks for confirmation; Stop never
      does.</p>
    <div id="ctl-block" class="warn-item" hidden></div>
    <fieldset><legend>Configuration</legend>
      <button class="btn" id="b-apply">Apply profile [${esc(name)}]</button></fieldset>
    <fieldset><legend>Run</legend>
      <div class="row"><label>setpoint source
        <select id="src"><option value="register">this page (starts at 0 A)</option>
        <option value="in2">IN2 — control computer</option></select></label>
        <button class="btn" id="b-enable">Enable</button></div>
      <div class="row" style="margin-top:8px"><label>current
        <input type="number" id="amps" step="0.1" min="${-rated}" max="${rated}" value="0"> A</label>
        <button class="btn" id="b-sp">Set</button>
        <span class="small muted">max ${rated} A</span></div></fieldset>
    <fieldset><legend>Acknowledge</legend>
      <button class="btn" id="b-trip">trip_ack</button>
      <button class="btn" id="b-flip">flip_fault_ack</button>
      <div class="small muted">Acknowledges firmware states only. The
      hardware latch on the logic board is reset by hand (disarm first).</div></fieldset>
    <fieldset><legend>Measurements — FIRST RUNS: DUMMY RESISTIVE LOAD ONLY</legend>
      <div class="row"><label>step to <input type="number" id="step-a" step="0.5" value="${Math.min(5, rated)}"> A</label>
        <label class="small">or OUT1 <input type="number" id="step-c" step="1" placeholder="counts"> counts (open loop)</label>
        <label><input type="checkbox" id="step-ol"> open loop</label>
        <button class="btn" id="b-step">Step response</button></div>
      <div class="row" style="margin-top:8px">
        <button class="btn" id="b-sweep">Swept-sine (needs function generator on IN2)</button></div>
      <div class="small muted">The sweep drives OUT1 from IN2 in open loop:
        connect a function generator to IN2, offset above the pass-bank dead
        zone (~4.4 V before the ÷10), small amplitude; then step it through
        the frequencies. STOP ends the sweep.</div></fieldset>
    ${sim ? `<fieldset><legend>Simulation only</legend>
      <button class="btn" id="b-arm-up">raise arm (DIO4)</button>
      <button class="btn" id="b-arm-dn">drop arm</button>
      <button class="btn" id="b-simtrip">inject trip</button></fieldset>` : ""}`;

  const summary = () => {
    const rows = (S.detail.info?.summary || []).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
    return rows ? `<table>${rows}</table>` : "";
  };
  $("#b-apply").onclick = async () => {
    const info = S.detail.info || {};
    if (await confirmDialog(`Apply profile [${name}]?`,
      `<p>Writes the configuration of <b>[${esc(name)}]</b> from
       <span class="mono">${esc(info.config_file)}</span> to the board at
       <b>${esc(st.host)}</b>. Only possible in IDLE.</p>
       <p>Board now: ${esc(S.devices[name].state.profile_text || "unknown")}</p>
       ${S.devices[name].state.bench ? "<p><b>BENCH profile: only with the current-limited bench supply connected, never with the SE-600-5.</b></p>" : ""}
       ${summary()}`,
      "Apply profile")) post(name, "apply_profile", {confirm: true});
  };
  $("#b-enable").onclick = async () => {
    const src = $("#src").value;
    const what = src === "in2"
      ? "<b>The analog setpoint on IN2 (the control computer) takes over as soon as RUN is reached.</b>"
      : "The setpoint register starts at <b>0 A</b>; set the current afterwards.";
    if (await confirmDialog(`Enable ${name}?`,
      `<p>Sets servo_enable = 1. The servo stays in IDLE until the arm line
       (DIO4) gets a <b>rising edge</b>; then the bridge closes and current
       can flow.</p><p>${what}</p>`, "Enable servo"))
      post(name, "enable", {source: src, confirm: true});
  };
  $("#b-sp").onclick = async () => {
    const a = parseFloat($("#amps").value);
    if (!isFinite(a)) return toast("enter a current in amps", true);
    if (Math.abs(a) > rated) return toast(`refused: above the rated ${rated} A`, true);
    const now = S.devices[name].state.i_amps;
    if (await confirmDialog(`Set ${name} to ${a} A?`,
      `<p>Now ${fmtA(now)} A → <b>${a} A</b>. The firmware ramps the
       setpoint (see "setpoint ramps" in the configuration).</p>`,
      `Set ${a} A`)) post(name, "setpoint", {amps: a, confirm: true});
  };
  $("#b-trip").onclick = async () => {
    if (await confirmDialog("Acknowledge the over-current trip?",
      `<p>Have you found out <b>why</b> it tripped (setpoint too high?
       loop oscillating? pass bank?)</p><p>TRIPPED → IDLE. Running again
       needs Enable and a fresh arm edge.</p>`, "Acknowledge trip"))
      post(name, "trip_ack", {confirm: true});
  };
  $("#b-flip").onclick = async () => {
    if (await confirmDialog("Acknowledge the flip timeout?",
      `<p>TIMEOUT_HOLD → RUN: the servo resumes following the setpoint.</p>`,
      "Acknowledge")) post(name, "flip_fault_ack", {confirm: true});
  };
  $("#b-step").onclick = async () => {
    const a = parseFloat($("#step-a").value), ol = $("#step-ol").checked;
    const cRaw = $("#step-c").value.trim();
    const counts = cRaw === "" ? null : parseInt(cRaw, 10);
    if (counts !== null && !ol)
      return toast("OUT1 counts are an open-loop command: tick open loop", true);
    if (counts === null && !isFinite(a)) return toast("enter a step size in amps", true);
    if (counts === null && Math.abs(a) > rated)
      return toast(`refused: above the rated ${rated} A`, true);
    const target = counts !== null ? `OUT1 ${counts} counts` : `${a} A`;
    if (await confirmDialog(`Step response to ${target} (${ol ? "open" : "closed"} loop)?`,
      `<p><b>First runs: dummy resistive load only.</b></p>
       <p>Applies the profile, enables the servo with the setpoint at 0,
       waits for you to raise arm (DIO4), steps to ${target} 2 ms into a
       16.8 ms capture, then ramps back to zero and disables.</p>
       ${ol && counts === null ? "<p>Open loop: OUT1 gets the command the model predicts for that current (PROVISIONAL).</p>" : ""}
       ${counts !== null ? "<p>Open loop: OUT1 gets exactly this command, bypassing the model.</p>" : ""}`,
      "Run step"))
      post(name, "step", counts !== null
        ? {counts, open_loop: true, confirm: true}
        : {amps: a, open_loop: ol, confirm: true});
  };
  $("#b-sweep").onclick = async () => {
    if (await confirmDialog("Start a swept-sine measurement?",
      `<p><b>First runs: dummy resistive load only.</b></p>
       <p>Open loop: <b>IN2 drives OUT1 directly</b>. A function generator
       must be on IN2 with its offset above the dead zone and a small
       amplitude. Applies the profile, enables, waits for arm, then records
       a point each time a new frequency is seen. STOP ends it.</p>`,
      "Start sweep")) post(name, "sweep", {confirm: true});
  };
  if (sim) {
    $("#b-arm-up").onclick = () => post(name, "sim_arm", {level: true});
    $("#b-arm-dn").onclick = () => post(name, "sim_arm", {level: false});
    $("#b-simtrip").onclick = () => post(name, "sim_trip");
  }
}

async function loadHistory() {
  const D = S.detail;
  try {
    const h = await getJSON(`/api/devices/${D.name}/history?span=${D.span}`);
    D.hist = h;
    D.lastT = h.t.length ? h.t[h.t.length - 1] : null;
    D.dirty = true;
  } catch (e) { /* offline: keep going */ }
}

async function loadInfo() {
  const D = S.detail;
  if (!D) return;
  try { D.info = await getJSON(`/api/devices/${D.name}/info`); }
  catch (e) { return; }
  const info = D.info;
  let h = "";
  if (info.profile_error)
    h += `<div class="alarm-item">profile error: ${esc(info.profile_error)}</div>`;
  h += `<p class="small muted">Profile <b>[${esc(info.name)}]</b>${info.bench
    ? ' <span class="badge bench">BENCH</span>' : ""} from
    <span class="mono">${esc(info.config_file)}</span></p>`;
  if (info.profile_text)
    h += `<div class="${info.profile_ok ? "notice" : "alarm-item"}">${esc(info.profile_text)}</div>`;
  if (info.header) h += `<p><b>${esc(info.header)}</b></p>`;
  if (info.summary.length)
    h += "<table>" + info.summary.map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("") + "</table>";
  if (info.compare.length) {
    const bad = info.compare.filter((r) => !r.match).length;
    h += `<h2 style="margin-top:14px">Board read-back vs profile</h2>
      <p class="small ${bad ? "" : "muted"}">${bad
        ? `<b style="color:var(--bad)">${bad} field(s) differ</b> — apply the profile (or another tool changed them)`
        : "all profile fields match"} · read ${info.readback_age_s} s ago
        ${info.map_ok === false ? '<br><b style="color:var(--bad)">old bitstream: this read-back uses the new layout and is not meaningful</b>' : ""}</p>
      <table><tr><th>field</th><th class="num">profile</th><th class="num">board</th><th></th></tr>`
      + info.compare.map((r) => `<tr class="${r.match ? "" : "mismatch"}">
        <td class="mono">${esc(r.field)}</td><td class="num">${r.profile}</td>
        <td class="num">${r.board}</td><td>${r.match ? "✓" : "✗"}</td></tr>`).join("")
      + "</table>";
    h += `<h2 style="margin-top:14px">Live fields</h2><table>` +
      Object.entries(info.live).map(([k, v]) =>
        `<tr><td class="mono">${esc(k)}</td><td class="num">${v}</td></tr>`).join("")
      + "</table>";
  }
  $("#cfg").innerHTML = h;
}

async function loadCapture(name) {
  try {
    const cap = await getJSON(`/api/devices/${name}/capture`);
    const D = S.detail;
    if (!D || D.name !== name) return;
    const x = cap.i_amps.map((_, k) => k * cap.dt_s * 1e3);
    D.plots.cap.setData(x, [cap.i_amps, cap.sp_amps]);
    const when = new Date(cap.t * 1000).toLocaleTimeString();
    let meta = "";
    if (cap.kind === "step")
      meta = ` — step to ${cap.meta.amps != null ? cap.meta.amps + " A"
        : "OUT1 " + cap.meta.command_counts + " counts"}, ${cap.meta.open_loop
        ? "open loop (the setpoint trace is the OUT1 command in sensor-scale amps)"
        : "closed loop"}`;
    $("#cap-info").innerHTML = `${esc(cap.kind)} at ${esc(when)}${esc(meta)}
      · ${cap.n} points · saved to <span class="mono">${esc(cap.file || "(not saved)")}</span>`;
  } catch (e) { /* none yet */ }
}

function updateDetail() {
  const D = S.detail;
  if (!D) return;
  const name = D.name, st = S.devices[name].state;
  $("#d-badges").innerHTML = badges(st);
  $("#d-host").textContent = st.host || "";
  const s = $("#d-state");
  s.textContent = stateText(st);
  s.className = "state " + stateClass(st);
  const on = st.connection === "online";
  $("#d-i").textContent = on ? fmtA(st.i_amps) : "—";
  $("#d-sp").textContent = on ? fmtA(st.sp_amps) : "—";
  $("#d-supply").innerHTML = st.i_supply_max != null
    ? `bench supply limit <b>${st.i_supply_max} A</b>` : "";
  $("#d-u").textContent = on ? `${st.out1_volts.toFixed(4)} V (${st.u14} counts)` : "—";
  $("#d-alarms").innerHTML = alarmsHtml(st);

  let p = "";
  if (st.pending) p += `<div class="pending">Servo enabled: ${esc(st.pending.message)}
    — ${st.pending.seconds_left} s left, then servo_enable goes back to 0</div>`;
  if (st.job) p += `<div class="pending">${esc(st.job.name)}: ${esc(st.job.phase)}</div>`;
  $("#d-pending").innerHTML = p;
  const n = st.notice;
  $("#d-notice").innerHTML = n && Date.now() / 1000 - n.t < 30
    ? `<div class="notice ${n.level === "error" ? "error" : ""}">${esc(n.text)}</div>` : "";

  // flags
  const flags = st.flags || {};
  $("#flags").innerHTML = Object.entries(FLAG_INFO).map(([k, [label, tip, bad]]) => {
    const v = !!flags[k];
    return `<div class="flag ${bad ? "bad" : ""} ${v ? "on" : ""}" title="${esc(tip)}">
      <span class="dot ${v ? "on" : ""}"></span>${esc(label)}${k === "polarity" ? `: ${v ? 1 : 0}` : ""}</div>`;
  }).join("");
  const m = st.mode;
  $("#mode").innerHTML = (m ? `
    <span class="k">servo_enable</span><span>${m.servo_enable}</span>
    <span class="k">setpoint source</span><span>${m.sp_source === "in2" ? "IN2 (control computer)" : "register (this page)"}</span>
    <span class="k">setpoint register</span><span>${fmtA(m.setpoint_amps)} A</span>
    <span class="k">open loop</span><span>${m.open_loop}</span>` : "") + `
    <span class="k">heartbeat</span><span>${st.heartbeat_alive == null ? "—" : st.heartbeat_alive ? "advancing" : "STUCK"}</span>
    <span class="k">register map</span><span>rev ${st.map_rev ?? "—"} (host expects ${st.map_rev_expected})</span>
    <span class="k">repeat capture</span><span>${st.auto_capture_s ? `every ${st.auto_capture_s} s` : "off"}</span>`;

  // buttons
  const block = st.control_block;
  const cb = $("#ctl-block");
  if (cb) { cb.hidden = !block; cb.textContent = block ? "Control unavailable: " + block : ""; }
  const ctlOk = S.who.control && !block;
  for (const el of document.querySelectorAll("#ctl button, #ctl input, #ctl select"))
    el.disabled = !ctlOk;
  $("#b-stop").disabled = !(S.who.stop && !block);
  $("#b-stop").title = !S.who.stop ? "Stop is accepted only from the lab PC"
    : block ? block : "servo_enable = 0: graceful ramp to zero";
  const capOk = S.who.capture && !block && !st.job;
  $("#b-cap").disabled = $("#b-auto").disabled = $("#cap-n").disabled = !capOk;

  // new samples for the strip charts
  if (D.hist && st.t != null && st.i_amps != null && st.t !== D.lastT) {
    const h = D.hist;
    h.t.push(st.t); h.i_amps.push(st.i_amps); h.sp_amps.push(st.sp_amps);
    h.u14.push(st.u14); h.out1_volts.push(st.out1_volts);
    D.lastT = st.t;
    while (h.t.length && h.t[0] < st.t - D.span)
      for (const k of Object.keys(h)) h[k].shift();
    D.dirty = true;
  }
  if (st.capture_seq !== D.capSeq) {
    D.capSeq = st.capture_seq;
    if (st.capture_seq) loadCapture(name);
  }
  // sweep results
  const sw = [st.job, st.last_job].find((j) => j && j.name === "sweep");
  $("#sweep-panel").hidden = !sw;
  if (sw && (sw.points.length !== D.sweepN || sw.phase !== D.sweepPhase)) {
    D.sweepN = sw.points.length;
    D.sweepPhase = sw.phase;
    if (!D.plots.mag) {
      D.plots.mag = new Plot($("#plot-mag"), {xlabel: "frequency (Hz)",
        ylabel: "|H|", xlog: true, height: 180, yMinSpan: 0.5, series: [
          {label: "|H| (measured A per commanded A)", color: cssVar("--c-i"), points: true}]});
      D.plots.ph = new Plot($("#plot-ph"), {xlabel: "frequency (Hz)",
        ylabel: "phase (deg)", xlog: true, height: 160, yMinSpan: 10, series: [
          {label: "phase", color: cssVar("--c-ph"), unit: "deg", points: true}]});
    }
    const pts = [...sw.points].sort((a, b) => a[0] - b[0]);
    D.plots.mag.setData(pts.map((p) => p[0]), [pts.map((p) => p[1])]);
    D.plots.ph.setData(pts.map((p) => p[0]), [pts.map((p) => p[2])]);
    $("#sweep-info").innerHTML = `${pts.length} points · ${esc(sw.phase)}${sw.file
      ? ` · saved to <span class="mono">${esc(sw.file)}</span>` : ""}`;
  }
}

function drawDetail() {
  const D = S.detail;
  if (!D || !D.dirty || !D.hist) return;
  D.dirty = false;
  const h = D.hist, now = D.lastT ?? 0;
  const x = h.t.map((t) => t - now);
  const fx = [-D.span, 0];
  D.plots.i.opts.fixedX = fx;
  D.plots.u.opts.fixedX = fx;
  D.plots.i.setData(x, [h.i_amps, h.sp_amps]);
  D.plots.u.setData(x, [h.out1_volts]);
}

// ---------------------------------------------------------------- routing
function route() {
  if (S.detail) {
    clearInterval(S.detail.infoTimer);
    clearInterval(S.detail.drawTimer);
    S.detail = null;
  }
  const m = location.hash.match(/^#\/ch\/(.+)$/);
  if (m && S.devices[decodeURIComponent(m[1])]) {
    S.route = {view: "detail"};
    renderDetail(decodeURIComponent(m[1]));
  } else {
    S.route = {view: "overview"};
    renderOverview();
  }
}

async function main() {
  S.who = await getJSON("/api/whoami");
  const who = $("#who");
  who.textContent = S.who.control ? `${S.who.client}: control enabled`
    : `${S.who.client}: viewing only`;
  who.className = "pill " + (S.who.control ? "ok" : "");
  const list = await getJSON("/api/devices");
  for (const d of list) S.devices[d.name] = {info: d, state: d.state};
  const nSim = list.filter((d) => d.state.simulated).length;
  $("#sim-banner").hidden = !nSim;
  if (nSim && nSim < list.length)
    $("#sim-banner").textContent = "SIMULATION — channels marked SIM have no hardware";
  await Promise.all(list.map(async (d) => {
    try {
      const h = await getJSON(`/api/devices/${d.name}/history?span=60`);
      S.spark[d.name] = h.t.map((t, k) => ({t, i: h.i_amps[k]}));
    } catch (e) { S.spark[d.name] = []; }
  }));
  window.addEventListener("hashchange", route);
  route();
  startStream();
}

main();
