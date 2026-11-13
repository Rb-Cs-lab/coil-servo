# Coil current servo — interface brief for the power electronics

**Purpose:** what the digital current controller (one Red Pitaya STEMlab
125-14 per coil, running our own firmware) does, seen from outside, and
what it expects from — and promises to — the circuits connected to it.
Coils: the MOT pair (anti-Helmholtz, up to 100 A) and the X/Y/Z shims (up
to 60 A). **What is being built now (Nov 2026) is the first version
("MVP"), for the MOT coils only**; the full parts list, the measured
transistor thresholds and the analysis of how the current switches off are
in [mvp_architecture.md](mvp_architecture.md).

*Revision history: 2026-09-08 — rewritten after the circuit-design review
(Ben Brown, Harvard) for the original full design (two supply voltages,
pass transistors with their own current sensing, active clamp).
2026-11-10 (MVP rewrite step 5) — rewritten for the power stage as built:
one 5 V supply, a simple gain-8 amplifier driving the transistor gates, no
clamp, no boost, a heartbeat safety board. The old clamp design (former
section 2b) is in [future_upgrades.md](future_upgrades.md) section 8.
2026-11-13 — reworded in plain language, then updated with the bench
measurements of 11-11 to 11-13 ([benchtop_test_log.md](benchtop_test_log.md),
3 A bench supply, temporary test coil).*

## 1. What the box does

The box measures the coil current as a voltage on one input (IN1) and
compares it with the requested current, the **setpoint** (from the control
computer on IN2, or set from the lab PC). A controller inside the Red
Pitaya's chip (FPGA) — fast and with fixed timing, updated every 1.02 µs —
adjusts **one output, OUT1**, until the two match. OUT1 sets how strongly
the six "pass" transistors conduct; they sit on the low side, after the
bridge. OUT1 is a small signal (the board's fixed ±1 V range). In the MVP
an amplifier (OPA994, gain ≈ 8) turns it directly into the transistors'
gate voltage. There is no extra current regulation at the transistors, so
the bank behaves like a current source set by a voltage, with a **dead
zone**: no current flows below OUT1 ≈ 0.44 V, and above that up to
~1000 A flow per volt of OUT1.

The box also:
- carries out the reversals of the current direction ("flips"): bring the
  current to zero → check it is zero → switch the bridge off → short pause
  → change direction → short pause → switch the bridge on → continue;
- limits how fast the setpoint may change;
- switches off ("trips") when the measured current is too high;
- sends a heartbeat signal to the external safety board.

A computer connects only over Ethernet, for settings and diagnostics. If
the network fails, the current control keeps running.

![MVP system schematic: SE-600-5 supply, H-bridge with body diodes and the two coils (LEM on the mid lead, varistor across each coil), low-side pass bank with ballast and TVS, OPA994 gate stage, LEM/burden/AD8421 sensing chain, safety logic board, isolated gate drivers, Red Pitaya and control computer](img/mvp_schematic.svg)

*MVP system schematic (MOT coils), checked against the hardware
2026-11-10. Thick lines: the 100 A path; copper: bus bars; solid blue:
analog signals; dashed blue: 3.3 V logic signals.*

## 1b. Capabilities at a glance (MOT coils, MVP)

Values marked (P) are PROVISIONAL: they come from the model, using the
transistors' datasheet values and the provisional coil and cable
resistances.

| Quantity | Value | Set by |
|---|---|---|
| Largest current | 100 A, either direction (H-bridge) | Supply rating |
| Over-current trip | 100 A measured, for ~4 µs | Firmware (adjustable) |
| Measurement step | **27.3 mA per measurement step (full scale 224 A, measured 11-12)**; the design value was 24.4 mA (200 A at +1 V), but the Red Pitaya reads full scale at 1.12 V, not 1 V | Current sensor (LEM) + 10 Ω resistor + amplifier with gain 1 + the Red Pitaya's input |
| Output step | ~0.12 A per OUT1 step (P); ~1 mA on average with the OUT1 dither (the output flicked up and down by one step very fast so its average falls between steps) | Gain-8 gate amplifier |
| Ramp up, 0 → 100 A | at least 0.5 ms: the firmware allows at most 200 A/ms; the 5 V supply alone would allow ~0.4 ms (P) | Firmware ramp limit / 5 V supply |
| Ramp down to a lower current | at most 50 A/ms (firmware limit) | Firmware ramp limit |
| Fast drop to zero | ~1.3 ms from 100 A, the current circulating in the bridge and dying away by itself (P; this path was confirmed on the bench 11-12). A zero setpoint (the rev 5 zero snap) and a trip switch the pass transistors off at once. A flip or a stop first winds the controller down, then opens the bridge once the current is near zero (bench, 2 A: a short pause near 0.4 A) | The bridge transistors' built-in diodes |
| Settling | to within 0.1 % in less than 1 ms after a ramp ends (simulation) | Controller; it corrects changes up to 2–5 kHz |
| Field reversal at 50 A, current only | ~1.2 ms: ~0.8 ms for the current to die away + ~70 µs zero check + 2 µs with the bridge off + 100 µs settling pause (placeholder) + 0.25 ms ramp back up. Bench, ±2 A: 1.0–1.4 ms, 32 of 32 reversals good | Board's sequence + physics |
| Continuous running | no time limit at 100 A from the controller's side; **limited by heating** (water cooling required, see 1c) | Power stage |

- **Why the firmware limits ramps:** with this power stage a sudden
  setpoint *step* misbehaves. A step up to ~55–95 A that the supply
  voltage can't follow overshoots by up to ~20 %, and a large step down
  undershoots while the coil's current dies away. The firmware turns any
  step into a ramp of at most 200 A/ms up and 50 A/ms down, so the control
  computer may simply step.
- **Smooth, slower ramps** are programmed on the control computer; the
  controller follows them.
- **Shim coils:** placeholders only — their power stages and supplies are
  not chosen yet.
- **Steadiness, measured on the bench at 2 A:** the average held current
  stays within 0.5 mA over 10 minutes (after a 1-minute warm-up), and
  repeats within 1–2 mA from one run to the next. The 0.1 % check at
  100 A, where one output step is much larger, is part of Test 4.

## 1c. Coils, supply and heat

### Coil values (resistances PROVISIONAL)

| Coils | Inductance (pair) | Resistance (pair) | Largest current |
|---|---|---|---|
| MOT anti-Helmholtz | 16 µH | 6.4 mΩ | 100 A |
| Z shim (Helmholtz) | 29 µH | 11.6 mΩ | 60 A |
| X shim (racetrack) | 57 µH | 14.2 mΩ | 60 A |
| Y shim (racetrack) | 57 µH | 14.2 mΩ | 60 A |

### One fixed supply

The MVP runs from a single Mean Well SE-600-5 (5 V / 100 A). There is no
separate "hold" and "boost" supply: 5 V is enough to ramp the MOT coils
from 0 to 100 A in ~0.4 ms, and whatever voltage the coil doesn't need is
taken up by the pass transistors.

### Where the heat goes at 100 A continuous (MOT coils)

| Part | Value | Power |
|---|---|---|
| From the supply | 5 V × 100 A | 500 W |
| Coil pair | 6.4 mΩ (P) | 64 W |
| Bridge, 2 switches on | 2 × 2.3–4 mΩ (hot) | 50–80 W |
| Cables and contacts | ~2–3 mΩ | 20–30 W |
| Ballast resistors | 6 × 8.4 W | 50 W |
| **Pass transistors** | the rest, ≈ 2.8–3.2 V × 100 A | **≈ 280–320 W (≈ 47–53 W each)** |

Each transistor may take at most 100 W on the water-cooled plate, so
continuous running is fine **with the water flowing**. The first test
round runs without water: short pulses, reduced current, and an explicit
limit on the on-time.

### A damper across the coils is needed (found on the bench, 11-12)

Without damping, the pass transistors and the coil ring together at
10–20 kHz (±0.1–0.2 A on the bench), even with the controller switched
off. A **damper** — a resistor and a capacitor in series across the coil
(3.2 Ω + 10 µF on the 45 µH test coil) — removed it completely. The
cause of the ringing is not understood yet.

- **For the MOT pair:** a damper across the whole pair, sized for 16 µH,
  close to critical damping (R ≈ 2√(L/C), so the current does not swing
  back through zero after a switch-off), with a resistor that survives
  the pulses at 100 A (safety cut-off, flips). Check on the real coils
  whether they ring at all.
- **Current sensor placement:** the sensor must sit *inside* the
  coil–damper loop (bridge → damper connection → sensor → coil),
  otherwise it cannot see current circulating between coil and damper.
  For the MOT pair: the sensor on the lead between the two coils, the
  damper across the whole pair.

## 2. Analog connections (SMA)

| Port | What it carries | Electrical | As built / requirements |
|---|---|---|---|
| IN1 | Measured coil current | ±1 V, 1 MΩ input, DC-coupled, 14 bit at 125 million samples/s | Current sensor LEM LF 310-S (2000:1) → 10 Ω resistor → amplifier AD8421 (gain 1, PROVISIONAL) → IN1: nominally 200 A at +1 V; **measured: full scale 224 A, reached at 1.12 V** (the firmware uses the Red Pitaya's raw readings, without its factory calibration; `in1_fs_volts` = 1.12, checked against the bench supply's ammeter 11-12). Positive and negative. The amplifier isolates the 10 Ω resistor from the Red Pitaya (2026-09-08 review). The zero offset (≈ +0.26–0.29 A, mostly the Red Pitaya's own input) is measured with the zero program and subtracted in the firmware (since rev 4); it drifts a little after power-up, so re-zero before trusting the ±0.5 A "near zero" band used for flips. |
| IN2 | Setpoint from the control computer | same as IN1 | The control computer's analog output through a ÷10 divider. **Same volts per amp as IN1**: at a 200 A full scale that is **20 A per computer volt** (100 A = 5 V). Because the firmware compares the raw IN1 and IN2 readings, this still holds with the measured 224 A full scale *if* IN2 reads volts the same way IN1 does — **IN2's own scale has not been measured yet** (to do before or with the control computer). Its sign must match the bridge direction (section 3). Its noise at 0 V, with the input terminated, is well below the zero-snap threshold (≈ 1.5 steps of margin); re-check with the control computer connected. |
| OUT1 | Command to the pass transistors | 0 … +1 V into 50 Ω (twice that if not terminated), updated 976,600 times per second; plus a ±1-step dither at 125 MHz when switched on | → OPA994 amplifier, gain ≈ 8 → 24.9 Ω → each pass-transistor gate. **Dead zone:** no transistor conducts below ~0.44 V at OUT1 (3.50 V lowest threshold); ~0.55 V asks for ~100 A (P). On the bench the current started at ≈ 3540 steps (gate ≈ 3.65 V), and near there the transistors give only ≈ 8 mA per step. **The 50 Ω terminator at the amplifier is fitted (Test 1b, 11-11)** and must stay: without it the gate swing doubles. Measured with it: 1.168 V at full scale (142.6 µV per step), −41.3 mV at zero, amplifier gain 7.93. The amplifier's supply is 5.18 V, which is also the highest gate voltage it can give. |
| OUT2 | (command for the active clamp in the full design) | — | **Not used in the MVP: held at exactly 0 V by the firmware** (`out2_enable` = 0). Leave unconnected. |

*How fast the controller reacts, for context: it updates 976,600 times per
second with ~3.6 µs delay, and should correct changes up to 2–5 kHz. In
the MVP the coil's inductance doesn't slow the controller down — the pass
transistors act as a current source — so the analog parts (gate charging
~0.6 µs, the amplifier, the current sensor) and how strongly the
transistors respond (3–8 in the controller's units) decide its speed. The
controller settings were tuned on the bench (kp 31.2 steps/A, ki 942
steps/(A·ms), half the strength at which it oscillates); at 100 A the
transistors give about 17× more current per step, so the MOT coils need
their own tuning from measured steps (Test 4).*

## 3. Digital connections (E1 header — 3.3 V logic, no buffer, NOT 5 V tolerant)

All E1 lines go through the safety logic board (built and tested
2026-11-03; every line has a 10 kΩ resistor to ground, so an unconnected
line reads "off").

| Signal (E1 pin) | Direction | What it does | On the safety board |
|---|---|---|---|
| Bridge direction, DIO0_P (3) | out | Chooses which pair of bridge switches conducts, i.e. the current direction. Changes only during a flip, with the bridge switched off for at least the safety pause before and after | inverter chip (74HC04) → direction signal to switches Q1/Q4, inverted to Q2/Q3 |
| Bridge on/off, DIO1_P (5) | out | High = the bridge may conduct | combined with "heartbeat OK" and "not cut off" in one gate chip (3-input NAND) → switches all four drivers off together. **The safety board adds no pause of its own** — the firmware's off-pause during a flip is the only protection against a short-circuit through the bridge |
| Boost on, DIO2_P (7) | out | **Not used in the MVP, held low** | not connected |
| Flip request, DIO3_P (9) | in | A low-to-high change starts a flip (only while running and armed); the pulse must last at least 1 µs | TTL signal, level-shifted |
| Arm, DIO4_P (11) | in | To start, arm must switch **from low to high** after the servo has been enabled from the PC (`servo_enable`); losing arm while running does *not* switch the bridge off | unconnected = not armed |
| Fault, DIO5_P (13) | in | Report only | **Not wired in the MVP** |
| Heartbeat, DIO6_P (15) | out | ~954 Hz square wave from power-on, always; stops only if the chip has failed or is being reprogrammed | Keeps restarting a timer chip (CD74HC123, 220 kΩ / 100 nF; it times out ~7.6 ms after the last heartbeat) → cut-off chip (74HC74 latch) with a reset button. The cut-off **starts in the "cut off" state at power-up** and never clears itself |

**Operating rule:** stop the servo first (`servo_enable` = 0 from the PC;
the firmware ramps the current to zero and switches the bridge off — the
board shows "idle"), and only then press the safety board's reset. If the
heartbeat was lost while the Red Pitaya kept running, the firmware never
noticed that the bridge was cut off, so the controller may have turned
OUT1 up to its maximum while trying to get current — pressing reset would
then switch the bridge on at full drive. Taking arm away is not enough: it
never switches a running servo off.

**Also (found on the bench):**
- **Press reset only with the green heartbeat light on.** The cut-off is
  triggered by the *moment* the heartbeat stops. A reset pressed while
  the heartbeat is still missing clears it, and when the heartbeat comes
  back nothing triggers it again.
- **Loading the firmware (or restarting the Red Pitaya) trips the safety
  board**, because the heartbeat pauses meanwhile. Reset it after every
  load.
- **The firmware cannot see the safety board** (the fault input DIO5 is
  not wired). After a cut-off at current, the controller turns OUT1 up to
  its maximum while trying to get current back. On the bench an
  unexplained 5 A surge happened in this situation and was caught by the
  over-current trip. Proposed: wire the safety board's output to DIO5 so
  the firmware stops itself, and/or have the firmware stop when OUT1 is
  at its maximum but no current flows.

### Reversing the field (rules for the control computer)

IN2 carries the **full setpoint, with its sign**. But the sign alone does
not reverse anything — it has to *match* the bridge direction that the flip
line has set. To go from +I to −I:

1. **Send a pulse on the flip line** (E1 pin 9, at least 1 µs). The board
   brings its own setpoint to zero, lets the current die away, checks that
   it is near zero, reverses the bridge with the safety pauses, and
   continues — ignoring IN2 throughout.
2. **Change the setpoint sign on IN2** (+I → −I) at any time while the
   flip is in progress. After the flip the firmware ramps up to the new
   value at up to 200 A/ms.

If the setpoint sign and the bridge direction don't match, the board
safely holds zero current and raises a "sign mismatch" flag
(`sp_sign_mismatch`) — a mistake in the order costs field, never hardware.
A change of the setpoint's sign never starts a flip by itself.

**Measured on the bench (2026-11-13, ±2 A, firmware rev 5;
benchtop_test_log.md section 3g and "day 3"):** every order works (32 of 32
reversals), but these rules make reversals fastest and unambiguous:

- **Change the setpoint sign first, then send the flip pulse** (anything
  up to ~0.5 ms later is fine). A setpoint of the opposite sign doubles the
  controller's error, so the current reaches zero about twice as fast
  (146 µs instead of ~340 µs from 2 A; steady at the new value within
  ±2 % after 1.2 ms instead of 1.3–1.4 ms). With the flip first, the
  current pauses near zero for 150–250 µs.
- **Exactly one flip pulse per reversal.** A second pulse *during* a flip
  is ignored (not saved for later); a second pulse *after* the flip has
  finished flips back again — the setpoint sign then no longer matches and
  the current stays at zero. Clean up the flip line so it never sends a
  double pulse.
- **Keep arm (DIO4) high for as long as flips may be wanted.** With arm
  low a flip pulse is ignored. Taking arm away is not a stop and does
  nothing to a running servo; switching arm on again while running does
  nothing either.
- To switch the field off, set IN2 to **exactly 0 V** (within about 4
  measurement steps, ≈ 0.1 A): the zero snap (firmware rev 5) then switches
  the pass transistors fully off at once. Any setpoint above that is held.
  The zero snap does **not** act during a flip or a stop (there the board
  brings the setpoint to zero itself); that is why the current pauses
  near zero when the flip comes first. Whether to extend the snap to flips
  and stops is an open decision (section 6).

## 4. What the box guarantees (built into the firmware, checked in simulation)

- **After power-up or a firmware load, everything is off:** OUT1 = OUT2 =
  0, every DIO output low, bridge off, the controller reset, the board
  idle. To start, the PC must enable the servo (`servo_enable`) *and* arm
  must then switch on; an arm line that is already on at load does
  nothing. The firmware-loading program checks this state after every
  load.
- **A hard upper limit on the OUT1 command.** In the MVP this limits the
  *command*, not the current (how much current a given command gives
  depends on the transistors, their thresholds and their temperature) — so
  there is also:
- **An over-current trip on the measured current** (MOT: 100 A for
  ~4 µs): both outputs go to zero on the next clock tick; the bridge stays
  on while the coil's current dies away, switches off once the current is
  near zero (or after 3 ms in any case), and the board stays tripped until
  the PC acknowledges and arm is switched on again. The supply's own
  over-current protection is the backup.
- **OUT2 is held at 0 V** and the boost line low in the MVP.
- **The bridge direction never changes while the bridge is on**; both
  outputs are zero in the same clock tick that the bridge switches off. A
  flip that cannot get the current to zero waits with the bridge on
  (holding the current at zero) until an operator acknowledges it.
- **"Off" from software is a smooth ramp to zero**, never a sudden bridge
  switch-off while current flows.
- **Setpoint ramps are limited** to 200 A/ms up and 50 A/ms down — except
  a target inside the ±0.5 A near-zero band, which is taken at once (fast
  drop, the current dying away at its natural rate).
- **A zero setpoint switches the pass transistors fully off at once**
  (rev 5 zero snap, within about 4 measurement steps of 0 V on IN2); small
  non-zero setpoints are still held.
- Losing the network or the computer changes nothing; the hardware
  protection is the heartbeat safety board. (Checked on the bench: the
  board's communication program was stopped for 9 s during a 1 A hold;
  the current stayed at 1.01 A with every setting unchanged.)

## 5. Performance measured on a cable (2026-08-27 — before the MVP power stage)

Measured with OUT1 connected straight back to IN1 (no power stage), with
the firmware of the original full design:

- Controller on: 10–90 % rise in 102 µs (≈ 3.5 kHz), steady within ±1 %
  after 259 µs, no overshoot, on target to 0.04 %.
- Built-in frequency-response measurement: flat to 0.3 % from 100 Hz to
  100 kHz; the instrument's own timing error equals 139 ns.
- Uncorrected input and output calibration: gain errors of about 2 % per
  path, output zero ≈ −35 mV (OUT1's resting offset — harmless in the MVP,
  because it sits inside the dead zone).

The MVP power stage responds differently, so the controller settings were
scaled; the response with the real load is measured during bring-up.

## 6. Open questions (MVP)

Each item is a measurement or decision that the firmware deliberately does
not fix in advance (the values live in `host/config/channels.toml`).

1. **Coil and cable resistance** (4-wire and LCR measurements): set the
   highest current the 5 V supply can push at 100 A and where the heat
   goes. *Open.*
2. **Pass transistors:** how strongly they respond at these currents, the
   measured response and dead zone and how they change with temperature,
   and whether OUT1 is terminated in 50 Ω — these set OUT1's upper and
   lower limits and the controller settings. *Partly answered on the
   bench: terminator fitted; current starts at ≈ 3540 steps; ≈ 8 mA per
   step at 1–2 A; they conduct more as they warm up (the controller
   removed ≈ 0.2 A of drift over 10 minutes). The behaviour at 100 A is
   for Test 4.*
3. **The OPA994 amplifier's supply voltage:** must be higher than the
   largest gate command. *Answered: 5.18 V, which is also the highest
   gate voltage.*
4. **Sensor amplifier gain** (1 or 2): full scale 200 A or 100 A.
   *Gain 1 as built; measured full scale 224 A.*
5. **How the current dies away at switch-off:** confirm that it circulates
   through the bridge by scoping the pass transistors' drain bar and node A
   during a fast drop from a moderate current (drain bar at ~supply +
   0.7 V and a ~ms decay means it does). *Answered on the bench (11-12):
   it circulates through the bridge's built-in diodes; the surge protector
   is not reached. Switching the transistors off briefly rings the drain
   bar up to 12.1 V at 2 A, just below the surge protector's 13.3 V — at
   100 A the surge protector will clip it; scope this in Test 4.*
6. **Bridge opened while current flows** (the safety board cutting off at
   current): does the SE-600-5 switch itself off for over-voltage? Is a
   capacitor or an absorber across the supply needed? *Bench preview at
   2 A without a damper: the supply voltage jumped by 2.5–3.2 V for
   ≈ 10 µs. With the damper fitted, the coil's energy went into the damper
   instead. At 100 A (≈ 0.08 J) most of it will go into the supply.
   Open until Test 4.*
7. **IN1 offset** (~0.29 A) — calibrate before trusting the "near zero"
   band for flips. *Answered: measured with the zero program and
   subtracted in the firmware; re-zero after power-up.*
8. **A capacitor across the supply** (C_hold): yes or no. *Open.*
9. **How long the field takes to settle after a flip** because of currents
   induced in the vacuum chamber → `settle_us`. *Open.*
10. **On-time limit without cooling water** for the first test round.
    *Moved to Test 4 (the bench only reaches 15 W).*
11. **Steadiness of a held current** with the gain-8 amplifier and the
    dither, against the 0.1 % goal — decides whether a different gain and
    offset, or an extra inner current loop with its own sensor, is needed
    (future_upgrades.md sections 7, 8d). *Bench (2 A): average steady
    within 0.5 mA over 10 minutes; the dither makes no difference there
    because one step is only ≈ 8 mA. The real test is at 100 A.*
12. **The damper for the MOT pair** (values, resistor pulse rating), and
    whether the real coils ring at all (section 1c). *Open.*
13. **Making the safety board visible to the firmware** (DIO5) and/or a
    firmware stop when OUT1 is at its maximum but no current flows
    (section 3). *Open; until then, always stop the servo before reset.*
14. **The zero snap during flips and stops:** extend it, or rely on the
    "change the setpoint sign first" rule (section 3). *Open.*
15. **IN2's scale and its noise with the control computer connected**
    (the noise sets the zero-snap threshold, now 0.1 A). *Open.*

*Full technical record: [mvp_architecture.md](mvp_architecture.md) (the
MVP power stage), [register_map.md](register_map.md) (every setting),
[design.md](design.md) (controller design) and
[lab_notes_2026-08-27.md](lab_notes_2026-08-27.md) (first commissioning).*
