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
| `--port 8080` | the network port (default 8080) |
| `--channels mot` | only these channels (default: every channel in `host/config/channels.toml`) |
| `--simulate` | no hardware: every channel runs on the simulation (see below); `--simulate mot` simulates only `mot` |
| `--bind 127.0.0.1` | serve this PC only, invisible to the LAN (default `0.0.0.0` = every network adapter) |
| `--logdir D:\coil-logs` | where logs and captures go (default `logs\` in the repo) |
| `--poll 0.05` | status poll interval in seconds (default 0.1 = 10 times per second) |
| `--allow 127.0.0.1,::1` | who may control (see *Who may do what*) |

**Simulation mode** (`--simulate`) needs no board: each channel is driven
by the Python model of the MVP power stage (`model/coil_servo_model`), so
you can try every button safely. The status follows the setpoint through
the configured ramps and the compliance limits; a capture runs the exact
integer PI of the FPGA against the plant model. Extra buttons stand in for
the outside world: *raise arm / drop arm* (the DIO4 line from the control
computer) and *inject trip*. In the swept-sine setup a simulated function
generator on IN2 steps through frequencies. `--sim-old-bitstream mot`
makes a simulated board report the old register map, to see that warning.
A yellow **SIMULATION** banner is shown at the top of the page.

## Opening the page

- **On the lab PC:** <http://localhost:8080>. Only this address gives you
  the controls (see below).
- **From any other PC on the lab LAN:** `http://<lab-pc-ip>:8080`. To
  find the lab PC's address, run `ipconfig` in a Command Prompt on it and
  take the *IPv4 Address* of the **wired** Ethernet adapter (something like
  `192.168.0.10`). The dashboard prints its guess at start-up.
- **Windows Firewall:** the first time it starts, Windows asks whether
  Python may accept connections. Allow it on **private** networks (the lab
  LAN), not public ones. If you clicked it away, other PCs cannot connect:
  *Windows Security → Firewall & network protection → Allow an app
  through firewall* → tick Python for *Private*.

## What the page shows

### Overview

One card per channel in `channels.toml`:

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
- **red boxes** — conditions that need a person: TRIPPED, TIMEOUT_HOLD,
  the fault input, a setpoint whose sign disagrees with the bridge
  polarity, a stuck fabric heartbeat, and **OLD BITSTREAM** (see below).
  Amber boxes are informational (OUT1 at the command clamp, integrator
  railed).
- **badges** — `BENCH` for a bench-test profile (a channel with
  `bench = true` in channels.toml, e.g. `mot_bench`, the 3 A bench-supply
  campaign of [bench_supply_test.md](bench_supply_test.md)); its card also
  shows the bench supply's current limit. `SIM` marks a simulated board.

`mot` and `mot_bench` are two profiles for the **same board**
(192.168.0.65), so both cards show that board. Every few seconds the
dashboard reads the board's settings and asks which channels.toml profile
they match (the same check `watch` and `check` print). A card whose
profile the board does *not* hold gets a red **PROFILE MISMATCH** box:
"the board holds the 'mot_bench' settings" (the other card is the one in
use), or "UNKNOWN / mixed" (not configured since the bitstream was loaded,
or changed by hand). With both cards open, exactly one of them is red.

Click a card for the channel page.

### Channel page

- **Header:** state, current, setpoint, the OUT1 command (the PI output
  sent to the pass-bank gate driver, in volts at the op-amp input and in
  DAC counts), any alarms, and the big **STOP** button.
- **Live charts:** measured current and active setpoint (the setpoint
  *after* the firmware's ramp limiter), and the OUT1 command, over a
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
- **Configuration:** what `python -m coil_servo_host.config <channel>`
  prints (supply limit for bench profiles, sensor scale, pass-bank gain,
  dead zone, floor, clamp, trip, zero window, ramps, PI gains, dither,
  board LEDs), which profile the board holds, then the board's
  **read-back** configuration compared
  field by field with the profile. Red rows differ — after a fresh deploy
  every row differs until the profile is applied. *Live fields* are the
  ones that change in normal operation (enable, setpoint, …).

### Old bitstream

The host code knows register-map revision 3. A board still running an
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
step. *Repeat every N s* takes one automatically (N ≥ 1; 0 = off).

Captures are **started by the PC, not by the experiment**: they land at a
random moment relative to a shot (see the proposal below). A capture
writes only the two capture-control bits (`capture_sel`, `fifo_rst`); it
never touches the loop and is safe during a sequence. Each capture is saved
as `logs\captures\<channel>_<date>-<time>_capture.csv` with columns
`t_s, i_amps, setpoint_amps` (same format as `step`).

## Who may do what

| What | Who |
|---|---|
| Watch everything (states, charts, configuration, captures) | everyone on the LAN |
| Capture now / repeat capture | the lab PC (or everyone, with `--allow-remote-capture`) |
| STOP | the lab PC (or everyone, with `--allow-remote-stop`) |
| Everything else (apply profile, enable, setpoint, acknowledges, step, sweep) | **the lab PC only** |

"The lab PC" means requests from the PC's own loopback address, i.e. a
browser on that PC opened at `http://localhost:8080`. Opening the LAN
address (`http://192.168.0.x:8080`) on the lab PC itself counts as a
remote viewer. The list is configurable with `--allow` (addresses or
networks, e.g. `--allow 127.0.0.1,::1,192.168.0.10`), but think twice: the
coils carry up to 100 A, and the person pressing a button should be
standing at the machine.

Why: the boards' register server has no authentication of its own (it
trusts the private wired LAN), and a mistaken click from a desk elsewhere
could drive current while someone works on the coils. Every action that
can drive current asks for **confirmation** in a dialog first; STOP never
does. **During experimental sequences the page is look-only** — the
control computer drives the setpoint through IN2 and the arm line, and a
click here would interfere.

Every control request — accepted or refused, from any address — is
written to the action log with the client's IP address.

## The controls (lab PC only)

- **STOP** — `servo_enable = 0`: the firmware ramps the current down at
  the configured rate (50 A/ms on the MOT) and returns to IDLE. Always
  safe; also ends a running step or sweep. It does not open the bridge
  at current.
- **Apply profile [mot]** — writes the channel's configuration from
  `channels.toml` (gains, clamp, floor, trip, ramps, dither, and the
  board-LED pattern: LED 7 lit = bench settings) and reads it back. Only
  in IDLE. The dialog says which profile the board holds now. Applying
  `mot_bench` is for the current-limited bench supply only, never with
  the SE-600-5 (and the reverse: see the switching checklist in
  bench_supply_test.md).
- **Enable** — sets `servo_enable = 1` with the chosen setpoint source:
  *this page* (the setpoint register, starting at 0 A) or *IN2* (the
  control computer). The board then **waits in IDLE for a rising edge on
  arm (DIO4)**: the page shows "raise arm now" with a countdown. An arm
  line that is already high does not count — drop it and raise it again.
  After 30 s without RUN (or on a trip) `servo_enable` is set back to 0,
  exactly like the command-line tools. Enable is refused when the board's
  configuration differs from the profile (apply the profile first).
- **Set current** — the setpoint register in amps (only with the source
  *this page*); refused above the rated current (100 A on the MOT). The
  firmware ramps to it.
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

Nothing on this page clears a hardware fault: the heartbeat latch on the
logic board is reset by hand, and the operating rule stands — **disarm
(DIO4 low) before pressing the latch reset**.

## Logs

All under `--logdir` (default `logs\` in the repo, not tracked by git):

| File | Contents |
|---|---|
| `status\<channel>_<YYYY-MM-DD>.csv` | every status poll: time, state, flags, current, setpoint (amps), OUT1 (counts and volts), map revision |
| `actions_<YYYY-MM-DD>.csv` | every control request with the client's IP and the result; connection changes |
| `captures\<channel>_<date>-<time>_<kind>.csv` | captures, steps (`t_s, i_amps, setpoint_amps`) and sweeps (`f_hz, mag, phase_deg, stim_amps`) |

At 10 polls per second the status log grows by ~70 MB per channel per
day; `--log-every 10` keeps one poll in ten. A new file starts at local
midnight. In memory the dashboard keeps the last 10 minutes
(`--history`).

## How it works (for maintainers)

- `host/coil_servo_host/dashboard/`: `servo_channel.py` (one background
  thread per channel: **one** TCP connection to the board's register
  server, polled at 10 Hz and shared by all browsers), `app.py` (the web
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
  `Board` talking to the mock register server.

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
around a generic *device* (`dashboard/device.py`): each servo channel is
one device of kind `coil_servo`; a laser controller would be another
device class with its own snapshot and actions, served by the same
endpoints.

| Request | Answer |
|---|---|
| `GET /api/devices` | list of devices: name, kind, available actions, current state |
| `GET /api/devices/<name>` | the current state (snapshot), e.g. `state`, `i_amps`, `sp_amps`, `u14`, `out1_volts`, `flags`, `alarms`, `connection`, `map_ok` |
| `GET /api/devices/<name>/history?span=60` | the last *span* seconds as columns `t` (Unix time), `i_amps`, `sp_amps`, `u14`, `out1_volts` |
| `GET /api/devices/<name>/info` | profile, derived values, CFG read-back vs profile |
| `GET /api/devices/<name>/capture` | the latest capture: `t`, `kind`, `dt_s`, `i_amps[]`, `sp_amps[]`, `file` |
| `GET /api/stream` | server-sent events: one `data:` line per 0.1 s with every device's snapshot |
| `GET /api/whoami` | your address and what you may do |
| `POST /api/devices/<name>/actions/<action>` | run an action; JSON body with its parameters |

POST requests need the header `X-Coil-Servo: 1` and a JSON body; actions
that can drive current also need `"confirm": true`. The same allowlist
applies as for the page. Example from Python on the lab PC:

```python
import json, urllib.request
req = urllib.request.Request(
    "http://localhost:8080/api/devices/mot/actions/capture",
    data=b"{}", method="POST",
    headers={"Content-Type": "application/json", "X-Coil-Servo": "1"})
print(json.load(urllib.request.urlopen(req)))
state = json.load(urllib.request.urlopen("http://localhost:8080/api/devices/mot"))
print(state["state"], state["i_amps"])
```

Errors come back as `{"ok": false, "error": "..."}` with HTTP status 400
(bad request), 403 (not allowed from your address), 404 (no such device or
action) or 409 (refused in the current state, e.g. old bitstream, not
IDLE).
