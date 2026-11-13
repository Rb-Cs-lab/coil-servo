# Coil servo — MVP build and bring-up log (November 2026)

**Channel:** MOT | **Board:** Red Pitaya STEMlab 125-14 at 192.168.0.65
(wired) | **Power stage:** the MVP (docs/mvp_architecture.md) |
**Sources:** OneNote "Construction" notes (Santi + Jeff, 11-02 to 11-06)
and the design decisions of 11-10.

**Words used in these notes:**

- **Setpoint:** the requested coil current.
- **The controller (servo):** the logic in the Red Pitaya that compares the
  measured current (IN1) with the setpoint and adjusts OUT1. Its two
  settings are kp (how strongly it reacts to the present error) and ki
  (how fast its built-up correction grows while an error persists).
- **Closed loop / open loop:** closed loop = the controller is active;
  open loop = OUT1 is set by hand and nothing corrects it.
- **Step ("count" in the software):** the smallest change the board can
  output (OUT1) or measure (IN1, IN2).
- **Firmware image ("bitstream"):** the file loaded into the Red Pitaya's
  chip (the FPGA). It is lost when the board is powered off. "Deploy" =
  load it.
- **Settings and status ("registers"):** numbers the PC writes to the
  board (settings) and reads back from it (status). The **register
  server** is the small program on the board that passes them between
  the PC and the chip.
- **Board states:** IDLE = idle, off; RUN = running; TRIPPED = stopped by
  the over-current trip, waiting for acknowledgement (`trip_ack`);
  RAMP_DOWN = bringing the current to zero (for example before a flip).
- **Arm:** a signal that allows the servo to start. It must be switched
  on *after* `servo_enable` is set ("arm edge"); an arm signal that is
  already on does not count.
- **Zero window:** a band of about ±0.5 A around zero current. Flips
  happen only inside it.
- **Freewheel:** when the pass transistors switch off, the coil's current
  circulates through the bridge's built-in diodes and dies away by
  itself.
- **Floor and clamp:** while running, OUT1 never goes below the floor
  (just under the pass transistors' turn-on point) or above the clamp.
- **Latch:** the safety board's cut-off. It opens the bridge when the
  heartbeat stops and stays tripped until the reset button is pressed.
- **Profiles `mot` and `mot_bench`:** two sets of settings in
  `host/config/channels.toml` (the real MOT coils / the 3 A bench tests).

## Build log

- **11-02 — transistor turn-on voltages.** The gate threshold voltage
  V_GS(th) was measured on all 10 pass-bank transistors (IXTN200N10L2,
  at 3 mA) and all 10 bridge transistors (IXFN420N10T, at 8 mA): gate
  tied to drain, a series resistor from a bench supply, a multimeter
  across gate and source. Pass bank 3.35–3.56 V → installed #1, 2, 4, 7,
  9, 10 (3.50–3.56 V, a 0.06 V spread → the six share the current to
  within 12 % at 100 A). Bridge 3.51–3.59 V → installed #2, 5, 6, 9
  (0.01 V spread). The SE-600-5 was reordered after the wrong model
  arrived.
- **11-03 — logic board on the MOT Red Pitaya** (still running the
  2026-08-27 firmware image). `pip install -e .` then `python -m
  coil_servo_host.check mot` **failed at first**: after a reboot the
  board had neither the firmware image nor the register server running.
  Fixed by hand over the network (ssh):

  ```bash
  /opt/redpitaya/bin/fpgautil -b /root/coil_servo.bit.bin
  ```

  ```bash
  nohup python3 /root/coil_servo_server.py --port 9001 >/dev/null 2>&1 &
  ```

  After that `check` reported heartbeat ADVANCING, LED walk done,
  state IDLE, armed 0, fault 0, bridge_en 0, polarity 0, measured
  **+0.179 A** (at the old full scale I_FS = 125 A this is ≈ 1.4 mV of
  offset on IN1, ≈ 0.29 A at the MVP's 200 A scale). The LED acceptance
  sequence passed (bringup.md 3a). In that test `servo_enable` was set
  from Python *after* arm was already on, and the old firmware started
  running because arm was on. The MVP firmware needs arm to be switched
  on after `servo_enable`, so the procedure now enables first, then arms.
- **11-03 — heartbeat timer (monostable).** First built with 68 kΩ: the
  timeout was ~1.1 ms, about one heartbeat period, so a lost heartbeat
  could be mistaken for a present one. Changed to **220 kΩ / 100 nF:
  cut-off ≈ 7.6 ms after the heartbeat stops, consistent**. (The
  CD74HC123 is specified at 5 V and times differently on the 3.3 V
  supply; trust the measurement.)
- **11-04 — cold plates.** Water-cooled plates machined (bridge: 4
  SOT-227 parts; pass bank: 6 SOT-227 + 6 MP930 parts). Transistors
  mounted with thermal paste.
- **11-04 — pass bank assembled.** 12 AWG drain wires of equal length to
  the "input → drain" bus bar; gate and source wires and the ballast
  resistors soldered; source wires to the "output → to source" bus bar;
  a 5KP12CA surge protector (TVS diode) between the bars; parts
  labelled; OPA994 amplifier on perfboard with its BNC input wired.
- **11-06 — H-bridge.** Assembly started; the gate-driver pin assignment
  fixed (mvp_architecture.md section 4); bridge bus bars ordered.

## Decisions (11-10)

- **Turn-off keeps the bridge closed:** the current goes down by
  freewheeling through the bridge's built-in diodes (~1.3 ms from
  100 A); the firmware never opens the bridge while current flows. (The
  pass bank's surge protector (TVS) does not conduct in this path.) A
  faster turn-off later = a blocking diode + a TVS across the bridge
  input (future_upgrades.md section 6; parts to be ordered).
- **Floor below the turn-on point:** while running, the controller's
  output never goes below ~90 % of the pass bank's dead zone (the range of
  OUT1 where no current flows yet), so steps from zero meet the 1 ms
  target.
- **Over-current trip at 100 A** (the supply's rating), on the measured
  current.
- **Setpoint ramps enforced in the firmware:** at most 200 A/ms up, at
  most 50 A/ms down (sudden steps overshoot or undershoot in this
  system) — except a target inside the ±0.5 A zero window, which is
  taken at once, so a step to zero turns the current off at the full
  freewheel speed (~1.3 ms from 100 A). *(Measured 11-12: in practice the
  controller winds the current down slowly near zero; firmware rev 5's
  zero snap fixes this, see below.)*
- **OUT1 dither:** the firmware flicks OUT1 up and down by one step very
  fast so that the average falls between steps. This recovers resolution
  lost to the ×8 amplifier in front of the gates.
- **DIO5 (fault input) not wired;** the Red Pitaya drives no current
  until it is enabled and then armed (arm switched on after enabling).
  Only the heartbeat (DIO6) always runs.

## Repo (MVP rewrite, brief section 11)

Steps 1–5 were done on 11-10: architecture documents, the computer model,
the firmware and its simulation tests (cocotb), the PC programs ("host
tools") and the documentation. The PC programs now notice when the
register server is not running and print the `deploy` command (the 11-03
problem), and `deploy` checks that the board comes up in its safe state.
Starting the firmware image and the server automatically at boot
(systemd) was **not** done: it would change what state the board starts
in — to be proposed separately if wanted.

## Step 6 — MVP firmware image (11-11)

The NUC is no longer available; Vivado 2025.1 (the chip-design software)
was installed on a lab laptop under WSL2 (Ubuntu 24.04) following README
"Setup B" (the libtinfo5 package had been revised to `6.3-2ubuntu0.3`;
the Linux "Self Extracting Web Installer" is needed, not the Windows one).

The build checks that every signal inside the chip arrives in time for
the next clock tick. WNS is the worst margin for "arrives early enough",
WHS the worst margin for "does not change too early"; a negative number
means the build fails. Signal paths that miss are "failing endpoints".

| Build | Result | Fix |
|---|---|---|
| 1 | WNS −2.169 ns, 232 failing endpoints: ramp limiter `slew_0` (−2.17), controller `pi_0` (−1.80), sequence logic `fsm_0` (−1.03), two marginal | ramp limiter split into a "measure" step and a "move" step; the controller's arithmetic spread over one more clock tick (sum, limits and floor comparisons each stored in between); the sequence logic's zero-window and trip comparisons stored before use; dither output stored before use |
| 2 | WNS −0.015 ns, 1 endpoint (`slew_0/acc`) | the ramp limiter now picks its ramp rate in the "measure" step and stores it |
| 3 | **WNS +0.072 ns, WHS +0.053 ns, 0 failing** | — |

Build 3 (from `aa2a92f1`) is `bitstreams/coil_servo.bit.bin`, checksum
(SHA-256) `3e9f15a3…f416`. **Next:** load it for the first time with arm
off and the power stage off, then bring-up per [bringup.md](bringup.md).

## First load of the MVP firmware image (11-11)

Board only: power stage off, arm off, nothing on the SMA inputs.
`deploy mot` loaded the image (69 ms) and its check afterwards passed:
IDLE, OUT1 = 0, bridge disabled, not armed, register map rev 3.
`check mot`: heartbeat ADVANCING, board settings UNKNOWN before (expected
after a load), `mot` settings after, **settings written and read back OK**
(all 32 settings words work on the hardware), OUT2/boost 0, dither on,
state IDLE, not armed, not tripped. Measured current at zero with nothing
on IN1: **+0.189 A** (≈ 1 mV, the Red Pitaya's own input offset; positive
is the safe direction for the over-current trip). Measure again with the
LEM sensor chain connected.

## Logic-board acceptance test with the MVP firmware (11-11) — latch fault found and fixed

Board only (power stage off). After a Red Pitaya power cycle the firmware
image was gone: no heartbeat (E1 pin 15 read 0.3 V instead of ~1.6 V
average), green LED (heartbeat) off, the latch held tripped — the safety
chain working as designed. Loaded the image again; heartbeat back.

- **Arm rule confirmed on the hardware:** arm jumper (pin 11) plugged in
  *before* `servo_enable` = 1 → stays IDLE, amber LED (bridge on) off;
  unplug and replug → RUN, amber on.
- **Fault: the latch did not stay tripped.** Pulling the heartbeat turned
  green and amber off, but red stayed off, and plugging it back brought
  amber back on by itself. Found with a multimeter: the latch chip
  (74HC74, half 1) trips when its clock input (CLK, pin 3) goes from low
  to high while D (pin 2) is held high — touching pin 3 with a probe
  tripped it — and the heartbeat timer's output 1Q̄ (CD74HC123 pin 4)
  rises when the heartbeat is lost, but **74HC74 pin 3 had no wire at
  all** (floating, ~0.4–0.6 V). Probably a wire that came loose after the
  11-03 test.
- **Fix:** wire CD74HC123 pin 4 → 74HC74 pin 3. Re-test: pull the
  heartbeat → green off, amber off, red on; plug it back → green on, red
  stays on, amber stays off. ✓
- **Design weakness to review (not fixed):** the latch reacts to the
  *moment* the heartbeat is lost, not to it *being* absent. Pressing
  reset while the heartbeat is still absent clears the latch, and when
  the heartbeat returns nothing trips it again, so the bridge switches on
  without another reset. A trip that reacts to the heartbeat being absent
  (the timer output wired to the latch chip's direct set/clear input,
  "asynchronous preset/clear") would prevent that. Until this is
  reviewed: **only press reset with the heartbeat present (green on), and
  only after stopping the servo.**
- Measured along the way: half 1 of the CD74HC123 is the heartbeat watch
  (pin 13 1Q = 3.3 V with the heartbeat, 0 V without; pin 4 1Q̄ the
  opposite); half 2 is unused.
- Corrected operating rule (also in all docs, commit 5493db89): stop the
  servo (`servo_enable` = 0, IDLE) before pressing reset — switching arm
  off alone does not stop a running servo.
- **Acceptance test passed** after the fix (all 8 steps of bringup.md 3a
  in the corrected order): arm before enable → stays IDLE; arm switched
  on after enabling → RUN; heartbeat pulled → red stays on (latched);
  plugged back → red stays, amber off; stop → IDLE; reset → amber stays
  off; enable again + arm switched on → RUN; stop → IDLE. The logic board
  passes with the MVP firmware.

## Loopback test with the MVP firmware (11-11)

OUT1 connected straight to IN1 with one SMA cable (IN1 high-impedance),
floor set to 0 for the test, logic board connected, no power stage. Run
from the lab PC.

- **Open loop (scale):** OUT1 set to 0 / 500 / 1000 / 1800 steps → IN1
  read −570 / 447 / 1459 / 3080 steps, ±1–2 steps. **Loopback factor
  2.026** (2.034 / 2.025 / 2.027 for each interval; August: 2.03). The
  −570-step (≈ −70 mV) reading at OUT1 = 0 is the output's own resting
  offset (≈ −35 mV, doubled because the cable is not terminated in 50 Ω;
  August: the same) — harmless in the MVP, because it sits inside the
  pass bank's dead zone.
- **Closed loop:** setpoint 819 steps ("20 A") → IN1 average 819.00
  (±1–2), OUT1 684; 2048 ("50 A") → IN1 2047.93 (±1), OUT1 1290 —
  matching the prediction (setpoint + 570)/2.026.
- **Over-current trip:** setpoint 4506 steps ("110 A") in closed loop →
  TRIPPED, bridge enable low; stop + `trip_ack` → IDLE. ✓
- **Finding — built-up correction in open loop:** the first switch from
  open to closed loop *tripped the board*. In open loop the controller
  kept building up its correction in the background (setpoint 0 against
  the −570-step offset), up to ~4400 steps, and that value reached OUT1
  the moment closed loop took over. On the real coil that would be a
  jump towards ~100 A. Fixed in the PC software the same day
  (`Board.write_cfg` switches `open_loop` from 1 to 0 only in IDLE or
  TRIPPED; `Board.stop()`; the step and sweep tools use it). A firmware
  fix (keep the built-up correction at zero in open loop) was queued for
  the next firmware build.

## H-bridge and current-sensor bench test (colleague, before integration, reported 11-11)

Bridge alone, switched by hand (logic board not connected): UCC5350 gate
drivers with RO-0515S isolated supplies (+5 V from an isolated bench
supply), 3.3 V logic; driver input IN+ high on the two switches meant to
conduct (one diagonal), IN+ low on the other two, all IN− low; the bridge
powered by a 5 V constant-voltage bench supply; 4 AWG welding-cable
jumpers with ring lugs between the transistors (the main cabling is 4/0 —
check how warm the 4 AWG gets before 100 A continuous).

1. Gate drivers, and check for a short circuit through the bridge
   (shoot-through): passed.
2. Q4 gate read 0 V (Q1 16 V), no load current: loose connection, fixed.
3. Resistor as load: Q1/Q4 on → +5 V, Q2/Q3 on → −5 V, all off → 0 V.
   Passed.
4. Coil + LEM LF 310-S current sensor (±15 V bench supply, 10 Ω burden
   resistor, AD8421 amplifier with gain G = 1 → 49.9 Ω → IN1): 1.12 A
   each way; voltage on the burden −35 mV (Q1/Q4) and −23 mV (Q2/Q3),
   i.e. ±6 mV around −29 mV. Reversal passed. Scale 5.4 mV/A against 5.0
   expected — within the uncertainty of a ±6 mV meter reading and a
   bench-supply ammeter at 1 A; calibrate with the Red Pitaya during the
   3 A bench run.

**Open finding — LEM zero offset: −29 mV on the burden ≈ −2.9 mA sensor
output ≈ −5.8 A of coil current** (datasheet: ±0.2 A class). Unchanged
with the AD8421 disconnected, after switching the ±15 V off and on, and
with the LEM turned round 180° → the offset is inside the sensor
(magnetised core, for example from coil current while the LEM was
unpowered, or a faulty sensor). **What this would mean for the servo as
built** (the firmware does not correct offsets): the controller would
deliver ~5.8 A more than the setpoint; the trip would sit ~5.8 A high
(bench: 8.6 A real, above the 3 A supply — it would never fire); the
current would never read inside the zero window (±0.5 A), so a stop
would hang in RAMP_DOWN, holding ~5.8 A of real current with the bridge
closed. **No powered runs with this offset.** Actions: demagnetise the
LEM (slowly decaying AC current through the coil it measures) or replace
it; a firmware setting to correct the IN1 offset was proposed for the
next firmware build.

## Register map rev 4 loaded (11-11, afternoon)

Rev 4 adds settings for the IN1 and IN2 zero offsets (prompted by the
−5.8 A LEM reading), a build-ID status value and the open-loop fix (the
controller's built-up correction is kept at zero in open loop). Built
from `103a127` on the laptop (the first attempt stopped on an error in
the build-ID script, fixed): **WNS +0.156 ns, WHS +0.074 ns, 0 failing**.
Loaded; `check mot`: **firmware: register map rev 4, commit 103a127**
(no uncommitted-changes flag), settings written and read back OK, IDLE,
not armed, not tripped. Zero reading +0.306 A, offsets not calibrated
yet — run `python -m coil_servo_host.zero mot --write --apply` with the
real sensor chain connected and powered and no current flowing.

## Test 1 — gate stage (11-11, no high current)

Setup (test_campaign.md): OPA994 amplifier board on its own 5.184 V bench
supply, OUT1 → amplifier BNC input (no 50 Ω terminator) with a tee to
scope CH1, ×10 probes on the gates of pass transistors #4 (CH2), #1 (CH3)
and #10 (CH4), all probe grounds on the source bar; high-current side
open. Open loop, OUT1 swept over 0–4474 steps (raw data:
logs/t1_gate_sweep.json, logs/t1_gate_decay.npz).

- **Wiring fault found first:** the gates stayed at ~0 V. The amplifier's
  supply sat 5 V too low relative to the source bar (pin 7 +0.39 V, pin 4
  −5.1 V): its − terminal was not connected to the source bar, and its +
  terminal was effectively at ground (the source bar is earthed through
  the scope). Fixed: 5 V supply − → source bar, supply floating from
  earth. Then pin 7 +5.18 V, pin 4 0 V.
- **OUT1 at the amplifier input: 285.4 µV per step, offset −81 mV at
  OUT1 = 0** → OUT1 is **not terminated in 50 Ω** and swings 2.34× the
  nominal 122 µV per step (the Red Pitaya's output without a load ≈
  2.34 V full scale).
- **Amplifier gain 7.94** (gates #4 and #10; design 7.98). Gate #1 read a
  constant ~0.1–0.15 V low on the replacement probe and agreed within 2 %
  at full bandwidth — the probe, not the circuit (one amplifier output
  feeds all gates).
- **Highest gate voltage 5.17–5.18 V** (the amplifier's 5.184 V supply;
  its output reaches its supply voltage), reached from ~2550 OUT1 steps.
  As wired, the firmware's OUT1 limit (clamp, 4474 steps, computed for a
  terminated OUT1) never acts; the amplifier's supply caps the gate at
  ~5.2 V (model: bank ≈ 190–200 A at that gate voltage).
- **No oscillation:** at 312 MS/s and full bandwidth: input 2 mV rms,
  gates 4–6 mV rms, only 1–5 mV tones at 125 MHz (the Red Pitaya's clock)
  and its multiples.
- **Safe if the amplifier loses power:** the gates fall together to
  ~0.1–0.2 V, τ ≈ 4.5 ms (the amplifier output follows its own supply as
  it discharges), below 0.5 V within ~8.5 ms.
- **Consequences:** with OUT1 unterminated one step ≈ 2.27 mV of gate
  voltage ≈ 0.27 A of bank current (model), the dead zone ends at ~1830
  steps, and the configured floor (3234) and clamp (4474) are wrong for
  this wiring. Recommendation: fit a 50 Ω BNC feed-through terminator at
  the amplifier input (halves the volts per step and the offset, and
  matches `out1_terminated = true`), repeat the sweep, then put the
  measured OUT1 scale and offset and the 5.18 V gate maximum into
  channels.toml.

### Test 1b — repeated with the 50 Ω terminator (11-11)

50 Ω BNC feed-through terminator fitted at the amplifier input, sweep
repeated (logs/t1_gate_sweep_terminated.json):

- **OUT1 at the amplifier input: 142.6 µV per step (1.168 V full scale),
  −41.3 mV at OUT1 = 0.** Terminated, but the Red Pitaya output still
  runs ~17 % above the nominal 1 V.
- **Amplifier gain 7.93**, highest gate voltage 5.18 V (unchanged).
- **Into channels.toml (all profiles):** `out1_fs_volts = 1.168`,
  `out1_offset_volts = -0.0413`, `gain_opamp = 7.93`, `V_opamp_max = 5.18`.
  The PC software now calculates from these: **dead zone 3385 steps, RUN
  floor 3047, clamp 4144 (100 A)**, overall system gain (in the model's
  normalised units) 5.56. The clamp now sits below the amplifier's supply
  limit (~4870 steps), so it acts again.
- **Controller gains rescaled** to the new system gain: kp 0.0900, ki
  2713 /s (bench profile: 3.6852 steps/A, 111.14 steps/A/ms). Still based
  on the loopback test — set them from the measured step response in
  Test 3.
- One OUT1 step is now ≈ 0.14 A of bank current (model): more than the
  ±0.1 % band at 50 A, so **`dither_enable` stays on** for every hold.
  The simulation tests now run the 50 A settling checks with dither on.

## Test 2 — sensor chain repair and zero calibration (11-11)

Setup: LEM LF 310-S on ±15 V (floating bench supply, connected to ground
only through the BNC cable shield to the Red Pitaya and scope), 10 Ω
burden resistor, AD8421 amplifier on a Perma-Proto board, its output →
IN1 with a tee to scope CH1; IN2 on a 50 Ω terminator; no current
anywhere (high-current supplies disconnected).

- **First reading: IN1 stuck at full scale** — the amplifier output sat
  at +12.6–13.7 V (its upper limit; IN1 was unplugged at once, and the
  Red Pitaya input was checked afterwards and is undamaged: +1.3 mV with
  nothing connected).
- **Fault 1: the reference pin REF (pin 6) was floating.** It reached
  ground only *through* the decoupling capacitors (a capacitor passes no
  DC) and read +4.6 V. Fixed: plain wire from pin 6 to GND. (Jeff's
  drawing shows the capacitors in line with the REF wire, which invites
  this; correct: REF wired straight to GND, capacitors from pin 8 and
  from pin 5 to GND.)
- **Fault 2: the gain-resistor pins (R_G, pins 2–3) were joined** by
  solder (on the replacement chip; the first chip probably had the same
  fault, so it is likely fine — kept as a spare). With R_G shorted the
  gain is effectively unlimited: the output sat at one supply limit or
  swung from one limit to the other. Fixed: solder bridge removed.
- **The output resistor was 47 kΩ** (yellow-violet-orange), not the
  49.9 Ω of the design — that would have read IN1 4.5 % low (9 % with the
  scope teed in) and filtered the signal at tens of kHz. Replaced with
  49.9 Ω.
- **The amplifier inputs are swapped** compared with the drawing (−IN on
  the LEM/M end, +IN on GND): this only flips the sign. Left as is; the
  sign is checked with the first real current in Test 3 (turn the LEM
  round if needed).
- **The LEM is not magnetised:** with the chain repaired the amplifier
  output is −0.2 mV (≈ −0.04 A) with the LEM powered (its supply current
  changes when it is plugged in). The "−29 mV / −5.8 A offset" of
  11-10/11-11 came from the wiring faults, not the sensor.
- Noise at the amplifier output (scope, 20 MHz bandwidth): ~1 mV rms ≈
  0.2 A rms at the scope's resolution — to be checked again with the
  board's own recording (the board's input averages over 128 samples per
  controller cycle).
- **Zero calibration** (`zero mot --write --apply`, `zero mot_bench
  --write`): IN1 +8.6 steps (+0.21 A — the Red Pitaya's own input offset
  plus the amplifier's −0.2 mV), IN2 +6.3 steps (+0.15 A, on the 50 Ω
  terminator — measure again once the control computer's analog output
  is connected). Stored in channels.toml [mot] and [mot_bench]; after
  loading, `check mot` reads −0.06 A (≈ 2 steps). **Pass.**

## Test 3 — bench supply, one coil (11-11, first session, profile mot_bench)

Setup: bench supply 5.0 V / 3.0 A in constant-current mode in place of
the SE-600-5, one MOT coil between bridge points A and B (4/0 cable), LEM
on its lead (one pass through the sensor), pass bank and OPA994 gate
stage as in Test 1 (the OUT1 50 Ω terminator stays). Scope ×1 BNC on all
four channels: CH1 IN1 tee, CH2 drain bar, CH3 point A, CH4 bridge enable
(DIO1 tee). Logic-board latch reset with the heartbeat present.

- **The current sign was reversed** (amplifier inputs swapped compared
  with Jeff's drawing): the first open-loop scan read a growing
  *negative* current and stopped by itself. Fixed by turning the LEM
  round; zero offsets measured again.
- **Ground loops: do NOT earth the bench supplies.** With the coil,
  amplifier and ±15 V supplies connected to earth (and the scope grounds
  on the source bar), part of the coil current returned through earth
  and the BNC shields: IN1 read ~2× low and with the wrong sign on the
  scope, and the bridge switched off after ~8 s at ~1 A (enable still
  requested — most likely ground noise tripping the latch). With all
  supplies floating (earthed only through the scope) the board, the scope
  and the supply's ammeter agree, and a 5-minute open-loop hold at ~1.1 A
  ran without the bridge switching off.
- **IN1 reads ~10 % low:** the firmware uses the raw measurement steps
  (the Red Pitaya's factory calibration is not applied). At a 1.09 A
  supply reading the board read 0.99–1.02 A (drifting up as the
  transistors warmed); the scope (5.50 mV at 5 mV/A) gave 1.10–1.15 A.
  New measured value `in1_fs_volts = 1.10` (PROVISIONAL ±3 %) in [mot]
  and [mot_bench] and in the model: full scale I_FS = 220 A, 26.9 mA per
  step. Refine in a closed-loop hold (constant current; read the supply
  at leisure). Zero offsets measured again on the new scale: IN1 +9.7
  steps, IN2 +5.5.
- **Pass bank at bench currents (open loop, logs/t3_open_loop_scan2.json,
  t3_hold_3850/3990.json):** current starts at ~3540 steps (gate ≈
  3.65 V; model 3385), ~1.1 A at 3850, ~2.2 A at 3990 when cold — and
  3990 tripped at 2.8 A once the transistors were warm (at a fixed gate
  voltage the current rises as they heat up). Roughly square-law near the
  turn-on point; ~0.008 A per OUT1 step at 1–2 A, against the model's
  0.136 A per step (the model's transistor gain g_fs is the value for
  100 A). (The scan's absolute amps were taken before the ground-loop fix
  and are ~2× low; the turn-on point and the shape are valid.)
- The supply voltage drops by ~0.24 V at 1 A before point A (the bench
  supply's leads, ≈ 0.24 Ω). The more careful measurements of 11-12 give
  ≈ 0.2 Ω (DC, 0.41 V drop at 2 A) and 0.17 Ω (fitted to the 10–20 kHz
  ringing). All three are the same leads; use ≈ 0.2 Ω.
- **Firmware trip confirmed in passing:** open loop at 3990 steps with
  warm transistors → TRIPPED at 2.8 A, bridge opened, acknowledged with
  `trip_ack`.
- **[mot_bench] updated:** `out_clamp_counts` 4100 (room for 2.5 A and
  the 2.9 A trip test), `u_floor_counts` 3450 (~90 steps below the
  turn-on point, ~0 A there). Controller gains matching the measured
  slope would be kp 62.5 / ki 1885 steps/A (per ms for ki); tuning starts
  at 1/4 of that (kp 15.6, ki 471) and doubles while the step response
  stays clean. (Four times the rounded start values gives 62.4 / 1884,
  the numbers in the 11-12 tuning table: the same setting, rounded
  differently.) (A quick check with the model and a hand-adjusted pass
  bank was not usable — the hardware decides.)
- Not done yet: closed-loop steps (the first attempt timed out waiting
  for arm to be switched on), trip test in closed loop, freewheel
  recording, flips, heartbeat trip, hold noise, duty cycle.
- **Model follow-up (open):** the model's pass bank (dead zone 3385
  steps, 0.136 A per step) does not match the bench measurement near the
  turn-on point (start ~3540, ~0.008 A per step, square-law). The
  firmware simulation therefore uses the [mot_bench] file's protection
  values but the model's floor and controller gains. Fit the model's
  pass bank near turn-on to the measured scan before using it to tune
  the bench gains. [mot] gains (in normalised units) rescaled to the
  220 A IN1 scale (kp 0.09897 / ki 2984.8 = unchanged steps per amp).

**Next session:** power up; load the firmware image (`deploy mot_bench` —
the image is lost when the Red Pitaya is powered off), `check mot_bench`,
measure the zero offsets again, then 3d closed-loop steps from 1 A,
reading the supply during a steady closed-loop hold to finalise
`in1_fs_volts`.

## Test 3 — bench supply, second session (11-12, profile mot_bench)

Same setup as 11-11. **Correction to 11-11:** the coil is not one of the
MOT pair but a **temporary test coil**: one racetrack, 12 turns of
4 × 2 mm hollow copper (2 mm bore), 70 × 210 mm, 12 mm inner corner
radius, lying on the steel optical table. Calculated from its shape
(Neumann integral): **L ≈ 45 µH, R ≈ 25 mΩ** (model and channels.toml
updated; the 9 µH used until now was a guess for one MOT coil). Arm and
flip requests come from software (pulses of the `dio_invert` setting:
bit 1 = arm, bit 0 = flip request), so the arm jumper stays out. Plots
and step-by-step detail: [benchtop_test_log.md](benchtop_test_log.md)
(made from the session's log page, `logs/testlog/benchtop_log.html` (lab
PC only -- logs/ is not in git; shown by the `test-log` entry in
`.claude/launch.json`)); data in `logs/t3_*`.

- **Start-up:** load + check clean; offsets IN1 +0.256 A, IN2 +0.157 A.
  **Loading the firmware trips the logic-board latch** (the heartbeat
  pauses while the image loads): the first closed-loop step gave no
  current and no amber light. Reset the latch after every firmware load
  or Red Pitaya reboot (now in bench_supply_test.md step 3).
- **IN1 scale (3c), closed-loop 2 A hold, 60 s:** board 1.988 A, bench
  supply ammeter 2.02–2.03 A, scope 2.086 A (≈ 3 % high).
  **`in1_fs_volts` = 1.12** (was 1.10 provisional): I_FS = 224 A, 27.3 mA
  per step. Agrees with the first ammeter estimate of 11-11 (1.118). [mot]
  gains (normalised units) rescaled to keep the same steps per amp (kp
  0.1008, ki 3038.6). Commit d70a45d6.
- **Controller gains (3d), closed-loop 1 A steps, ratio ki/kp kept
  fixed:**

  | kp (steps/A) | ki (steps/(A·ms)) | 90 % | 98 % | hold noise | verdict |
  |---|---|---|---|---|---|
  | 15.6 | 471 | 1.41 ms | 1.85 ms | 40 mA rms | clean, slow |
  | **31.2** | **942** | 0.73 ms | 1.04 ms | 40 mA rms | **chosen** |
  | 62.4 | 1884 | 0.38 ms | 0.53 ms | 86 mA rms | 21 kHz oscillation |

  2 A step at the chosen gains: 98 % in 0.65 ms, no overshoot. A 2.5 A
  step tripped once (coil lifted, see ringing below); on the table it
  overshot by +2.8 %, with 28 single samples above the 2.79 A trip level
  → **bench `I_rated` lowered 2.5 → 2.2 A**.
- **Over-current trip (3e), 2.9 A step (`I_rated` temporarily 2.9; the
  trip level is set from the supply's 3 A limit, not from `I_rated`, so
  it stayed at 2.79 A):**
  fired at ≈ 2.85 A (set to 2.79 A), both outputs to zero, the current
  freewheeled to zero in ≈ 225 µs, bridge open, TRIPPED; the supply's
  current-limit lamp stayed off. `trip_ack` + stop → IDLE.
- **Turn-off path (3f), open loop, OUT1 → 0 at once:** the gate falls
  from 4 V to 0 in ≈ 4 µs; the drain bar jumps to ≈ 5.9 V (supply + one
  diode drop, briefly ringing to 7.4 V) and stays there exactly as long
  as current flows; the current falls in a straight line. **Confirmed:
  the current freewheels through the bridge's built-in diode; the surge
  protector (TVS) is never reached.** Slope 12 mA/µs on the table,
  8 mA/µs lifted 13–15 cm (slower in free air: eddy currents in the
  table absorb energy) → ≈ 0.37 V across the 45 µH coil. Setpoint → 0 in
  closed loop is slower, because the controller winds OUT1 down
  gradually: the current falls to a few tenths of an amp in ~0.3 ms, and
  the last part takes ~1.3 ms in all (measured later, see the 2 A pulse
  in the next section). The "snap OUT1 to zero" change (firmware rev 5)
  addresses that. Leakage through the warm bank at the RUN
  floor: 45–60 mA at 3450 steps.
- **Flips and the switch-over gap (3g), ±2 A:** flips happen only inside
  the ±0.49 A zero window (current at the moment of the polarity change
  +0.13 / −0.29 A), ≈ 300 µs from 2 A, no timeout. **Bridge enable drops
  1.02–1.04 µs before and returns 0.98–1.02 µs after each polarity
  change** — the short gap when all four bridge switches are off (125 ×
  8 ns; measured at the Red Pitaya pins; the bridge transistors' gate
  voltages still to be checked with a floating probe). Flips need arm to
  be held on (`if (flip_req && armed)`): the dashboard's software-arm
  button only switches arm on briefly, so flips after it are ignored —
  passed to the dashboard session.
- **Heartbeat-latch trip (3h), 2 A:** the bridge opens at once; the coil
  discharges into the supply through three diodes (up from ground
  through the pass bank's built-in diodes, drain bar ≈ −0.5 V) and is at
  zero in ≈ 10–13 µs (≈ 6.7 V across 45 µH). **Supply: +2.5 to +3.2 V
  spike for ≈ 10 µs** (inductance of the leads), then ≈ +0.4 V because
  the voltage drop of 2 A on the leads disappears (DC supply 5.06 V at
  0 A, 4.65 V at 2 A: ≈ 0.2 Ω of supply leads). The controller saw zero
  current and drove OUT1 to the clamp until it was stopped — hence the
  stop-before-reset rule. A 2.5 A attempt tripped the firmware first.
- **Hold noise and dither (3i), 2 A:** dither on or off makes no
  difference on the bench (one OUT1 step ≈ 8 mA here; the real test is
  at 100 A, ≈ 0.14 A per step). The average current is very stable: the
  averages of four 16.8 ms recordings agree to ±0.2 mA (0.01 %).
- **Open finding — 10–20 kHz ringing of the bench power stage.** With
  the coil lifted (and slightly also on the table) the current rings by
  itself: ±0.08–0.2 A, drain ±0.5–0.8 V (exactly L·dI/dt), supply
  ±0.06 V.
  - Not the controller (the same at half the gains and in open loop).
  - Not the gate stage (a pass-transistor gate pin is steady to ±0.1 mV).
  - Not the varistor across the coil (Digi-Key 495-6502-ND; removed → no
    change) — there is no capacitor on the H-bridge.
  - No current goes around the bank: the supply ripple is exactly the
    supply leads' 0.17 Ω + 3.6 µH times the oscillating current, so
    supply, bank and coil carry the same current — **the bank changes its
    current at a fixed gate voltage** (it depends on drain voltage and/or
    chip temperature near the turn-on point, where the bench runs a bank
    built for 100 A).
  - Frequency ∝ 1/√I: 20 kHz at 0.71 A, 15 kHz at 1.23 A, 11.5 kHz at
    1.93 A; drain swing always ≈ 1.6 V peak-to-peak (limited by the
    freewheel diode).
  - Coil flat on the table: gone (82 mA rms, no tone) — damping decides.
  - **Matters for the MOT:** similar ringing would be a few-percent
    field ripple at 10–20 kHz, far faster than the controller can
    correct. Next: a series 3 Ω + 10 µF damper across the coil (being
    fitted), then the cause (transistor data near the turn-on point)
    before the 100 A build.
- **3j (duty cycle without water cooling) moved to the 100 A build:**
  ≤ 15 W on the bench.
- Firmware and settings: bench `I_rated` 2.2 A; zero offsets measured
  again after an unplanned Red Pitaya reboot (IN1 +0.289 A, IN2 +0.179 A
  — the input drifts while it warms up after power-up).
- **Extra checks, coil flat on the table (second Claude session; data
  `logs/t3b_*`):**
  - Closed-loop steps 0 → 0.5 / 1.0 / 1.5 / 2.0 / 2.2 A: 98 % in 1.36 /
    0.95 / 0.70 / 0.62 / 0.63 ms (faster at higher current: the bank's
    gain rises), overshoot 25–41 mA = at the noise level; no sample above
    the trip at 2.2 A. Steps down 2.2 → 1.0, 1.0 → 0, 2.0 → 0 A: 98 % in
    0.34 / 1.22 / 1.49 ms (a fast drop, then a slow tail while the
    controller's built-up correction winds down); leakage at the floor
    leaves 14–18 mA at setpoint 0.
  - 10 min hold at 2.0 A: average 1.9958 A; after the first (warm-up)
    minute the averages over each time window agree within ≈ 0.5 mA
    (0.025 %); resolution per point ±5.8 mA. OUT1 fell from 3948 to 3927
    steps as the bank warmed (the controller removed ≈ 0.2 A of drift
    that would have appeared in open loop).
  - 20 flips at ±1 A: 20/20, no timeout (`TIMEOUT_HOLD`), no trip; from
    request to RUN with the new polarity ≈ 1.1 ms (timed by the PC).
- **Damper test — an RC damper across the coil cures the ringing.**
  1 Ω + 2.2 Ω (1/4 W metal film) in series with 10 µF (non-polarized)
  across the coil terminals, next to the varistor (≈ 3.2 Ω ≈ the coil's
  impedance at 12 kHz). Coil lifted (where it rang reliably), open loop:
  strongest tone between 5 and 30 kHz 43 / 76 / 124 mA at 0.7 / 1.2 /
  1.9 A without the damper → 2.5 / 2.8 / 4.9 mA (noise) with it; drain
  ripple 326–366 → 63–107 mV rms. Closed-loop 2 A hold: 6.5 mA rms in
  100 µs averages (15–21 mA before). A small tone at ≈ 72 kHz (≈ 10–14 mA)
  appears with the damper, probably its capacitor with the wiring. **For
  the MOT build: plan an RC damper across the pair** (R ≈ ωL at the
  ringing frequency for 16 µH, C large enough to pass it, resistor rated
  for the 100 A pulses) and confirm on the real coils.

### Proposed damper for the MOT pair (worked out 11-13)

Same layout as the bench damper (a resistor in series with a capacitor,
across the coils), but **new values**, because the MOT pair has about a
third of the test coil's inductance (16 µH against 45 µH). Also in
[servo_interface_brief.md](servo_interface_brief.md) section 1c.

- **How the values are chosen.** The resistor should be close to
  *critical damping* of the coil and damper together, R ≈ 2√(L/C), so the
  coil current does not swing back through zero after a switch-off. It
  should also be close to the coil's own impedance at the ringing
  frequency, R ≈ 2π·f·L, which takes the most energy out of the ringing.
  The capacitor's impedance at that frequency should be well below R, so
  it lets the ringing through. Check on the bench values: 45 µH with
  10 µF needs 4.2 Ω for critical damping; the 3.2 Ω used was a little
  low, which is why the current swung to −0.2 A after a switch-off.
- **Values for 16 µH:** with 10 µF, R ≈ 2–2.5 Ω (critical 2.5 Ω), fine if
  the coils ring above ~15 kHz; **proposed: 22 µF with ≈ 1.5–1.7 Ω**
  (critical 1.7 Ω), which also works at lower frequencies. The MOT pair's
  ringing frequency is unknown, because the cause of the ringing is not
  understood; if it scales like a coil-and-capacitor resonance, 16 µH
  would ring about 1.7× higher than the test coil (roughly 20–35 kHz at a
  few amps).
- **Ratings matter more than the values.** In normal running the damper
  is hardly stressed (the capacitor blocks the steady current; flips
  switch at near-zero current). The hard case is the bridge opening at
  100 A (a safety-board cut-off): the bridge's built-in diodes limit the
  coil voltage to about 7–8 V, so most of the energy (≈ 0.08 J) goes into
  the supply, but the damper carries about V/R ≈ 3–5 A for ≈ 0.2 ms,
  roughly 5 mJ per event. So: a **5–10 W, pulse-rated, non-inductive
  resistor** (thick film or metal oxide; the bench's 1/4 W resistors
  would be marginal), a **film capacitor rated ≥ 50 V** (12–13 V spikes
  were seen at switching), and **short leads** (the 72 kHz tone came from
  the damper's own wiring).
- **Check in Test 4, before high current:** (1) measure the mounted coil
  pair's impedance at 10–30 kHz with an LCR meter (the chamber and mounts
  change it) and adjust R toward it; (2) at 0.5–2 A with OUT1 fixed and
  no damper: do the coils ring, and at what frequency? (3) the same with
  the damper; (4) check that the current does not swing back through zero
  after a switch-off.
- **Metal near the coils** (the steel table here; the stainless chamber
  and mounts on the experiment) damps the ringing a little, but not
  reliably (on the table it came back once at 13.6 kHz), so fit the
  damper regardless. Mechanical contact is fine; the coil conductor must
  not touch grounded metal electrically (a second path to ground, like
  the day-1 ground loops).
- **Follow-up 11-13, the "72 kHz" and "290 kHz" tones:** in the 50-run
  data (sensor inside the coil–damper loop), the tones near 290 kHz and
  the broad hump at 30–150 kHz are the same size with 2 A flowing and
  with no current, so they are measurement, not current. No 72 kHz tone
  stands out in the coil current any more; if it exists, it circulates in
  the damper's own wiring. *Refined by the clean zero-current reference
  (bridge open) later on 11-13, see "Zero-current noise reference" below:
  the lines near 290 kHz do grow with the load current, but they are not
  coil current (the coil voltage stays steady); most likely pickup from
  the bench supply's switching.*

## LEM moved inside the coil–damper loop; turn-off measurements (11-12, late)

Plots and details: [benchtop_test_log.md](benchtop_test_log.md), section
"LEM inside the coil–damper loop". Data `logs/t3c_*` (lab PC).

- **Why:** the damper and varistor were connected at the coil terminals,
  and the LEM sat on the bridge side of that connection, so it measured
  coil + damper + varistor current. Current going round coil → damper →
  coil (after a turn-off, or when the bridge opens) was invisible.
  **Moved**, so the order is now bridge → damper/varistor connection →
  LEM → coil. For the MOT, the planned LEM position between the two coils
  is automatically inside this loop, as long as the damper goes across
  the whole pair.
- **Sign and control:** sign unchanged (OUT1 3800 steps → +0.65 A).
  Closed-loop 1 A step: 90 % in 0.61 ms, 98 % in 0.81 ms, +2.4 % — the
  coil–damper resonance in the measured signal does not hurt the
  controller.
- **Turn-off with the bridge closed (open loop, OUT1 → 0), 2 A:**
  freewheel at ≈ 0.5 V across the coil, ≈ 12–13 mA/µs, 2 A → 0 in
  ≈ 170 µs (two runs agree; the scope and the board's own recording
  agree). Then a **≈ −0.2 A swing for ≈ 100 µs** (the coil ringing once
  into the damper capacitor), gone by ≈ 200 µs. Switching the bank off
  makes the drain bar ring up to **12.1 V** for a few µs (≈ 500 kHz),
  just under the bank's surge protector (5KP12CA TVS ≈ 13.3 V) — at
  100 A expect the TVS to cut it off; look at it on the scope in Test 4.
- **Bridge opened at 2 A** (firmware trip with `trip_timeout` = 0 and
  `trip_level` 1 A, temporarily; electrically the same as the heartbeat
  trip, but without the controller's built-up correction running away):
  the coil voltage jumps to ≈ 6 V = 2 A × 3.2 Ω, current zero in ≈ 25 µs,
  swings to ≈ −0.3 A, gone by ≈ 150–200 µs; the energy goes into the
  damper, not the supply. 12.7 V / 0.4 µs spike on point A (wiring
  inductance). The morning's "gone in ≈ 10 µs" (3h, no damper, LEM
  outside the loop) no longer describes the coil. At 100 A the bridge's
  built-in diodes hold the coil at ≈ supply + 1.5 V and most of the 0.08 J
  goes into the supply; size the MOT damper near critical damping
  (R ≈ 2√(L/C)) so the current does not ring back through zero.
- **Unexplained 5 A surge.** During a closed-loop 2 A hold, while waiting
  to pull the heartbeat, the firmware over-current trip fired; the scope
  shows ≈ 4.7–5 A with the bridge closed, the supply sagging from 4.3 to
  1.9 V (the 3 A supply at its current limit, its output capacitors
  supplying the rest), then the trip and a normal freewheel. The latch
  reset had been pressed before the LEDs were read. **Latch check
  afterwards** (supply output off, RUN at setpoint 0 with OUT1 driven up
  to the clamp): DIO6 pulled for ≈ 2 s → red on, amber off; plugged back
  → red stays on, amber stays off ✓ (a quick-flick test was not done).
  Cause unknown: reset pressed while still in RUN (with the controller's
  built-up correction at its maximum), a heartbeat loss too short for the
  pull test, or a disturbance on the measurement. **Underlying weakness:
  the firmware cannot see the latch** — when the latch opens the bridge,
  the controller drives OUT1 up to the clamp, and any reset or glitch
  closes the bridge again at full drive. Proposed (not done): latch
  output → DIO5, with the firmware stopping and clearing its built-up
  correction when it sees it, and/or stall detection (OUT1 at the clamp
  and the current far below the setpoint for > ~1 ms → stop). Until
  then: stop the servo before pressing reset.
- **Noise on the raw current traces** is pickup on the 5 mV/A IN1 cable
  (≈ 3 mV rms, with the scope's 20 MHz limit already on), white: 575 mA
  rms raw → 36 mA rms in 50 µs averages with the current at zero. The
  real steadiness is the board's averaged reading (6.5 mA rms per 100 µs
  at 2 A; average within 0.5 mA over 10 min).
- **Closed-loop 2 A square pulse (8 ms):** rise from 10 to 90 % in 360 µs,
  98 % at ≈ 0.56 ms, +2 % (limited by the controller; the supply could do
  it in ≈ 20 µs). Fall: 2 → 0.35 A in ≈ 130 µs, then a **slow tail while
  the controller winds down**: 10 % at ≈ 0.43 ms, 2 % at ≈ 1.3 ms (the
  built-up correction unwinds at ≈ 280 steps/ms through the range where
  the bank turns on). Present with the LEM inside or outside the loop.
  **Proposed firmware option "snap OUT1 to zero"** (OUT1 → 0 and the
  built-up correction → floor when the setpoint enters the zero window):
  turn-off at the natural freewheel rate; works with the present hardware
  (the bridge stays closed). Separate from the fast turn-off with the
  bridge opened, which needs the TVS + blocking diode. Not built yet.
  **Decided (user, 11-12): trigger the snap only on a zero setpoint**,
  not anywhere in the ±0.5 A zero window, so currents below 0.5 A can
  still be held. For the analog IN2 input, "zero" needs a threshold of a
  few steps (IN2 reads ±1–3 steps of noise at 0 V), to be set from the
  measured IN2 noise. Expected side effect: the ≈ −0.2 A damper swing
  comes back on the bench (≈ V_f/√(L/C)); a near-critically damped MOT
  damper removes it.
- **Estimates for 100 A on the MOT pair:** freewheel turn-off ≈ 1.3 ms
  (1.0–1.7 ms; t = (L/R)·ln(1 + I₀R/V_f), V_f ≈ 0.6–1.0 V, R ≈ 8–12 mΩ;
  the same formula gives 165 µs for the bench against 170 µs measured);
  bridge opened ≈ 0.2 ms; a future turn-off with a 30 V limit ≈ 50 µs;
  rise 0 → 100 A ≈ 0.4 ms as the hardware limit, ≥ 0.5 ms with the
  200 A/ms firmware ramp, depending on how much voltage headroom the bank
  has at 100 A with the 5.18 V gate maximum (Test 4). Coil current only;
  eddy currents in the chamber come on top.

## Register map rev 5: zero snap — built, loaded, tested (11-12, evening)

**What:** the firmware now removes the slow turn-off tail in closed loop
(above). Zero snap: when the setpoint is zero, the pass transistors are
switched fully off at once. In detail: in RUN, closed loop, when the
requested setpoint, averaged over one controller cycle, is within
`snap_thresh` of zero (setting word 23; control-word bit 21
`snap_enable`), OUT1 is forced to exactly 0 and the controller's built-up
correction is kept at zero: the bank switches fully off at once and the
coil freewheels with the bridge closed. One step of hysteresis (it
releases one step further from zero than it engages); status bit 14
`snapped`. channels.toml: `snap_zero_enable = 1`, `snap_zero_amps = 0.1`
(4 steps, PROVISIONAL) for `mot` and `mot_bench`. Only a zero setpoint
triggers it (decided 11-12), so small currents can still be held. Works
with the present hardware; separate from the fast turn-off with the
bridge opened, which needs the TVS + blocking diode.

- **Simulation** (the firmware code simulated together with the model,
  `sim/tb_coil_servo_top.py`, 5 new tests, 237 tests pass): bench model
  2 A → 0.1 % in 108 µs instead of 1.74 ms; MOT model 80 A unchanged
  (1.1 ms — there the controller already gets the bank off fast); the
  rise afterwards matches a rise from rest; the hysteresis works; ±8
  steps of IN2 noise do not switch it on and off; it does nothing in IDLE
  and in open loop; flips unaffected.
- **Build** on the lab laptop (`scripts/lab-build.sh`, clean, from
  `275081b`, firmware code identical to `704fd89`): **WNS +0.003 ns, WHS
  +0.042 ns, 0 failing** — timing met, but the margin is thin (rev 4:
  +0.156 ns); watch it on the next change. 685 496 bytes, checksum
  (SHA-256) `f3533cc8…17aa`. Copied to the Drive folder "RP servo files"
  (rev 4 kept as `coil_servo_v1.bit.bin`) and to `bitstreams/` (rev 4
  archived as `coil_servo_rev4_20261111.bit.bin`).
- **Loaded** (`deploy mot_bench`): check reports rev 5, commit 275081b,
  settings read back OK, snap on (0.109 A). Zero offsets measured again:
  IN1 +0.237 A, IN2 +0.166 A. (Remote-desktop note: the laptop session
  loses the Shift and Ctrl keys, so the build and the copy are two
  scripts with names that need no Shift.)
- **Hardware, closed-loop 2 A pulse** (board recording; the scope refused
  connections this time):

  | | rise to 98 % | fall to 10 % | fall to 2 % | undershoot |
  |---|---|---|---|---|
  | snap off | 0.55 ms | 0.48 ms | **1.55 ms** | −0.03 A |
  | snap on (4 runs) | 0.55–0.60 ms | 0.13 ms | **0.14 ms** | −0.16 to −0.20 A |

  The fall now runs at the freewheel rate all the way to zero; the dip is
  the damper capacitor ringing back once (≈ V_f/√(L/C)), as predicted — a
  near-critically damped MOT damper removes it. Rise unchanged.
- **Hardware checks:** setpoint 0 and 2 steps (55 mA) → snapped on every
  reading; 10 steps (0.27 A) → not snapped, the controller holds
  0.275 A; analog IN2 input at 0 V → snapped on 3000/3000 readings (no
  switching back and forth) — but IN2 read −3 steps after the offset
  correction, only 1 step inside the threshold: measure the IN2 noise and
  drift with the control computer connected and set `snap_zero_amps`
  from that; `snap_enable` 0 → OUT1 back at the floor.
- **Repeated after a Red Pitaya power cycle** (firmware loaded again,
  latch reset, zero offsets IN1 +0.237 A / IN2 +0.150 A): 2 A pulse, fall
  to 2 % in 1.31 ms (snap off) against 0.14 ms (snap on), rise unchanged,
  undershoot −0.19 A; the threshold, IN2 and snap-off checks pass again
  (0.27 A held at 0.272 A).
- **IN2 noise at 0 V** (terminated input, 20 raw recordings of 131 µs
  over 20 s, after the offset correction): 4.1 steps rms per sample (why
  single status readings of `sp_active` show ±5), but the 1 µs averages
  that the snap uses have a standard deviation of 0.43 steps, average
  +0.75 (the offset setting only takes whole steps, which leaves ~0.7),
  maximum +2.45 — none of 2560 above the 4-step threshold. Margin
  ≈ 1.5 steps to engage, 2.5 to release. Measure again with the control
  computer driving IN2 before Test 4.

## Test 3g, continued — full field reversal, +2 A ↔ −2 A (11-13, second Claude session)

*(First written up as "Test 3e"; in the benchtop log it is part of section
3g, flips. "3e" there is the over-current trip. The data files keep their
`t3e_*` names.)*

The control computer reverses the field by pulsing DIO3 and changing the
sign of the IN2 setpoint while the flip is under way
(servo_interface_brief.md) — never run as one sequence before. Rev 5,
`mot_bench`, closed loop, setpoint set from the PC, arm held on in
software, flip triggered by switching the software flip bit on; +2 → −2
and −2 → +2 A, two runs each, three orders: **A** flip and sign change
together, **B** sign change first, flip 0.5 ms later, **C** flip first,
sign change 0.2 ms later. Data `logs/t3e_*`.

- **12/12 OK:** RUN at the new polarity, no sign mismatch, no timeout
  (`TIMEOUT_HOLD`), no trip; final +1.989 / −2.003 A; overshoot 25–38 mA.
- Ramp-down from 90 % to 0: A 338 µs, **B 146 µs**, C 343 µs; from 0 to
  90 % of the new value 0.82–0.95 ms; from leaving 90 % to settled within
  ±2 %: A 1.37 ms, **B 1.22 ms**, C 1.29 ms.
- **B is fastest** because the controller works with the error measured
  in the bridge's present current direction (`servo_error.v`: e =
  pol·(sp − meas), pol = bridge polarity), so a setpoint of the opposite
  sign doubles the error and the controller winds OUT1 down about twice
  as fast — it is not the snap.
- **Open finding: the rev 5 snap does not act during a flip.** The snap
  (`snap_zero`) watches the requested setpoint (`sp_sel`) and works only
  in RUN; during a flip the board's sequence logic brings the current
  down by forcing the setpoint to zero internally (`sp_force_zero`, state
  RAMP_DOWN), which the snap does not see. The flip still ends quickly
  because the sequence logic opens the bridge once |I| < `zero_win`
  (0.49 A) for the waiting time, which cuts off the slow tail (a ≈ 0.4 A
  plateau for 150–250 µs in A and C). **Decide before 100 A:** extend the
  snap to `sp_force_zero` (ramp-downs for flips and stops), or have the
  control computer change the sign first (order B).
- First attempt: the latch was still tripped from loading rev 5 — no
  current, and the sequence logic flipped at zero current. Reset the
  latch after every firmware load.

## Test 3, day 3 — checks before connecting the control computer (11-13, second Claude session)

Rev 5, `mot_bench`, closed loop, setpoint set from the PC, software arm
(held on) and flip (switched on once per flip); board recordings only
(the scope's remote-control connection, SCPI, was in use by another
program). Data `logs/t3g_*` … `logs/t3m_*`; the flip tests are in the 3g
section of the benchtop log. Board left IDLE, temporary settings
restored.

- **Flip special cases:** flip at 0 A OK; flip while the current is
  ramping up OK; a second request *during* a flip is ignored (not saved
  for later); a second request *after* it flips back → sign mismatch,
  stays at 0 (safe). **Rule: one clean pulse per reversal, sign change
  first.** Sign change first, then flip as soon as possible: 20/20,
  0.95–1.0 ms from leaving +2 A to 90 % of −2 A.
- **Stop and trip around a flip:** a stop during a hold or during a flip
  → IDLE, bridge open; a trip (level lowered to 1.5 A) right after a flip
  fires in the new polarity → TRIPPED, `trip_ack` → IDLE.
- **Arm while running:** switching arm off does nothing (by design),
  flips with arm off are ignored, switching arm on again in RUN has no
  effect. **Rule: keep arm on while flips are wanted.**
- **Ramp limits:** 2.09 / 1.04 A/ms measured against 2.08 / 1.04 A/ms set
  (test values); a zero target and targets inside the zero window are
  taken at once. Found that the recording decoder showed the setpoint
  wrongly at polarity 1 — fixed (7e4b7e84).
- **50 shot sequences** (+2 A, +1 A, sign change + flip to −2 A, 0,
  stop): 50/50 OK; holds within 0.3 mA of the request, 1–2 mA from shot
  to shot. Found a bias of −½ of the recording's smallest step (−6.8 mA),
  because the recording keeps only bits [21:6] of each value and drops
  the rest — the decoder now adds back half a step (39bbf373). After the
  snap the current reads ≈ −10 mA (zero offsets are stored in whole
  steps).
- **Two programs changing settings — safety issue, fixed:** when the PC
  program (`Board.write_cfg`) changed one setting, it wrote back a whole
  settings word from its own, possibly out-of-date copy. So a second
  connected program could silently switch `servo_enable` and the arm bit
  back on after another program had stopped the servo, and restart it
  (reproduced at 0 A). Now each change reads the word from the board
  first, changes only the wanted bits and writes it back (39bbf373), with
  a test. The dashboard already re-synced.
- **Register server stopped for 9 s during a 1 A hold:** the board's chip
  stayed in RUN at 1.01 A, settings unchanged; the dashboard reconnected
  by itself.
- **Still to test (needs a signal source):** the analog IN2 setpoint
  (scale, steps and ramps, noise against the snap threshold, ground loops
  with an earthed source, cable unplugged) and the real DIO3/DIO4 inputs
  (voltage levels, shortest pulse, glitches, delay).


## New firmware: zero snap also in flips and stops (11-13)

**Why:** in the reversal tests (3g, flip first) the current paused near
0.4 A for 150–250 µs before the bridge could switch. The zero snap only
reacted when the control computer asked for zero; during a flip or a stop
the board brings the setpoint to zero itself, and then the controller
turned the pass transistors down slowly.

**What changed:** the snap now also switches the pass transistors fully
off whenever the board itself brings the setpoint to zero for a flip or a
stop (bridge closed, no trip). Same on/off setting (`snap_enable`); the
register layout is unchanged (map rev 5), so the host tools and the
dashboard needed no change.

- **Simulation:** new test of flips and stops with the snap off and on.
  Bench model, 2 A flip: current to within 2 % of zero in 105 µs instead of
  176 µs; stop 105 µs. MOT model (30 A): no difference — there the
  controller already switches off at full speed; the snap makes it certain.
  All 240 tests pass.
- **Build:** the first laptop build (25964ff0) missed timing by 0.05 ns in
  the dither (13 logic steps in one clock: sigma-delta adder, 15-bit adder
  and both clip comparisons in a row). Not shipped. The dither now forms
  its three possible outputs (u14 − 1, u14, u14 + 1, each clipped) side by
  side and only picks one at the end — the same result on every clock tick
  (the bit-exact dither test is unchanged). Second build (583e877): **WNS
  +0.251 ns**, WHS +0.007 ns, 0 failing. File 867 292 bytes, SHA-256
  `ca812771…1bd1`. Drive folder: new `coil_servo.bit.bin`, the previous one
  kept as `coil_servo_v2.bit.bin`; repo: previous one kept as
  `coil_servo_rev5_20261112.bit.bin`. New helper scripts on the laptop:
  `scripts/lab-timing.sh` (failing timing paths), and
  `lab-copy-to-drive.sh` now picks the next free version number itself.
- **Bench, reversal test as in 3g** (+2 ↔ −2 A, three orderings, both
  directions, two runs each), time from 90 % to within 2 % of zero:

  | Ordering | Old firmware (11-13 morning) | New, snap off | New, snap on |
  |---|---|---|---|
  | A: flip + sign change together | 329 µs | 290 µs | **142 µs** |
  | B: sign change first | 144 µs | 144 µs | **144 µs** |
  | C: flip first | 335 µs | 317 µs | **143 µs** |

  24/24 OK. The ~0.4 A pause is gone with the snap on.
- **Bench, stop during a 2 A hold** (3 each): 90 % → 2 % in 139–140 µs
  with the snap on, 296–381 µs with it off; all end with the bridge open.
- **Whole-reversal time** was longer today (A/C ≈ 2.1 ms instead of
  1.4 ms) with the snap off too — the PC, not the firmware: since the
  morning's safety fix each settings write first reads the board, so the
  PC's flip and sign-change commands arrive twice as far apart (0.76 ms
  instead of 0.38 ms). Does not apply to the control computer (flip line
  and IN2 act within microseconds).

## Zero-current noise reference (11-13)

Which tones in the current reading are real current? Ten 16.8 ms
recordings each with (1) servo off, bridge open, no current; (2) servo
running at setpoint 0 (snap on, bridge closed); (3) 2 A hold; plus (4) the
2 A hold with the dither off. Data `logs/t3o_*`.

| Band (current in that band) | No current | Setpoint 0 | 2 A | 2 A, dither off |
|---|---|---|---|---|
| 7.7 kHz bump | 8.1 mA | 8.4 | 9.6 | 10.2 |
| 10–20 kHz | 4.3 | 4.3 | 7.2 | 7.5 |
| 30–150 kHz hump | 45.1 | 45.1 | 43.6 | — |
| 260–320 kHz lines | 32.3 | 32.8 | 52.3 | 52.2 |

- The 30–150 kHz hump and the 72 kHz region are the same with no current:
  measurement.
- The extra 200–400 kHz lines with 2 A are **not** coil current: 30 mA at
  290 kHz would need ≈ 2.5 V across the 45 µH coil, and the coil voltage
  was steady to ±0.05 V on the scope (≤ 0.6 mA possible). They grow with
  load current and are unchanged with the dither off: pickup from
  something carrying the load current, most likely the bench supply's
  switching. Re-check with the SE-600-5 in Test 4.
- Possibly real and small: +5 mA at 7.7 kHz (near the coil + damper
  resonance, ≈ 7.5 kHz) and in 10–20 kHz (≈ 0.25 % of 2 A).
