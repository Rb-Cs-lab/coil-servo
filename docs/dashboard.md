# The web dashboard

A web page that shows what every coil-servo board is doing, live, to
anyone on the lab network — and lets the person sitting at the lab PC
operate the boards by hand. It is a monitoring and commissioning tool:
**the servo loop itself runs in the FPGA and never depends on this page**.
Closing the dashboard, or the PC crashing, changes nothing on the boards.

## Starting it

On the Windows lab PC, in the repo folder with the virtual environment
activated (`.venv\Scripts\activate`; see the README's *Setup*):

```
pip install -e .[dashboard]          # once: the web server packages
python -m coil_servo_host.dashboard
```

It prints the addresses to open, then keeps running until Ctrl-C. Useful
options (`--help` lists them all):

| Option | Meaning |
|---|---|
| `--port 8081` | the network port (default 8081; 8080 is taken by KoheronControl, the laser-controller software, on the lab PCs). If the port is busy the dashboard says so and stops |
| `--channels mot,test` | only these cards (card ids or profile names; default: every card in `host/config/dashboard.toml`) |
| `--cards file.toml` | another card list instead of `host/config/dashboard.toml` |
| `--simulate` | no hardware: every board runs on the simulation (see below); `--simulate mot` simulates only the MOT board |
| `--bind 127.0.0.1` | serve this PC only, invisible to the LAN (default `0.0.0.0` = every network adapter) |
| `--logdir D:\coil-logs` | where logs and captures go (default `logs\` in the repo) |
| `--poll 0.05` | status poll interval in seconds (default 0.1 = 10 times per second) |
| `--allow 127.0.0.1,::1` | who may control (see *Who may do what*) |

**Simulation mode** (`--simulate`) needs no board: each board is driven
by the Python model of the MVP power stage (`model/coil_servo_model`), so
you can try every button safely. The status follows the setpoint through
the configured ramps and the compliance limits; a capture runs the exact
integer PI of the FPGA against the plant model. Extra buttons stand in for
the outside world: *raise arm / drop arm* (the DIO4 line from the control
computer), *inject trip*, and *cut drive / restore drive* (the drive
disappears behind the board's back, as when the logic-board latch trips or
the supply is off: it shows the amber *possible latch trip* guess). In the
swept-sine setup a simulated function
generator on IN2 steps through frequencies. The simulated current sensor
reads −0.45 A with nothing flowing (IN2: +0.03 A), so the zero
calibration has something to find. `--sim-old-bitstream mot`
makes a simulated board report the old register map, to see that warning.
A yellow **SIMULATION** banner is shown at the top of the page.

## Opening the page

- **On the lab PC:** <http://localhost:8081>. Only this address gives you
  the controls (see below).
- **From any other PC on the lab LAN:** `http://<lab-pc-ip>:8081`. The
  dashboard prints this address at start-up: the lab PC's own address on
  the network the boards are on (e.g. `192.168.0.61`). A PC that is also
  on Wi-Fi has a second address; the start-up message lists it as "not on
  the boards' network, don't use". (By hand: `ipconfig`, the *IPv4
  Address* of the **wired** Ethernet adapter.)
- **Windows Firewall:** the first time it starts, Windows asks whether
  Python may accept connections. Allow it on **private** networks (the lab
  LAN), not public ones. If you clicked it away, other PCs cannot connect:
  *Windows Security → Firewall & network protection → Allow an app
  through firewall* → tick Python for *Private*.

## What the page shows

### Overview

One card per entry in `host/config/dashboard.toml`: **MOT, Z shim, X
shim, Y shim and Test setup**. Each card is a *view* of one board with one
settings profile from `channels.toml` (the card shows `profile [mot]`).
At the top, a permanent note: the red FAULT LED of the safety logic board
cannot be seen from here (see *What the page cannot see* below).

- **dot and address** — green: connected to the board's register server;
  red: offline (the card says why — e.g. the board is off or not deployed
  yet). An offline board is retried every 5 s and never slows the others.
- **state** — the firmware's state machine: `IDLE` (off, waiting),
  `RUN` (servo active), `RAMP_DOWN` (graceful stop in progress),
  `TRIP`/`TRIPPED` (over-current trip), `TIMEOUT_HOLD` (a polarity flip
  never reached zero current), and the flip states `DISABLE`, `FLIP`,
  `ENABLE`, `SETTLE`.
- **current** — the measured coil current in amps (IN1, averaged over the
  1 µs PI tick), with the active setpoint underneath, and a sparkline of
  the last minute.
- **red boxes** — only conditions where a person must act on the coil:
  TRIPPED, TIMEOUT_HOLD, the fault input, a setpoint whose sign disagrees
  with the bridge polarity, a stuck fabric heartbeat, and **OLD
  BITSTREAM** (see below).
- **amber boxes** — worth a look, not an emergency: the board's settings
  match no profile (fresh deploy: apply one), a profile the config loader
  refused, OUT1 at the command clamp, integrator railed, and the
  **possible latch trip** guess (below).
- **grey lines** — information: *look only, the control computer owns
  this coil*; *repeating capture every N s*.
- **NO FRESH DATA** — the values are greyed out when the last status is
  more than ~1 s old (the poll stopped) or the dashboard server itself
  stops answering. Frozen numbers are never shown as live.
- **badges** — `BENCH` on the Test setup card; `SIM` marks a simulated
  board.
- **firmware** — which firmware the board runs: the register-map
  revision (the layout of its settings; red if it is not the one this
  host knows) and the git commit the bitstream was built from, e.g.
  "commit 0aa4ed2", with ", built with uncommitted changes" when the build
  had local edits. "unknown" means the bitstream carries no build ID.

**The Test setup card** (profile `mot_bench`, the 3 A bench-supply
campaign of [bench_supply_test.md](bench_supply_test.md)) is drawn
differently on purpose: purple dashed border with a hatched top edge, a
`BENCH` badge, and the bench supply's current limit and rating (3 A
supply, 2.5 A rated) on the card.

**One board, two cards.** MOT and Test setup are two profiles for the
**same board** (192.168.0.65). The dashboard keeps one connection to it.
Every couple of seconds it reads the board's settings and works out which
profile they match (the same check `watch` and `check` print):

- the card whose profile the board holds shows it live;
- the other card is greyed out: **"board in use as Test setup"** (or
  "… as MOT") — not red, because nothing is wrong;
- if the settings match neither (not configured since the bitstream was
  loaded, or changed by hand), both cards show the board with an amber
  "match no profile — apply a profile".

To switch, Stop, open the other card and press *Apply profile*. When the
test rig gets its own Red Pitaya, only `board_host` of `[mot_bench]` in
channels.toml changes; the cards then show two independent boards.

**Possible latch trip (amber).** The logic board's latch is invisible to
the Pitaya, but its effect is not: the current collapses while the Pitaya
still enables the bridge. The card shows the guess when, for more than
0.2 s, the servo is in RUN with the bridge enabled and a setpoint above
5 % of rated, but the measured current is below 1 % of rated (or 2 ADC
counts). The same symptom comes from the supply being off or tripped, or
an open circuit — the text says so. It is amber because it is an
inference, not a measured signal.

Click a card for its page.

### Card page

- **Header:** the card's title and profile, state, current, setpoint,
  **OUT1** — what the output to the pass-bank gate driver actually
  carries, in volts at the op-amp input and in DAC counts — and, smaller,
  the **PI command (u14)**, the controller's output. They differ: with the
  bridge disabled (IDLE) or after a trip the firmware holds OUT1 at
  DAC code 0 — which measures −0.041 V, not 0 V, so the header shows
  "−0.0413 V (DAC code 0: outputs off)" — while the PI value stays at its last
  value (the integrator is held, not cleared); in open loop OUT1 carries
  the setpoint, limited to the clamp, not the PI value. Then any alarms
  and the big
  **STOP** button. On a greyed card it says which role the board is in
  ("in use as MOT", with a link) and how to switch.
- **Live charts:** measured current and active setpoint (the setpoint
  *after* the firmware's ramp limiter), and OUT1 (solid) with the PI
  command (dashed), over a
  selectable span from 10 s to 10 min. The points are the 10-per-second
  status polls, so this shows slow behaviour — use a capture for anything
  faster than ~0.2 s. Hover for values.
- **Flags:** the status bits, each with a tooltip. `OUT1 at floor` is
  normal at low current in RUN (the PI sits on its dead-zone floor; current
  can only decay).
- **Mode:** whether the servo is enabled, where the setpoint comes from
  (IN2 = the control computer's analog output, or the setpoint register
  = this page), open loop on/off, heartbeat, and the register-map
  revision.
- **Zero offsets:** the sensor zero offsets — what the current sensor
  (IN1) and the setpoint input (IN2) read with nothing flowing. The table
  shows the values stored in `channels.toml` for this card's profile and
  the values loaded in the board, in amps and ADC counts, with ✗ where they
  differ. The FPGA subtracts the loaded values from every sample, so the
  loop, the over-current trip and the flip zero window work on the true
  current. Amber *IN1 not calibrated* means the profile still has
  `in1_offset_amps = 0`; amber *zero offsets on the board differ* means
  someone loaded values without storing them (Apply profile puts the
  stored ones back). Also: the last zero calibration and its buttons (see
  the controls below).
- **Configuration:** what `python -m coil_servo_host.config <channel>`
  prints (supply limit for bench profiles, sensor scale, pass-bank gain,
  dead zone, floor, clamp, trip, zero window, ramps, PI gains, dither,
  zero offsets, board LEDs) for this card's profile, plus read-only
  *measured* rows: IN1 full scale (`in1_fs_volts`, the volts on IN1 at the
  full-scale ADC code — the MOT board reads 1.10 V, not the nominal 1.0 V,
  which sets I_FS = 220 A; shown only when not nominal) and, from the
  gate-stage test (Test 1, 2026-11-11), OUT1 full
  scale (`out1_fs_volts`), the OUT1 offset at DAC code 0
  (`out1_offset_volts`) and the gate ceiling (`V_opamp_max`). They come
  from `channels.toml`; change them there. Then which profile the board
  holds, and the board's **read-back** configuration compared field by field
  with this card's profile. Red rows differ — after a fresh deploy every
  row differs until the profile is applied. *Live fields* are the ones
  that change in normal operation (enable, setpoint, …).

### What the page cannot see

The safety logic board's heartbeat latch (and its red FAULT LED) is not
wired back to the Red Pitaya — DIO5 is unconnected in the MVP. A latch
trip therefore only shows up indirectly: the current drops while the
Pitaya still enables the bridge (the amber *possible latch trip* guess).
Both the overview and every card page say so permanently. When in doubt,
look at the logic board.

### Old bitstream

The host code knows register-map revision 4. A board still running an
older bitstream (the 2026-08-27 image reports revision 0) has a different
register layout: writing would land values in the wrong registers. The
dashboard then shows **OLD BITSTREAM** in red and is **monitoring only** for
that board — every control button is refused, and the status fields may
themselves be misread. Fix: deploy the current bitstream
(`python -m coil_servo_host.deploy mot`).

## Waveform capture

*Capture now* records **16.8 ms** of measured current and setpoint at
976.6 kS/s (one point per 1.024 µs PI tick, 16384 points) from the FPGA's
capture memory and plots it against time in ms. This is the tool for
"what is the current actually doing" — ripple, a ramp, ringing after a
step. *Repeat every N s* takes one automatically (N ≥ 1; 0 = off); the
card then says "repeating capture every N s".

Captures are **started by the PC, not by the experiment**: they land at a
random moment relative to a shot (see the proposal below). A capture
writes only the two capture-control bits (`capture_sel`, `fifo_rst`); it
never touches the loop and is allowed during a sequence. Each capture is
saved as `logs\captures\<board>_<date>-<time>_capture.csv` with columns
`t_s, i_amps, setpoint_amps` (same format as `step`). Manual captures are
kept; repeating ones (`..._auto.csv`) are deleted after one hour.

While the dashboard captures, **do not run `step` or `sweep` from a
terminal** on the same board: they use the same capture memory and would
corrupt each other's record. The capture panel says so.

## Who may do what

| What | Who |
|---|---|
| Watch everything (states, charts, configuration, captures) | everyone on the LAN |
| Capture now / repeat capture | the lab PC (or everyone, with `--allow-remote-capture`) |
| STOP | the lab PC (or everyone, with `--allow-remote-stop`) |
| Everything else (apply profile, enable, setpoint, acknowledges, step, sweep) | **the lab PC only** |

"The lab PC" means requests from the PC's own loopback address, i.e. a
browser on that PC opened at `http://localhost:8081`. Opening the LAN
address (`http://192.168.0.x:8081`) on the lab PC itself counts as a
remote viewer. The list is configurable with `--allow` (addresses or
networks, e.g. `--allow 127.0.0.1,::1,192.168.0.10`), but think twice: the
coils carry up to 100 A, and the person pressing a button should be
standing at the machine.

Why: the boards' register server has no authentication of its own (it
trusts the private wired LAN), and a mistaken click from a desk elsewhere
could drive current while someone works on the coils. Every action that
can drive current asks for **confirmation** in a dialog first; STOP never
does.

**During experimental sequences the page is look-only, enforced:** while
the servo is enabled with the setpoint from IN2 (the control computer owns
the coil), the dashboard refuses every action except STOP and captures,
with "look only" as the reason. To take over by hand, press STOP first.

Every control request — accepted or refused, from any address — is
written to the action log with the client's IP address.

## The controls (lab PC only)

- **STOP** — `servo_enable = 0`: the firmware ramps the current down at
  the configured rate (50 A/ms on the MOT) and returns to IDLE. Always
  safe; also ends a running step or sweep. It does not open the bridge
  at current. STOP needs only a reachable board with the current
  bitstream — never a valid profile. If the board was in open loop (a
  sweep, or another tool), it stays in open loop until IDLE is reached
  and only then is switched back to closed loop with the integrator
  cleared: the firmware's integrator keeps adding up in open loop, and
  switching to closed loop while running would jump OUT1 (found on the
  loopback, 2026-11-11). Anything that would switch to closed loop while
  the board runs is refused with "press STOP and wait for IDLE first".
- **Apply profile [mot]** (on the MOT card; **[mot_bench]** on Test
  setup) — writes that profile's configuration from `channels.toml`
  (gains, clamp, floor, trip, ramps, dither, and the board-LED pattern:
  LED 7 lit = bench settings) and reads it back. Only in IDLE. The dialog
  says which profile the board holds now. For `mot_bench` it shows a red
  box: **only with the current-limited bench supply connected, never with
  the SE-600-5**; going back from bench to production settings it warns
  that the production power stage must be connected (see the switching
  checklist in bench_supply_test.md).
- **Enable** — sets `servo_enable = 1` with the chosen setpoint source:
  *this page* (the setpoint register, starting at 0 A) or *IN2* (the
  control computer). The board then **waits in IDLE for a rising edge on
  arm (DIO4)**: the page shows "raise arm now" with a countdown. An arm
  line that is already high does not count — drop it and raise it again.
  After 30 s without RUN (or on a trip) `servo_enable` is set back to 0,
  exactly like the command-line tools. Enable, Set current, Step and
  Sweep act as the card's profile and are refused unless the board holds
  it (apply the profile first) — so you cannot run the Test setup's
  board with the MOT's settings by mistake, or the reverse.
- **Arm (software, testing)** — for bench tests, so nobody has to plug
  the arm cable in and out: after *Enable*, while the page says "raise arm
  now", this gives the servo its arm edge from the page instead. It uses
  an existing setting, `dio_invert` bit 1 (CFG word 13), which inverts how
  the FPGA reads DIO4 *before* it looks for the rising edge: switching it
  on with DIO4 low looks exactly like an arm edge. The dashboard switches
  it straight back (losing arm in RUN never opens the bridge, by design),
  so the board keeps matching its profile, and checks that the board left
  IDLE. **Leave the DIO4 cable unplugged** — the input has a pull-down and
  reads low. (With DIO4 high it still arms, one step later, and the
  message says so.) Lab PC only, with a confirmation, logged. It bypasses
  the control computer's arm, so it is for testing, not for runs with the
  experiment. Stop as usual with STOP; to run again: Enable, then arm.
  Works with the deployed bitstream; no hardware change.
- **Set current** — the setpoint register in amps (only with the source
  *this page*); refused above the profile's rated current (100 A on the
  MOT, 2.5 A on the Test setup). The firmware ramps to it.
- **trip_ack** — after an over-current trip: TRIPPED → IDLE. Find the cause
  first. Running again needs Enable and a fresh arm edge.
- **flip_fault_ack** — TIMEOUT_HOLD → RUN.
- **Step response** — runs the same steps as `python -m
  coil_servo_host.step`: apply profile, enable, wait for the arm edge,
  step the setpoint 2 ms into a capture, ramp back down, disable. The
  result appears in the capture plot and is saved (`..._step.csv`).
  *Open loop* drives OUT1 with the command the model predicts for that
  current; or give *OUT1 counts* to set the open-loop command directly,
  bypassing the model (as `step --counts`, for scanning the dead zone). **First runs: dummy resistive load only.**
- **Swept-sine** — as `python -m coil_servo_host.sweep`: the board goes to
  open loop with **IN2 driving OUT1 directly**. It needs a function
  generator on IN2 with its offset above the pass-bank dead zone (~0.44 V
  at OUT1, ~4.4 V at the generator before the ÷10) and a small amplitude;
  step the generator through the frequencies and each clean new frequency
  adds a point to the Bode plot (gain and phase) and to
  `..._sweep.csv`. STOP ends it.

- **Zero calibration…** (in the *Zero offsets* panel) — as `python -m
  coil_servo_host.zero`: records the raw IN1/IN2 readings (before the
  FPGA's correction), 8 records over about 1 s, and shows the offsets in
  amps and counts with their spread between records. Only with the servo
  stopped (IDLE, bridge disabled). Before you press it: the LEM is powered
  (±15 V) and warmed up, and the control computer's setpoint output is at
  0 V. Measuring changes nothing. A loud warning above 0.3 A on IN1
  ("sensor needs attention": a magnetised LEM read −5.8 A on 2026-11-11);
  above 10 % of the sensor full scale (22 A at the MOT's I_FS = 220 A) the result is
  refused — that is a broken, unpowered or saturated sensor, not an
  offset. Then:
  - **Store in channels.toml** — writes `in1_offset_amps` /
    `in2_offset_amps` into the measured profile's section, with the date
    as a comment, keeping everything else. The board uses them after
    *Apply profile*. `channels.toml` is in git: commit the change.
  - **Load into board** — writes the offsets to the board now (only in
    IDLE); readings jump to ~0 A. Not stored: Apply profile would restore
    the stored ones.

Nothing on this page clears a hardware fault: the heartbeat latch on the
logic board is reset by hand. The operating rule: **press STOP first and
wait for IDLE (bridge enabled = no), then press the latch reset** — if the
heartbeat dropped while the Pitaya kept running, the integrator may be
wound up, and a reset with the servo still running would re-close the
bridge with OUT1 at the clamp. Dropping arm (DIO4) alone is *not* enough:
it never stops a running servo. STOP is always accepted (also during the
look-only lock). To run again after the reset: Enable, then a fresh arm
edge.

## Logs

All under `--logdir` (default `logs\` in the repo, not tracked by git):

| File | Contents |
|---|---|
| `status\<board>_<YYYYMMDD-HHMM>.csv` | every status poll: time, state, flags, current, setpoint (amps), PI command (u14 counts, volts), actual OUT1 (counts and volts), map revision, profile held. One file per 10 minutes; **only the last hour is kept** |
| `actions_<YYYY-MM-DD>.csv` | every control request with the client's IP and the result; connection changes. One file per day, **kept in full** (the audit trail) |
| `captures\<board>_<date>-<time>_<kind>.csv` | captures, steps (`t_s, i_amps, setpoint_amps`) and sweeps (`f_hz, mag, phase_deg, stim_amps`); repeating captures (`_auto`) are kept one hour, the rest are kept |

At 10 polls per second an hour of status is ~3 MB per board; when a new
10-minute file starts, files that ended more than an hour ago are deleted.
`--log-every 10` keeps one poll in ten. `<board>` is the first
channels.toml profile of that board (`mot` for the MOT board, also when
it holds the bench settings — the *profile* column says which). In memory
the dashboard keeps the last 10 minutes (`--history`).

## How it works (for maintainers)

- **Devices and cards.** A *device* is one board: `servo_channel.py`
  runs one background thread per board_host with **one** TCP connection to
  its register server, polled at 10 Hz and shared by all browsers; its
  profiles are every channels.toml table with that board_host. A *card*
  is a view of a device with one profile; the list is
  `host/config/dashboard.toml` (id, title, profile, optional
  `style = "bench"`). Without that file there is one card per
  channels.toml table. Actions that drive current take the card's
  profile as a parameter and are refused unless the board holds it.
- An unexpected error in a poll takes the board "offline" with the
  message and reconnects; the snapshot carries `age_s`/`stale`, so the
  page never shows a dead poll as live.
- `host/coil_servo_host/dashboard/`: `servo_channel.py`, `app.py` (the web
  server, [Starlette](https://www.starlette.io/) on uvicorn), `access.py`
  (allowlist), `logs.py`, `static/` (plain HTML/JS/CSS, no build step;
  `plot.js` is a small built-in plotting module, so nothing is loaded
  from the internet).
- Monitoring only **reads** registers. CFG is written only by a control
  action or a capture, and before each write the dashboard re-reads the
  board's CFG (`Board.sync_shadow`) so it never puts back a value another
  tool changed meanwhile.
- Running `step`/`sweep`/`check` from the command line while the
  dashboard is open works (the register server takes several clients),
  but don't start a capture in both at once: they share one capture
  memory.
- `host/coil_servo_host/simboard.py` is the simulated board; the tests in
  `host/tests/test_dashboard.py` run against it and against the real
  `Board` talking to the mock register server. The simulated MOT board
  switches its physics to the bench supply when the bench settings are
  applied (LED 7), and has a *cut drive* button that imitates a latch
  trip or a dead supply.
- The dashboard tests need Starlette and httpx (`pip install -e .[dev]`);
  without them pytest skips the file, and the skip reason is printed in
  the test summary.

## Proposal: shot-synchronous capture (firmware feature, not built)

Today a capture starts when the PC resets the capture memory, so it cannot
be aligned with an experimental shot — 16.8 ms windows land at random
times. Seeing the current *during* a MOT load, a flip or a switch-off needs
the FPGA to start the capture itself on an event. A small addition would
do it:

- a CFG field `cap_trigger` selecting the start event: host (today's
  behaviour), **arm rising edge** (DIO4), **flip request** (DIO3), or a
  **setpoint step** larger than a CFG threshold (detected on the active
  setpoint);
- a CFG `cap_pretrigger` in decimated samples: the FIFO records
  continuously and freezes `16384 − pretrigger` samples after the event,
  so the record shows what happened just before as well;
- STS bits `cap_armed` / `cap_done` and the trigger's sample index, so
  the host knows when to drain the FIFO and where t = 0 is.

The dashboard would then "arm" a capture and draw it when the shot has
happened; the experiment-control software could fetch it per shot. It
needs a register-map revision (docs/register_map.md first), HDL in the
capture path, a cocotb bench and a bitstream rebuild — a separate,
self-contained step.

## JSON API (for integrating other software later)

Everything the page shows comes from a small HTTP/JSON interface that
other programs — the experiment-control software, or a future dashboard
for lasers and other lab electronics — can use too. The server is built
around a generic *device* (`dashboard/device.py`): each servo board is
one device of kind `coil_servo`; a laser controller would be another
device class with its own snapshot and actions, served by the same
endpoints, and its own entries in `dashboard.toml`.

| Request | Answer |
|---|---|
| `GET /api/cards` | the overview's cards: `id`, `title`, `profile`, `device`, `style` |
| `GET /api/devices` | list of devices (boards): name, kind, available actions, current state |
| `GET /api/devices/<name>` | the current state (snapshot), e.g. `state`, `i_amps`, `sp_amps`, `u14` and `pi_volts` (PI output), `out1_counts`/`out1_volts` (actual OUT1) and `out1_mode` (`off`, `open` or `closed`), `flags`, `alarms`, `warnings`, `connection`, `map_ok`, `build_id` and `build_text` (firmware identity), `offsets_board` (zero offsets in the board) and per profile `profiles[].offsets` (in channels.toml), `zero` (the last zero calibration), `held` (profiles the board holds), `profiles`, `age_s`, `stale`, `sequence_lock`, `stop_block`, `control_block` |
| `GET /api/devices/<name>/history?span=60` | the last *span* seconds as columns `t` (Unix time), `i_amps`, `sp_amps`, `u14`, `out1_volts` (actual), `pi_volts` |
| `GET /api/devices/<name>/info?profile=mot_bench` | one profile's derived values and the CFG read-back compared with it |
| `GET /api/devices/<name>/capture` | the latest capture: `t`, `kind`, `dt_s`, `i_amps[]`, `sp_amps[]`, `file` |
| `GET /api/stream` | server-sent events: one `data:` line per 0.1 s with every device's snapshot |
| `GET /api/whoami` | your address and what you may do |
| `POST /api/devices/<name>/actions/<action>` | run an action; JSON body with its parameters |

POST requests need the header `X-Coil-Servo: 1` and a JSON body; actions
that can drive current also need `"confirm": true`, and take `"profile":
"mot"` (or `"mot_bench"`) — `apply_profile` requires it, the others
default to the profile the board holds. The same allowlist applies as for
the page. Example from Python on the lab PC:

```python
import json, urllib.request
req = urllib.request.Request(
    "http://localhost:8081/api/devices/mot/actions/capture",
    data=b"{}", method="POST",
    headers={"Content-Type": "application/json", "X-Coil-Servo": "1"})
print(json.load(urllib.request.urlopen(req)))
state = json.load(urllib.request.urlopen("http://localhost:8081/api/devices/mot"))
print(state["state"], state["i_amps"])
```

Errors come back as `{"ok": false, "error": "..."}` with HTTP status 400
(bad request), 403 (not allowed from your address), 404 (no such device or
action) or 409 (refused in the current state, e.g. old bitstream, not
IDLE).
