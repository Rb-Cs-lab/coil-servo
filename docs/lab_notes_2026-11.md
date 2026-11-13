# Coil servo — MVP build and bring-up log (November 2026)

**Channel:** MOT | **Board:** Red Pitaya STEMlab 125-14 at 192.168.0.65
(wired) | **Power stage:** the MVP (docs/mvp_architecture.md) |
**Sources:** OneNote "Construction" notes (Santi + Jeff, 11-02 to 11-06)
and the design decisions of 11-10.

## Build log

- **11-02 — MOSFET thresholds.** V_GS(th) of all 10 pass-bank
  (IXTN200N10L2, at 3 mA) and 10 bridge (IXFN420N10T, at 8 mA) devices
  measured: gate tied to drain, series resistor from a bench supply,
  DMM across gate–source. Pass bank 3.35–3.56 V → installed #1, 2, 4, 7,
  9, 10 (3.50–3.56 V, 0.06 V spread → ≤ 12 % current-sharing spread at
  100 A). Bridge 3.51–3.59 V → installed #2, 5, 6, 9 (0.01 V spread).
  The SE-600-5 was reordered after the wrong model arrived.
- **11-03 — logic board on the MOT Red Pitaya** (still running the
  2026-08-27 bitstream). `pip install -e .` then `python -m
  coil_servo_host.check mot` **failed at first**: after a reboot the
  board had neither the bitstream nor the register server running. Fixed
  by hand over ssh:

  ```bash
  /opt/redpitaya/bin/fpgautil -b /root/coil_servo.bit.bin
  ```

  ```bash
  nohup python3 /root/coil_servo_server.py --port 9001 >/dev/null 2>&1 &
  ```

  After that `check` reported heartbeat ADVANCING, LED walk done,
  state IDLE, armed 0, fault 0, bridge_en 0, polarity 0, measured
  **+0.179 A** (at the old I_FS = 125 A: ≈ 1.4 mV IN1 offset, ≈ 0.29 A at
  the MVP's 200 A scale). The LED acceptance sequence passed
  (bringup.md 3a). In that test `servo_enable` was set from Python
  *after* arm was already high, and the old firmware went to RUN on the
  level — the MVP firmware needs an arm edge after `servo_enable`, so the
  recipe now enables first, then arms.
- **11-03 — monostable timing.** First built with 68 kΩ: the timeout was
  ~1.1 ms, about one heartbeat period, so a pulled heartbeat could
  recover on an edge. Changed to **220 kΩ / 100 nF: drop ≈ 7.6 ms,
  consistent**. (The CD74HC123 is specified at 5 V and times differently
  on the 3.3 V rail; trust the measurement.)
- **11-04 — cold plates.** Water-cooled plates machined (bridge: 4
  SOT-227; pass bank: 6 SOT-227 + 6 MP930). FETs mounted with thermal
  paste.
- **11-04 — pass bank assembled.** 12 AWG equal-length drain legs to the
  "input → drain" bus bar; gate/source leads and ballast resistors
  soldered; source legs to the "output → to source" bus bar; 5KP12CA
  between the bars; parts labelled; OPA994 perfboard with BNC input
  wired.
- **11-06 — H-bridge.** Assembly started; gate-drive pinout fixed
  (mvp_architecture.md section 4); bridge bus bars ordered.

## Decisions (11-10)

- **Turn-off keeps the bridge closed:** current goes down by freewheeling
  through the bridge body diodes (~1.3 ms from 100 A); the firmware never
  opens the bridge at current. (The bank TVS does not conduct in this
  path.) A faster snap-off later = blocking diode + TVS across the bridge
  input (future_upgrades.md section 6; parts to be ordered).
- **Dead-zone floor:** in RUN the PI output rests at ~90 % of the
  pass-bank dead zone, so steps from zero meet the 1 ms target.
- **Over-current trip at 100 A** (the supply's rating), on measured
  current.
- **Setpoint ramps enforced in firmware:** ≤ 200 A/ms up, ≤ 50 A/ms down
  (raw steps overshoot or undershoot on this plant) — except a target
  inside the ±0.5 A zero window, which is taken at once, so a step to
  zero is a full-speed freewheel snap-off (~1.3 ms from 100 A).
- **OUT1 dither** to recover resolution lost to the gain-8 gate stage.
- **DIO5 (fault input) not wired;** the Red Pitaya outputs nothing until
  enabled and armed (edge).

## Repo (MVP rewrite, brief section 11)

Steps 1–5 done on 11-10: architecture docs, model, firmware + cocotb
benches, host tools, documentation. The host now detects "no register
server" and prints the `deploy` command (the 11-03 problem), and `deploy`
checks the board comes up in its safe state. Auto-loading the bitstream
and server at boot (systemd) was **not** done: it would change what state
the board boots into — propose separately if wanted.

## Step 6 — MVP bitstream (11-11)

The NUC is no longer available; Vivado 2025.1 was installed on a lab
laptop under WSL2 (Ubuntu 24.04) following README "Setup B" (the libtinfo5
package had been revised to `6.3-2ubuntu0.3`; the Linux "Self Extracting
Web Installer" is needed, not the Windows one).

| Build | Result | Fix |
|---|---|---|
| 1 | WNS −2.169 ns, 232 failing endpoints: `slew_0` (−2.17), `pi_0` (−1.80), `fsm_0` (−1.03), two marginal | slew limiter split into measure/move phases; PI extra pipeline stage (registered sum, bounds, floor compares); FSM window/trip compares registered; dither output registered |
| 2 | WNS −0.015 ns, 1 endpoint (`slew_0/acc`) | rate choice registered in the measure phase |
| 3 | **WNS +0.072 ns, WHS +0.053 ns, 0 failing** | — |

Build 3 (from `aa2a92f1`) is `bitstreams/coil_servo.bit.bin`, SHA-256
`3e9f15a3…f416`. **Next:** first deploy with arm low and the power stage
off, then bring-up per [bringup.md](bringup.md).

## First deploy of the MVP bitstream (11-11)

Board only: power stage off, arm low, nothing on the SMA inputs.
`deploy mot` loaded the image (69 ms) and its post-check passed: IDLE,
OUT1 = 0, bridge disabled, not armed, register map rev 3. `check mot`:
heartbeat ADVANCING, board settings UNKNOWN before (expected after a load),
`mot` settings after, **configuration written and read back OK** (the
32-word CFG block works on hardware), OUT2/boost 0, dither on, state IDLE,
not armed, not tripped. Measured current at zero with nothing on IN1:
**+0.189 A** (≈ 1 mV, the Red Pitaya's own input offset; positive = the
safe direction for the trip). Re-measure with the LEM chain connected.

## Logic-board acceptance test with the MVP firmware (11-11) — latch fault found and fixed

Board only (power stage off). After a Red Pitaya power cycle the bitstream
was gone: no heartbeat (E1 pin 15 read 0.3 V instead of ~1.6 V average),
green off, latch held tripped — the safety chain working as designed.
Redeployed; heartbeat back.

- **Arm-edge rule confirmed on hardware:** arm jumper (pin 11) in *before*
  `servo_enable` = 1 → stays IDLE, amber off; unplug/replug → RUN, amber on.
- **Fault: the latch did not latch.** Pulling the heartbeat turned green
  and amber off but red stayed off, and re-plugging it brought amber back
  on by itself. Diagnosis by meter: the latch (74HC74 half 1) trips on a
  rising edge at CLK (pin 3) with D (pin 2) held high — a probe touch on
  pin 3 tripped it — and the monostable's 1Q̄ (CD74HC123 pin 4) rises when
  the heartbeat is lost, but **74HC74 pin 3 had no wire at all** (floating,
  ~0.4–0.6 V). Likely a wire that came loose after the 11-03 test.
- **Fix:** wire CD74HC123 pin 4 → 74HC74 pin 3. Re-test: pull heartbeat →
  green off, amber off, red on; re-plug → green on, red stays on, amber
  stays off. ✓
- **Design weakness to review (not fixed):** the trip is *edge*-triggered.
  Pressing reset while the heartbeat is still absent clears the latch, and
  when the heartbeat returns there is no new edge, so the bridge enables
  without another reset. A level-sensitive trip (monostable output on the
  latch's asynchronous preset/clear) would prevent that. Until reviewed:
  **only press reset with the heartbeat present (green on), and only after
  stopping the servo.**
- Measured on the way: CD74HC123 half 1 is the watchdog (pin 13 1Q = 3.3 V
  with heartbeat, 0 V without; pin 4 1Q̄ the inverse); half 2 idle.
- Corrected operating rule (also in all docs, commit 5493db89): stop the
  servo (`servo_enable` = 0, IDLE) before pressing reset — dropping arm
  alone does not stop a running servo.
- **Acceptance test passed** after the fix (all 8 steps of bringup.md 3a
  in the corrected order): arm-before-enable stays IDLE; arm edge → RUN;
  heartbeat pulled → red latched; re-plugged → red stays, amber off; stop →
  IDLE; reset → amber stays off; re-enable + arm edge → RUN; stop → IDLE.
  The logic board passes with the MVP firmware.

## Loopback test with the MVP firmware (11-11)

OUT1 → IN1 with one SMA cable (IN1 high-impedance), floor set to 0 for the
test, logic board connected, no power stage. Run from the lab PC.

- **Open loop (scaling):** OUT1 command 0 / 500 / 1000 / 1800 counts → IN1
  −570 / 447 / 1459 / 3080 counts, ±1–2 counts. **Loopback factor 2.026**
  (2.034 / 2.025 / 2.027 per segment; Aug: 2.03). The −570-count (≈ −70 mV)
  reading at code 0 is the DAC's resting offset (≈ −35 mV, ×2 unterminated;
  Aug: same) — harmless in the MVP, it sits inside the pass-bank dead zone.
- **Closed loop:** setpoint 819 counts ("20 A") → IN1 mean 819.00 (±1–2),
  OUT1 684; 2048 ("50 A") → IN1 2047.93 (±1), OUT1 1290 — matching the
  predicted (setpoint + 570)/2.026.
- **Over-current trip:** setpoint 4506 ("110 A") in closed loop → TRIPPED,
  bridge enable low; stop + `trip_ack` → IDLE. ✓
- **Finding — open-loop integrator wind-up:** the first switch from open to
  closed loop *tripped the board*: in open loop the PI keeps integrating
  (setpoint 0 against the −570 offset) and wound up to ~4400 counts, which
  hit OUT1 the moment closed loop took over. On the real coil that would be
  a jump towards ~100 A. Fixed host-side the same day (`Board.write_cfg`
  applies open_loop 1 → 0 only from IDLE/TRIPPED; `Board.stop()`; step and
  sweep use it); firmware fix (hold the integrator cleared in open loop)
  queued for the next rebuild.

## H-bridge and current-sensing bench test (colleague, before integration, reported 11-11)

Bridge alone, driven by hand (logic board not connected): gate drivers
UCC5350 with RO-0515S isolated supplies (+5 V from an isolated bench supply),
3.3 V logic; IN+ high on the active diagonal, IN+ low on the other, all IN−
low; bridge supply a 5 V CV bench supply; 4 AWG welding-cable jumpers with
ring lugs between the MOSFETs (main cabling is 4/0 — check 4 AWG heating
before 100 A CW).

1. Gate drivers and shoot-through check: passed.
2. Q4 gate read 0 V (Q1 16 V), no load current: loose connection, fixed.
3. Resistive load: Q1/Q4 → +5 V, Q2/Q3 → −5 V, all off → 0 V. Passed.
4. Coil + LEM LF 310-S (±15 V bench supply, 10 Ω burden, AD8421 G = 1 →
   49.9 Ω → IN1): 1.12 A each way; burden −35 mV (Q1/Q4) and −23 mV
   (Q2/Q3), i.e. ±6 mV about −29 mV. Reversal passed. Scale 5.4 mV/A vs
   5.0 expected — within the uncertainty of a ±6 mV meter reading and a
   bench-supply ammeter at 1 A; calibrate with the Red Pitaya on the 3 A
   bench run.

**Open finding — LEM zero offset: −29 mV on the burden ≈ −2.9 mA output ≈
−5.8 A primary-equivalent** (datasheet ±0.2 A class). Unchanged with the
AD8421 disconnected, after power-cycling ±15 V, and with the LEM flipped
180° → internal to the sensor (magnetised core, e.g. primary current while
the LEM was unpowered, or a sensor fault). **Consequences for the servo as
built** (no offset correction in firmware): the loop would deliver ~5.8 A
more than the setpoint; the trip would sit ~5.8 A high (bench: 8.6 A real,
above the 3 A supply — would never fire); the zero window (±0.5 A) would
never qualify, so a stop would park in RAMP_DOWN servoing ~5.8 A real
current with the bridge closed. **No powered runs with this offset.**
Actions: demagnetise the LEM (slowly decaying AC through the primary) or
replace it; firmware IN1 offset-correction register proposed for the next
rebuild.

## Register-map rev 4 deployed (11-11, afternoon)

Rev 4 adds IN1/IN2 zero-offset registers (prompted by the −5.8 A LEM),
a build-ID register and the open-loop integrator clear. Built from
`103a127` on the laptop (first attempt stopped on a Tcl error in the
build-ID script, fixed): **WNS +0.156 ns, WHS +0.074 ns, 0 failing**.
Deployed; `check mot`: **firmware: register map rev 4, commit 103a127**
(no uncommitted-changes flag), configuration written and read back OK,
IDLE, not armed, not tripped. Zero reading +0.306 A, offsets not yet
calibrated — run `python -m coil_servo_host.zero mot --write --apply` with
the real sensor chain connected, powered and no current flowing.

## Test 1 — gate stage (11-11, no high current)

Setup (test_campaign.md): OPA994 board on a separate 5.184 V bench supply,
OUT1 → op-amp BNC (no terminator) teed to scope CH1, ×10 probes on the
gates of pass FETs #4 (CH2), #1 (CH3), #10 (CH4), all grounds on the source
bar; high-current side open. Open loop, OUT1 swept 0–4474 counts
(raw data: logs/t1_gate_sweep.json, logs/t1_gate_decay.npz).

- **Wiring fault found first:** the gates stayed at ~0 V. The op-amp's
  supply sat 5 V too low relative to the source bar (pin 7 +0.39 V, pin 4
  −5.1 V): its − terminal was not tied to the source bar and its + was
  effectively at ground (the source bar is earthed through the scope).
  Fixed: 5 V supply − → source bar, supply floating from earth. Then pin 7
  +5.18 V, pin 4 0 V.
- **OUT1 at the op-amp input: 285.4 µV per DAC count, offset −81 mV at
  code 0** → OUT1 is **unterminated** and swings 2.34× the nominal
  122 µV/count (Red Pitaya open-circuit output ≈ 2.34 V full scale).
- **Op-amp gain 7.94** (gates #4 and #10; design 7.98). Gate #1 read a
  constant ~0.1–0.15 V low on the replacement probe and agreed within 2 %
  at full bandwidth — probe, not circuit (one op-amp output feeds all gates).
- **Gate ceiling 5.17–5.18 V** (the op-amp's 5.184 V rail, rail-to-rail),
  reached from ~2550 OUT1 counts: as wired, the firmware clamp (4474
  counts, computed for a terminated OUT1) never acts; the op-amp rail caps
  the gate at ~5.2 V (model: bank ≈ 190–200 A at that gate voltage).
- **No oscillation:** 312 MS/s, full bandwidth: input 2 mV rms, gates
  4–6 mV rms, only 1–5 mV lines at 125 MHz (Red Pitaya clock) and harmonics.
- **Fail-safe on op-amp power loss:** gates fall together to ~0.1–0.2 V,
  τ ≈ 4.5 ms (the op-amp output follows its own supply's discharge), below
  0.5 V within ~8.5 ms.
- **Consequences:** with OUT1 unterminated one count ≈ 2.27 mV of gate ≈
  0.27 A of bank current (model), the dead zone sits at ~1830 counts, and the
  configured floor (3234) and clamp (4474) are wrong for this wiring.
  Recommendation: fit a 50 Ω BNC feed-through at the op-amp input (halves
  volts/count and the offset, matches `out1_terminated = true`), re-run the
  sweep, then put the measured OUT1 scale/offset and the 5.18 V ceiling into
  channels.toml.

### Test 1b — re-run with the 50 Ω terminator (11-11)

50 Ω BNC feed-through fitted at the op-amp input, sweep repeated
(logs/t1_gate_sweep_terminated.json):

- **OUT1 at the op-amp input: 142.6 µV per count (1.168 V full scale),
  −41.3 mV at code 0.** Terminated, but the Red Pitaya output still runs
  ~17 % above the nominal 1 V.
- **Op-amp gain 7.93**, gate ceiling 5.18 V (unchanged).
- **Into channels.toml (all profiles):** `out1_fs_volts = 1.168`,
  `out1_offset_volts = -0.0413`, `gain_opamp = 7.93`, `V_opamp_max = 5.18`.
  The host now derives from these: **dead zone 3385 counts, RUN floor 3047,
  clamp 4144 (100 A)**, normalised plant gain 5.56. The clamp now sits below
  the op-amp rail (~4870 counts), so it acts again.
- **PI gains rescaled** to the new plant gain: kp 0.0900, ki 2713 /s
  (bench profile: 3.6852 counts/A, 111.14 counts/A/ms). Still loopback-
  derived — set them from the measured step response in Test 3.
- One OUT1 count is now ≈ 0.14 A of bank current (model): more than the
  ±0.1 % band at 50 A, so **dither_enable stays on** for every hold. The
  simulation tests now run the 50 A settling checks with dither on.

## Test 2 — sensor chain repair and zero calibration (11-11)

Setup: LEM LF 310-S on ±15 V (floating bench supply, GND referenced only
through the BNC jacket to the Red Pitaya/scope), 10 Ω burden, AD8421 on a
Perma-Proto board, output → IN1, teed to scope CH1; IN2 on a 50 Ω
terminator; no current anywhere (high-current supplies disconnected).

- **First reading: IN1 pinned at full scale** — the in-amp output sat at
  +12.6–13.7 V (its positive limit; IN1 unplugged at once, the Red Pitaya
  input was checked undamaged afterwards: +1.3 mV with nothing connected).
- **Fault 1: REF (pin 6) floating.** It reached ground only *through* the
  decoupling capacitors (a capacitor passes no DC), reading +4.6 V. Fixed:
  plain wire pin 6 → GND. (Jeff's drawing shows the capacitors in line with
  the REF wire, which invites this; correct: REF wired straight to GND,
  capacitors from pin 8 and from pin 5 to GND.)
- **Fault 2: R_G pins 2–3 bridged** by solder (on the replacement chip;
  the first chip was probably the same, so it is likely good — kept as a
  spare). A bridged R_G makes the gain effectively unlimited: the output
  sat at one supply limit or oscillated rail to rail. Fixed: bridge removed.
- **Output resistor was 47 kΩ** (yellow-violet-orange), not the 49.9 Ω of
  the design — that would have read IN1 4.5 % low (9 % with the scope
  teed) and filtered the signal at tens of kHz. Replaced with 49.9 Ω.
- **In-amp inputs are swapped** relative to the drawing (−IN on the LEM/M
  end, +IN on GND): this only flips the sign. Left as is; the sign is
  checked with the first real current in Test 3 (turn the LEM round if
  needed).
- **The LEM is not magnetised:** with the chain repaired the in-amp output
  is −0.2 mV (≈ −0.04 A) with the LEM powered (its supply current changes
  when plugged in). The "−29 mV / −5.8 A offset" of 11-10/11-11 was the
  wiring faults, not the sensor.
- Noise at the in-amp output (scope, 20 MHz BW): ~1 mV rms ≈ 0.2 A rms
  equivalent at the scope's resolution — to be re-checked with the board's
  own capture (the ADC averages over the 128-sample tick).
- **Zero calibration** (`zero mot --write --apply`, `zero mot_bench --write`):
  IN1 +8.6 counts (+0.21 A — the Red Pitaya's own input offset plus the
  in-amp's −0.2 mV), IN2 +6.3 counts (+0.15 A, on the 50 Ω terminator —
  re-measure once the control computer's analog output is connected). Stored
  in channels.toml [mot] and [mot_bench]; after loading, `check mot` reads
  −0.06 A (≈ 2 counts). **Pass.**

## Test 3 — bench supply, one coil (11-11, first session, profile mot_bench)

Setup: bench supply 5.0 V / 3.0 A CC in place of the SE-600-5, one MOT coil
between bridge nodes A and B (4/0), LEM on its lead (one pass), pass bank and
OPA994 gate stage as in Test 1 (OUT1 50 Ω terminator stays). Scope ×1 BNC on
all four: CH1 IN1 tee, CH2 drain bar, CH3 node A, CH4 bridge enable (DIO1
tee). Logic board latch reset with heartbeat present.

- **Current sign was reversed** (in-amp inputs swapped vs Jeff's drawing):
  the first open-loop scan read a growing *negative* current and stopped by
  itself. Fixed by turning the LEM round; re-zeroed.
- **Ground loops: do NOT earth the bench supplies.** With the coil, op-amp
  and ±15 V supplies strapped to earth (and scope grounds on the source bar)
  part of the coil current returned through earth and the BNC shields: IN1
  read ~2× low and with the wrong sign on the scope, and the bridge dropped
  out after ~8 s at ~1 A (enable still commanded — most likely a ground-noise
  latch trip). With all supplies floating (earthed only through the scope)
  the board, the scope and the supply ammeter agree, and a 5-minute open-loop
  hold at ~1.1 A ran without a dropout.
- **IN1 reads ~10 % low:** the firmware uses raw ADC codes (the Red Pitaya's
  factory calibration is not applied). At a 1.09 A supply reading the board
  read 0.99–1.02 A (drifting up as the FETs warmed); the scope (5.50 mV at
  5 mV/A) gave 1.10–1.15 A. New measured key `in1_fs_volts = 1.10`
  (PROVISIONAL ±3 %) in [mot] and [mot_bench] and in the model: I_FS = 220 A,
  26.9 mA per count. Refine in a closed-loop hold (constant current; read the
  supply at leisure). Re-zeroed on the new scale: IN1 +9.7 counts, IN2 +5.5.
- **Pass bank at bench currents (open loop, logs/t3_open_loop_scan2.json,
  t3_hold_3850/3990.json):** current starts at ~3540 counts (gate ≈ 3.65 V;
  model 3385), ~1.1 A at 3850, ~2.2 A at 3990 cold — and 3990 tripped at
  2.8 A once the FETs were warm (at fixed gate the current rises as they
  heat). Roughly square-law near threshold; ~0.008 A per OUT1 count at
  1–2 A versus the model's 0.136 A/count (the model's g_fs is for 100 A).
  (The scan's absolute amps were taken before the ground-loop fix and are
  ~2× low; onset and shape are valid.)
- Rail droop ~0.24 V at 1 A upstream of node A (bench supply leads).
- **Firmware trip verified in passing:** open loop at 3990 counts with warm
  FETs → TRIPPED at 2.8 A, bridge opened, acknowledged with trip_ack.
- **[mot_bench] updated:** out_clamp_counts 4100 (room for 2.5 A and the
  2.9 A trip test), u_floor_counts 3450 (~90 counts below onset, ~0 A there),
  PI gains for the measured slope would be kp 62.5 / ki 1885 counts/A(/ms);
  tuning starts at 1/4 (kp 15.6, ki 471) and doubles while the step stays
  clean. (A quick model check with a hand-adjusted bank was not usable —
  hardware decides.)
- Not yet done: closed-loop steps (first attempt timed out waiting for the
  arm edge), trip test in closed loop, freewheel capture, flips, heartbeat
  trip, hold noise, duty.

**Next session:** power up; deploy (`deploy mot_bench` — the bitstream is lost
when the Red Pitaya is powered off), `check mot_bench`, re-zero, then 3d
closed-loop steps from 1 A, reading the supply in a steady closed-loop hold
to finalise `in1_fs_volts`.
- **Model follow-up (open):** the model's pass bank (dead zone 3385 counts,
  0.136 A/count) does not match the bench measurement near threshold (onset
  ~3540, ~0.008 A/count, square-law). The HDL bench therefore runs
  [mot_bench] with the file's protection values but the model's floor and
  gains. Fit the model's near-threshold bank to the measured scan before
  using it to tune bench gains. [mot] normalized gains rescaled to the
  220 A IN1 scale (kp 0.09897 / ki 2984.8 = unchanged counts per amp).

## Test 3 — bench supply, second session (11-12, profile mot_bench)

Same setup as 11-11. **Correction to 11-11:** the coil is not one of the MOT
pair but a **temporary test coil**: one racetrack, 12 turns of 4 × 2 mm
hollow copper (2 mm bore), 70 × 210 mm, 12 mm inner corner radius, lying on
the steel optical table. Calculated from its geometry (Neumann integral):
**L ≈ 45 µH, R ≈ 25 mΩ** (model and channels.toml updated; the 9 µH used
until now was a guess for one MOT coil). Arm and flip requests from software
(pulses of the `dio_invert` setting: bit 1 = arm, bit 0 = flip request), so
the arm jumper stays out. Plots and step-by-step detail:
[benchtop_test_log.md](benchtop_test_log.md) (generated from the session's log
page, `logs/testlog/benchtop_log.html` (lab PC only -- logs/ is not in git;
served by the `test-log` entry in
`.claude/launch.json`)); data in `logs/t3_*`.

- **Bring-up:** deploy + check clean; offsets IN1 +0.256 A, IN2 +0.157 A.
  **Deploying trips the logic-board latch** (loading the bitstream pauses
  the heartbeat): the first closed-loop step gave no current and no amber
  light. Reset the latch after every deploy or Red Pitaya reboot (now in
  bench_supply_test.md step 3).
- **IN1 scale (3c), closed-loop 2 A hold, 60 s:** board 1.988 A, bench supply
  ammeter 2.02–2.03 A, scope 2.086 A (≈ 3 % high). **`in1_fs_volts` = 1.12**
  (was 1.10 provisional): I_FS = 224 A, 27.3 mA per count. Agrees with the
  11-11 first ammeter estimate (1.118). [mot] normalized gains rescaled to
  keep counts per amp (kp 0.1008, ki 3038.6). Commit d70a45d6.
- **PI gains (3d), closed-loop 1 A steps, ki/kp fixed:**

  | kp (counts/A) | ki (counts/(A·ms)) | 90 % | 98 % | hold noise | verdict |
  |---|---|---|---|---|---|
  | 15.6 | 471 | 1.41 ms | 1.85 ms | 40 mA rms | clean, slow |
  | **31.2** | **942** | 0.73 ms | 1.04 ms | 40 mA rms | **chosen** |
  | 62.4 | 1884 | 0.38 ms | 0.53 ms | 86 mA rms | 21 kHz oscillation |

  2 A step at the chosen gains: 98 % in 0.65 ms, no overshoot. A 2.5 A step
  tripped once (coil lifted, see ringing below); on the table it gave +2.8 %
  overshoot but 28 single samples above the 2.79 A trip level → **bench
  `I_rated` lowered 2.5 → 2.2 A**.
- **Over-current trip (3e), 2.9 A step (I_rated temporarily 2.9):** fired at
  ≈ 2.85 A (set 2.79 A), DACs to zero, freewheel to zero in ≈ 225 µs, bridge
  open, TRIPPED; supply CC lamp stayed off. trip_ack + stop → IDLE.
- **Turn-off path (3f), open loop, OUT1 → 0 at once:** gate falls 4 V → 0
  in ≈ 4 µs; drain bar jumps to ≈ 5.9 V (rail + one diode drop, brief
  ringing to 7.4 V) and stays there exactly as long as current flows; the
  current ramps down linearly. **Freewheel through the bridge body diode
  confirmed; the TVS is never reached.** Slope 12 mA/µs on the table,
  8 mA/µs lifted 13–15 cm (slower in free air: the table's eddy currents
  absorb energy) → ≈ 0.37 V across the 45 µH coil. Setpoint → 0 in closed
  loop is slower (~0.3 ms) because the PI winds OUT1 down — the planned
  "snap OUT1 to zero" change (fast turn-off upgrade) addresses that. Warm-bank
  leakage at the RUN floor: 45–60 mA at 3450 counts.
- **Flips and dead time (3g), ±2 A:** flips only inside the ±0.49 A zero
  window (current at the polarity edge +0.13 / −0.29 A), ≈ 300 µs from 2 A,
  no timeout. **Enable drops 1.02–1.04 µs before and returns 0.98–1.02 µs
  after each polarity edge** (125 × 8 ns; measured at the Red Pitaya pins;
  bridge-FET V_GS still to be checked with a floating probe). Flips need the
  arm *level* high (`if (flip_req && armed)`): the dashboard's software-arm
  button gives only an edge, so flips after it are ignored — passed to the
  dashboard session.
- **Heartbeat-latch trip (3h), 2 A:** bridge opens at once; the coil
  discharges into the supply through three diodes (up from ground through
  the pass-bank body diodes, drain bar ≈ −0.5 V) and is at zero in
  ≈ 10–13 µs (≈ 6.7 V across 45 µH). **Rail: +2.5 to +3.2 V spike for
  ≈ 10 µs** (lead inductance), then ≈ +0.4 V because the 2 A lead drop
  vanishes (DC rail 5.06 V at 0 A, 4.65 V at 2 A: ≈ 0.2 Ω of supply leads).
  The PI saw zero current and drove OUT1 to the clamp until stopped — the
  stop-before-reset rule. A 2.5 A attempt tripped the firmware first.
- **Hold noise and dither (3i), 2 A:** dither on/off makes no difference on
  the bench (one OUT1 count ≈ 8 mA here; the real test is at 100 A, ≈ 0.14 A
  per count). Average current very stable: means of four 16.8 ms records
  agree to ±0.2 mA (0.01 %).
- **Open finding — 10–20 kHz ringing of the bench power stage.** With the
  coil lifted (and marginally on the table) the current rings by itself:
  ±0.08–0.2 A, drain ±0.5–0.8 V (exactly L·dI/dt), rail ±0.06 V.
  - Not the PI loop (same at half the gains and in open loop).
  - Not the gate stage (a pass-FET gate pin is steady to ±0.1 mV).
  - Not the varistor across the coil (Digi-Key 495-6502-ND; removed → no
    change) — there is no capacitor on the H-bridge.
  - Nothing bypasses the bank: the rail ripple is exactly the supply leads'
    0.17 Ω + 3.6 µH times the oscillating current, so supply, bank and coil
    carry the same current — **the bank changes its current at a fixed gate
    voltage** (drain-voltage and/or die-temperature dependence near
    threshold, where the bench runs a bank built for 100 A).
  - Frequency ∝ 1/√I: 20 kHz at 0.71 A, 15 kHz at 1.23 A, 11.5 kHz at
    1.93 A; drain swing always ≈ 1.6 V p-p (clamped by the freewheel diode).
  - Coil flat on the table: gone (82 mA rms, no line) — damping decides.
  - **Matters for the MOT:** a similar ringing would be a few-percent field
    ripple at 10–20 kHz, far above the loop bandwidth. Next: a series
    3 Ω + 10 µF damper across the coil (being fitted), then the mechanism
    (FET data near threshold) before the 100 A build.
- **3j (uncooled duty) moved to the 100 A build:** ≤ 15 W on the bench.
- Firmware/config: bench `I_rated` 2.2 A; offsets re-zeroed after an
  unplanned Red Pitaya reboot (IN1 +0.289 A, IN2 +0.179 A — the input drifts
  while warming up after power-up).
- **Extra checks, coil flat on the table (second Claude session; data
  `logs/t3b_*`):**
  - Closed-loop steps 0 → 0.5 / 1.0 / 1.5 / 2.0 / 2.2 A: 98 % in 1.36 /
    0.95 / 0.70 / 0.62 / 0.63 ms (faster at higher current: the bank's gain
    rises), overshoot 25–41 mA = at the noise level; no sample above the
    trip at 2.2 A. Down-steps 2.2 → 1.0, 1.0 → 0, 2.0 → 0 A: 98 % in 0.34 /
    1.22 / 1.49 ms (fast drop, then the integrator unwinds); floor leakage
    leaves 14–18 mA at setpoint 0.
  - 10 min hold at 2.0 A: mean 1.9958 A; after the first (warm-up) minute
    the window means agree within ≈ 0.5 mA (0.025 %); per-point resolution
    ±5.8 mA. OUT1 fell 3948 → 3927 counts as the bank warmed (≈ 0.2 A of
    open-loop drift removed by the loop).
  - 20 flips at ±1 A: 20/20, no TIMEOUT_HOLD, no trip; request → RUN with the
    new polarity ≈ 1.1 ms (host-timed).
- **Damper test — the ringing is cured by an RC damper across the coil.**
  1 Ω + 2.2 Ω (1/4 W metal film) in series with 10 µF (non-polarized) across
  the coil terminals, next to the varistor (≈ 3.2 Ω ≈ the coil's impedance
  at 12 kHz). Coil lifted (where it rang reliably), open loop: strongest
  5–30 kHz line 43 / 76 / 124 mA at 0.7 / 1.2 / 1.9 A without the damper →
  2.5 / 2.8 / 4.9 mA (noise) with it; drain ripple 326–366 → 63–107 mV rms.
  Closed-loop 2 A hold: 6.5 mA rms in 100 µs averages (15–21 mA before).
  A small line at ≈ 72 kHz (≈ 10–14 mA) appears with the damper, probably
  its capacitor with the wiring. **For the MOT build: plan an RC damper across
  the pair** (R ≈ ωL at the ringing frequency for 16 µH, C to pass it,
  resistor rated for the 100 A pulses) and confirm on the real coils.

## LEM moved inside the coil–damper loop; turn-off measurements (11-12, late)

Plots and details: [benchtop_test_log.md](benchtop_test_log.md), section
"LEM inside the coil–damper loop". Data `logs/t3c_*` (lab PC).

- **Why:** the damper and varistor were connected at the coil terminals and
  the LEM sat on the bridge side of that connection, so it read coil +
  damper + varistor current. Current circulating coil → damper → coil (after
  a turn-off, or when the bridge opens) was invisible. **Moved** so the order
  is bridge → damper/varistor tap → LEM → coil. For the MOT the planned LEM
  position between the two coils is inside the loop automatically, as long
  as the damper goes across the whole pair.
- **Sign and loop:** sign unchanged (OUT1 3800 counts → +0.65 A). Closed-loop
  1 A step 90 % 0.61 ms, 98 % 0.81 ms, +2.4 % — the coil–damper resonance in
  the feedback path does not hurt the loop.
- **Turn-off, bridge closed (open loop, OUT1 → 0), 2 A:** freewheel at
  ≈ 0.5 V across the coil, ≈ 12–13 mA/µs, 2 A → 0 in ≈ 170 µs (two runs
  agree; the scope and the board's own capture agree). Then a **≈ −0.2 A
  swing for ≈ 100 µs** (the coil ringing once into the damper capacitor),
  gone by ≈ 200 µs. The bank turn-off rings the drain bar up to **12.1 V**
  for a few µs (≈ 500 kHz), just under the bank TVS (5KP12CA ≈ 13.3 V) —
  at 100 A expect the TVS to clamp it; scope it in Test 4.
- **Bridge opened at 2 A** (firmware trip with `trip_timeout` = 0 and
  `trip_level` 1 A, temporarily; same electrical event as the heartbeat
  trip without integrator wind-up): coil voltage jumps to ≈ 6 V = 2 A ×
  3.2 Ω, current zero in ≈ 25 µs, swings to ≈ −0.3 A, gone by ≈ 150–200 µs;
  the energy goes into the damper, not the supply. 12.7 V / 0.4 µs spike on
  node A (wiring inductance). The morning's "gone in ≈ 10 µs" (3h, no
  damper, LEM outside) no longer describes the coil. At 100 A the bridge
  body diodes clamp the coil at ≈ rail + 1.5 V and most of the 0.08 J goes
  into the supply; size the MOT damper near critical damping
  (R ≈ 2√(L/C)) so the current does not ring back through zero.
- **Unexplained 5 A surge.** During a closed-loop 2 A hold waiting for a
  heartbeat pull, the firmware over-current trip fired; the scope shows
  ≈ 4.7–5 A with the bridge closed, the rail sagging 4.3 → 1.9 V (3 A supply
  in current limit, its output capacitors supplying the rest), then the
  trip and a normal freewheel. The latch reset was pressed before the LEDs
  were read. **Latch check afterwards** (supply output off, RUN at setpoint
  0 with OUT1 wound to the clamp): DIO6 pulled ≈ 2 s → red on, amber off;
  re-plugged → red stays on, amber stays off ✓ (quick-flick test not done).
  Cause open: reset pressed while still in RUN (wound-up integrator), a
  heartbeat loss too short for the pull test, or a disturbance on the
  reading. **Underlying weakness: the firmware cannot see the latch** — when
  the latch opens the bridge the PI winds OUT1 to the clamp, and any reset
  or glitch re-closes the bridge at full drive. Proposed (not done): latch
  output → DIO5 with the firmware stopping and clearing the integrator on
  it, and/or stall detection (OUT1 at the clamp, current far below the
  setpoint for > ~1 ms → stop). Until then: stop the servo before reset.
- **Noise on the raw current traces** is pickup on the 5 mV/A IN1 line
  (≈ 3 mV rms, with the scope's 20 MHz limit already on), white: 575 mA rms
  raw → 36 mA rms in 50 µs averages with the current at zero. The real
  steadiness is the board's averaged reading (6.5 mA rms per 100 µs at
  2 A; mean within 0.5 mA over 10 min).
- **Closed-loop 2 A square pulse (8 ms):** rise 10 → 90 % 360 µs, 98 % at
  ≈ 0.56 ms, +2 % (PI-limited; the rail could do ≈ 20 µs). Fall: 2 → 0.35 A
  in ≈ 130 µs, then a **PI tail**: 10 % at ≈ 0.43 ms, 2 % at ≈ 1.3 ms (the
  integrator unwinds at ≈ 280 counts/ms through the bank's turn-on region).
  Present with the LEM inside or outside. **Proposed firmware option "snap
  OUT1 to zero"** (OUT1 → 0 and integrator → floor when the setpoint enters
  the zero window): turn-off at the natural freewheel rate; works with the
  present hardware (bridge stays closed). Separate from the bridge-open fast
  turn-off that needs the TVS + blocking diode. Not implemented yet.
  **Decided (user, 11-12): trigger the snap only on a zero setpoint**, not
  the whole ±0.5 A zero window, so currents below 0.5 A can still be held.
  For the analog IN2 path "zero" needs a threshold of a few counts (IN2
  reads ±1–3 counts of noise at 0 V), to be set from measured IN2 noise.
  Expected side effect: the ≈ −0.2 A damper swing returns on the bench
  (≈ V_f/√(L/C)); a near-critically damped MOT damper removes it.
- **Estimates for 100 A on the MOT pair:** freewheel turn-off ≈ 1.3 ms
  (1.0–1.7 ms; t = (L/R)·ln(1 + I₀R/V_f), V_f ≈ 0.6–1.0 V, R ≈ 8–12 mΩ; the
  same formula gives 165 µs for the bench vs 170 µs measured); bridge open
  ≈ 0.2 ms; future 30 V-clamp turn-off ≈ 50 µs; rise 0 → 100 A ≈ 0.4 ms
  hardware limit, ≥ 0.5 ms with the 200 A/ms firmware ramp, depending on
  the bank's compliance at 100 A with the 5.18 V gate ceiling (Test 4).
  Coil current only; chamber eddy currents come on top.

## Register map rev 5: zero-setpoint snap — built, deployed, tested (11-12, evening)

**What:** the closed-loop turn-off tail (above) removed in firmware. In RUN,
closed loop, when the requested setpoint averaged over one PI tick is
within `snap_thresh` of zero (CFG word 23; ctrl b21 `snap_enable`), OUT1 is
forced to exactly 0 and the integrator held cleared: the bank turns fully
off at once and the coil freewheels with the bridge closed. One count of
hysteresis; STS b14 `snapped`. channels.toml: `snap_zero_enable = 1`,
`snap_zero_amps = 0.1` (4 counts, PROVISIONAL) for `mot` and `mot_bench`.
Only a zero setpoint triggers it (decided 11-12), so small currents can
still be held. Works on the present hardware; separate from the
bridge-open fast turn-off that needs the TVS + blocking diode.

- **Simulation** (HDL in the loop, `sim/tb_coil_servo_top.py`, 5 new tests,
  237 tests pass): bench model 2 A → 0.1 % in 108 µs instead of 1.74 ms;
  MOT model 80 A unchanged (1.1 ms — the PI already gets the bank off
  there); the re-rise matches a rise from rest; threshold hysteresis; ±8
  counts of IN2 noise does not toggle it; inactive in IDLE and open loop;
  flips unaffected.
- **Build** on the lab laptop (`scripts/lab-build.sh`, clean, from
  `275081b`, HDL identical to `704fd89`): **WNS +0.003 ns, WHS +0.042 ns,
  0 failing** — met, but the setup margin is thin (rev 4: +0.156 ns); watch
  it on the next change. 685 496 bytes, SHA-256 `f3533cc8…17aa`. Copied to
  the Drive folder "RP servo files" (rev 4 kept as `coil_servo_v1.bit.bin`)
  and to `bitstreams/` (rev 4 archived as `coil_servo_rev4_20261111.bit.bin`).
- **Deployed** (`deploy mot_bench`): check reports rev 5, commit 275081b,
  configuration read back OK, snap on (0.109 A). Re-zeroed: IN1 +0.237 A,
  IN2 +0.166 A. (Remote-desktop note: the laptop session drops Shift and
  Ctrl, so the build and copy are two scripts with Shift-free names.)
- **Hardware, closed-loop 2 A pulse** (board capture; the scope refused
  connections this time):

  | | rise to 98 % | fall to 10 % | fall to 2 % | undershoot |
  |---|---|---|---|---|
  | snap off | 0.55 ms | 0.48 ms | **1.55 ms** | −0.03 A |
  | snap on (4 runs) | 0.55–0.60 ms | 0.13 ms | **0.14 ms** | −0.16 to −0.20 A |

  The fall now runs at the freewheel rate all the way to zero; the dip is
  the damper capacitor ringing back once (≈ V_f/√(L/C)), as predicted — a
  near-critically damped MOT damper removes it. Rise unchanged.
- **Hardware checks:** setpoint 0 and 2 counts (55 mA) → snapped on every
  read; 10 counts (0.27 A) → not snapped, the loop holds 0.275 A; IN2 analog
  path at 0 V → snapped on 3000/3000 reads (no chatter) — but IN2 read −3
  counts after the offset correction, only 1 count inside the threshold:
  measure IN2 noise and drift with the control computer connected and set
  `snap_zero_amps` from it; `snap_enable` 0 → OUT1 back on the PI floor.
- **Re-test after a Red Pitaya power cycle** (redeploy, latch reset,
  re-zero IN1 +0.237 A / IN2 +0.150 A): 2 A pulse, fall to 2 % in 1.31 ms
  (snap off) vs 0.14 ms (snap on), rise unchanged, undershoot −0.19 A;
  threshold / IN2 / off checks pass again (0.27 A held at 0.272 A).
- **IN2 noise at 0 V** (terminated input, 20 raw 131 µs captures over
  20 s, after the offset correction): 4.1 counts rms per sample (why single
  STS reads of `sp_active` show ±5), but the 1 µs averages the snap uses
  have sd 0.43 counts, mean +0.75 (the integer offset register leaves
  ~0.7), max +2.45 — none of 2560 above the 4-count threshold. Margin
  ≈ 1.5 counts to engage, 2.5 to release. Re-measure with the control
  computer driving IN2 before Test 4.

## Test 3e — full field reversal, +2 A ↔ −2 A (11-13, second Claude session)

The control computer reverses the field by pulsing DIO3 and swapping the
IN2 sign while the flip is in progress (servo_interface_brief.md) — never
run as one sequence before. Rev 5, `mot_bench`, closed loop, register
setpoint, arm held high in software, flip by a software edge; +2 → −2 and
−2 → +2 A, two runs each, three orderings: **A** flip + swap together,
**B** swap first then flip 0.5 ms later, **C** flip first then swap 0.2 ms
later. Data `logs/t3e_*`.

- **12/12 OK:** RUN at the new polarity, no sign mismatch, no
  TIMEOUT_HOLD, no trip; final +1.989 / −2.003 A; overshoot 25–38 mA.
- Ramp-down 90 % → 0: A 338 µs, **B 146 µs**, C 343 µs; 0 → 90 % of the new
  value 0.82–0.95 ms; leaving 90 % → settled within ±2 %: A 1.37 ms,
  **B 1.22 ms**, C 1.29 ms.
- **B is fastest** because an opposite-sign setpoint doubles the drive-frame
  error (servo_error.v: e = pol·(sp − meas)), so the PI winds OUT1 down
  about twice as fast — not the snap.
- **Open finding: the rev 5 snap does not act during a flip.** `snap_zero`
  watches the requested setpoint (`sp_sel`) and needs RUN; the flip
  ramp-down is the FSM's `sp_force_zero` in RAMP_DOWN. The flip still ends
  quickly because the FSM opens the bridge once |I| < `zero_win` (0.49 A)
  for the holdoff, cutting the PI tail (a ≈ 0.4 A shelf for 150–250 µs in
  A/C). **Decide before 100 A:** extend the snap to `sp_force_zero`
  (flip and stop ramp-downs), or have the control computer swap the sign
  first (ordering B).
- First attempt: latch still tripped from the rev 5 deploy — no current,
  the FSM flipped at zero current. Reset the latch after every deploy.
