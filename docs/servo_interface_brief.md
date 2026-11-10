# Coil current servo — interface brief for the power electronics

**Purpose:** what the digital servo (Red Pitaya STEMlab 125-14, one per coil
channel, custom FPGA firmware) does as a black box, and what it expects
from — and promises to — the circuits connected to it. Channels: MOT
anti-Helmholtz (100 A rated), X/Y/Z shims (60 A rated). **The hardware
being built (Nov 2026) is the MVP, MOT channel only**; the full parts
list, measured thresholds and the turn-off analysis are in
[mvp_architecture.md](mvp_architecture.md).

*Revision history: 2026-09-08 — rewritten after the circuit-design review
(Ben Brown, Harvard) for the original full architecture (hold + boost
rails, per-FET source-sense pass bank, active clamp). 2026-11-10 (MVP
rewrite step 5) — rewritten for the power stage as built: one 5 V rail,
open-loop gain-8 gate stage, no clamp, no boost, heartbeat latch. The
superseded clamp design (old section 2b) is in
[future_upgrades.md](future_upgrades.md) section 8.*

## 1. Black-box function

The box measures the coil current as a voltage on one analog input (IN1),
compares it with the setpoint (IN2 from the control computer, or a
register), runs a PI feedback loop in hardware (FPGA — deterministic,
updated every 1.02 µs), and drives **one analog command output, OUT1**,
which sets how hard the low-side linear pass bank conducts. OUT1 is a
small signal (the board's fixed ±1 V range); in the MVP an OPA994 stage
with gain ≈ 8 turns it into the pass-FET gate voltage directly — there is
no local current loop, so the bank behaves as a voltage-controlled
current source with a **dead zone** (no current below OUT1 ≈ 0.44 V) and
a gain of up to ~1000 A per volt above it.

The box also sequences the H-bridge polarity flips (ramp to zero → verify
→ open bridge → dead time → toggle polarity → dead time → close → resume),
limits how fast the setpoint may move, trips on measured over-current,
and toggles a heartbeat line for the external watchdog. A computer
connects only over Ethernet for configuration and diagnostics; if the
network dies, the loop keeps running.

![MVP system schematic: SE-600-5 supply, H-bridge with body diodes and the two coils (LEM on the mid lead, varistor across each coil), low-side pass bank with ballast and TVS, OPA994 gate stage, LEM/burden/AD8421 sensing chain, safety logic board, isolated gate drivers, Red Pitaya and control computer](img/mvp_schematic.svg)

*MVP system schematic (MOT channel), checked against the hardware
2026-11-10. Thick lines: 100 A path; copper: bus bars; solid blue: analog
signals; dashed blue: 3.3 V logic.*

## 1b. Capabilities at a glance (MOT channel, MVP)

Numbers marked (P) are PROVISIONAL: they come from the model with the
datasheet g_fs and the provisional coil/cable resistances.

| Quantity | Value | Set by |
|---|---|---|
| Rated current | 100 A, either sign (H-bridge) | Supply rating |
| Over-current trip | 100 A measured, ~4 µs persistence | Firmware (configurable) |
| Measurement resolution | 24.4 mA per ADC count (I_FS = 200 A at +1 V) | LEM + 10 Ω burden + in-amp G = 1 |
| Actuator step | ~0.12 A per OUT1 count (P); ~1 mA effective with the OUT1 dither | Gain-8 gate stage |
| Ramp up, 0 → 100 A | ≥ 0.5 ms: firmware limit 200 A/ms; the 5 V rail alone allows ~0.4 ms (P) | Firmware slew limit / 5 V compliance |
| Ramp down to a lower current | ≤ 50 A/ms (firmware limit) | Firmware slew limit |
| Snap to zero (setpoint → 0, flip, stop, trip) | ~1.3 ms from 100 A, freewheeling (P) | Body-diode freewheel |
| Settling | to 0.1 % in < 1 ms after a ramp ends (simulation) | PI loop, crossover 2–5 kHz |
| Field reversal at 50 A, electrical | ~1.2 ms: ~0.8 ms freewheel to zero + ~70 µs zero check + 2 µs bridge-open + 100 µs settle (placeholder) + 0.25 ms ramp back up | FSM + physics |
| Continuous duty | indefinite at 100 A from the servo's side; **thermally limited** (water cooling required, see 1c) | Power stage |

- **Why ramps are limited in firmware:** on this plant a raw setpoint
  *step* misbehaves — a compliance-limited step up to ~55–95 A overshoots
  by up to ~20 %, and a large step down undershoots while the coil
  freewheels. The firmware turns any step into a ramp at ≤ 200 A/ms up
  and ≤ 50 A/ms down, so the control computer may simply step.
- **Shaped (adiabatic) ramps** slower than those limits are programmed at
  the control computer; the loop tracks them.
- **Shim channels:** placeholders — their power stages and supplies are
  not chosen yet.

## 1c. Coil parameters, the rail, and the power budget

### Per-channel coil parameters (PROVISIONAL resistances)

| Channel | L (pair) | R (pair) | Rated current |
|---|---|---|---|
| MOT anti-Helmholtz | 16 µH | 6.4 mΩ | 100 A |
| Z shim (Helmholtz) | 29 µH | 11.6 mΩ | 60 A |
| X shim (racetrack) | 57 µH | 14.2 mΩ | 60 A |
| Y shim (racetrack) | 57 µH | 14.2 mΩ | 60 A |

### One fixed rail

The MVP runs from a single Mean Well SE-600-5 (5 V / 100 A). There is no
hold/boost split: 5 V is enough to ramp the MOT pair 0 → 100 A in
~0.4 ms, and everything the coil doesn't need is dropped across the
linear pass bank.

### Power dissipation at 100 A CW (MOT channel)

| Term | Value | Power |
|---|---|---|
| From rail | 5 V × 100 A | 500 W |
| Coil pair | 6.4 mΩ (P) | 64 W |
| Bridge, 2 switches conducting | 2 × 2.3–4 mΩ (hot) | 50–80 W |
| Cable + contacts | ~2–3 mΩ | 20–30 W |
| Ballast | 6 × 8.4 W | 50 W |
| **Pass MOSFETs** | remainder ≈ 2.8–3.2 V × 100 A | **≈ 280–320 W (≈ 47–53 W each)** |

The design limit is ≤ 100 W per FET with the water-cooled plate, so
steady state is inside limits **with water flowing**. The first test
round runs without water: short pulses, reduced current, an explicit
duty limit.

## 2. Analog interfaces (SMA)

| Port | Function | Electrical | As built / requirements |
|---|---|---|---|
| IN1 | Measured coil current (feedback) | ±1 V, 1 MΩ, DC-coupled, 14 bit @ 125 MS/s | LEM LF 310-S (2000:1) → 10 Ω burden → AD8421 (G = 1, PROVISIONAL) → IN1: **I_FS = 200 A at +1 V**, signed. The burden is buffered by the in-amp (2026-09-08 review). Measured offset ≈ 1.4 mV ≈ 0.29 A — calibrate before relying on the ±0.5 A flip zero window. |
| IN2 | Setpoint from the control computer | same as IN1 | Control computer analog out through a ÷10 divider. **Same volts-per-amp as IN1**: at I_FS = 200 A that is **20 A per computer volt** (100 A = 5 V). Its sign must agree with the bridge polarity (section 3). |
| OUT1 | Pass-bank command | 0 … +1 V into 50 Ω (×2 if unterminated), 976.6 kS/s updates, plus a ±1-count dither at 125 MHz when enabled | → OPA994 non-inverting gain ≈ 8 → 24.9 Ω → each pass-FET gate. **Dead zone:** no FET conducts below ~0.44 V at OUT1 (3.50 V lowest threshold); ~0.55 V asks for ~100 A (P). **Check before first power-on:** OUT1 terminated in 50 Ω at the op-amp, or the gate swing doubles (up to 16 V command). The op-amp supply rail must exceed the largest gate command and is not yet documented. |
| OUT2 | (active clamp command in the full architecture) | — | **Unused in the MVP: held at exactly 0 V by firmware** (`out2_enable` = 0). Leave unconnected. |

*Loop dynamics for context: PI at 976.6 kS/s, ~3.6 µs digital latency;
target crossover 2–5 kHz. In the MVP the coil's L/R pole is not in the
loop — the bank is a current source — so the analog chain (gate RC
~0.6 µs, op-amp, LEM) and the plant gain G_pass/I_FS ≈ 3–8 set the
loop; the PI gains are the loopback-commissioned ones rescaled by that
gain, to be set from the measured step response.*

## 3. Digital interface (E1 header — 3.3 V logic, unbuffered, NOT 5 V tolerant)

All E1 lines go through the breakout / safety logic board (built and
tested 2026-11-03; 10 kΩ pull-down on every line).

| Signal (E1 pin) | Dir | Behaviour | On the logic board |
|---|---|---|---|
| Bridge polarity, DIO0_P (3) | out | Selects the bridge diagonal; changes only mid-flip with enable low for ≥ the dead time on both sides | 74HC04 → POL to Q1/Q4, NOT-POL to Q2/Q3 driver IN+ |
| Bridge enable, DIO1_P (5) | out | High = bridge may conduct | 3-input NAND with "heartbeat OK" and "latch not tripped" → shared driver DISABLE. **The logic board adds no dead time** — the FSM's enable-low window is the only shoot-through protection |
| Boost enable, DIO2_P (7) | out | **Unused in the MVP, held low** | not connected |
| Flip request, DIO3_P (9) | in | Rising edge starts a flip (only in RUN and while armed); pulse ≥ 1 µs | level-shifted TTL |
| Arm, DIO4_P (11) | in | Leaving IDLE needs a **rising edge** while `servo_enable` is already set; losing arm mid-run does *not* drop the bridge | disconnected = disarmed |
| Fault, DIO5_P (13) | in | Report-only | **Not wired in the MVP** |
| Heartbeat, DIO6_P (15) | out | ~954 Hz square wave from power-on, unconditionally; stops only if the FPGA is dead or being reprogrammed | Retriggers a CD74HC123 monostable (220 kΩ / 100 nF, measured drop ~7.6 ms) → 74HC74 latch with reset button; the latch **powers up tripped** and never re-arms itself |

**Operating rule:** disarm (DIO4 low) before pressing the latch reset.
If the heartbeat was lost while the Red Pitaya kept running, the
firmware never saw the bridge open and its integrator may be wound up —
reset would re-close the bridge with OUT1 near the clamp.

### Field-reversal choreography (contract for the control computer)

IN2 carries the **full signed setpoint**, but the sign does not command a
flip — it must *agree with* the bridge polarity the flip line has
established. To reverse the field from +I to −I:

1. **Pulse the flip request line** (E1 pin 9, ≥ 1 µs). The servo forces
   its setpoint to zero, lets the current freewheel down, verifies it sits
   inside the zero window, reverses the bridge with dead time, and resumes
   — ignoring IN2 throughout.
2. **Swap the analog setpoint sign** (+I → −I) any time while the flip is
   in progress. After the flip the firmware ramps up to it at ≤ 200 A/ms.

If the setpoint sign and bridge polarity disagree, the servo sits safely
at zero and raises `sp_sign_mismatch` — a sequencing mistake costs field,
never hardware. Flips are never triggered by the setpoint changing sign.

## 4. What the box guarantees (enforced in the FPGA, verified in simulation)

- **Reset/load state is fully off:** OUT1 = OUT2 = 0, every DIO output low,
  bridge disabled, integrator cleared, FSM in IDLE. Leaving IDLE needs
  `servo_enable` from the host *and* a fresh arm edge; an arm line already
  high at load does nothing. `deploy` checks this state after every load.
- **Hard clamp on the OUT1 command.** In the MVP it limits the *command*,
  not the current (the current per volt depends on g_fs, thresholds and
  temperature) — so there is also:
- **Over-current trip on measured current** (MOT: 100 A for ~4 µs): both
  outputs to zero on the next clock; the bridge stays closed while the
  coil freewheels down, opens once the current is near zero (or after
  3 ms regardless), and latches until the host acknowledges and arm is
  re-raised. The supply's own over-current protection is the backup.
- **OUT2 ≡ 0 V** and the boost line low in the MVP.
- **The bridge polarity never changes with the bridge enabled**; both
  outputs are zero on the same clock the bridge opens. A flip that cannot
  reach zero parks with the bridge closed (servoing to zero) until the
  operator acknowledges.
- **Software "off" is a graceful ramp-to-zero**, never an instantaneous
  bridge drop at current.
- **Setpoint ramps limited** to 200 A/ms up, 50 A/ms down — except a
  target inside the ±0.5 A zero window, which is taken at once (snap-off
  at the freewheel rate).
- Network/computer loss changes nothing; the hardware protection is the
  heartbeat latch.

## 5. Commissioned performance (bench loopback, 2026-08-27 — before the MVP plant)

Measured with OUT1 looped into IN1 (no power stage), on the
full-architecture bitstream:

- Closed loop: 10–90 % rise 102 µs (≈ 3.5 kHz), settled to ±1 % in
  259 µs, no overshoot, on-target to 0.04 %.
- Built-in swept-sine transfer-function measurement: magnitude flat to
  0.3 % over 100 Hz – 100 kHz, instrument phase error equivalent to
  139 ns.
- Raw converter calibration: gain ≈ +2 % class per path, DAC zero
  ≈ −35 mV (OUT1 resting offset — sits harmlessly inside the dead zone in
  the MVP).

The MVP plant has a different gain, so the PI gains were rescaled; the
real-load step response is a bring-up measurement.

## 6. Open questions (MVP)

Each item is a measurement or decision the firmware deliberately does not
hardcode (values live in `host/config/channels.toml`).

1. **Coil and cable resistance** (Kelvin + LCR): sets the compliance at
   100 A and the dissipation split.
2. **Pass bank:** g_fs in this regime, the measured G_pass and dead zone
   and their temperature dependence, and whether OUT1 is 50 Ω-terminated —
   these set the OUT1 clamp and floor values and the PI gains.
3. **OPA994 supply rail:** must exceed the largest gate command.
4. **In-amp gain** (1 vs 2): I_FS 200 A vs 100 A.
5. **Turn-down path:** confirm the freewheel by scoping the pass-bank
   drain bar and node A during a snap-off from moderate current (drain bar
   at ~V_rail + 0.7 V and a ~ms decay = freewheel).
6. **Bridge-open-at-current energy path** (heartbeat trip at current):
   does the SE-600-5 trip its over-voltage protection? Is a hold capacitor
   or an absorber across the rail needed?
7. **IN1 offset** (~0.29 A) — calibrate before trusting the flip zero
   window.
8. **Hold capacitor across the rail** (C_hold) yes/no.
9. **Chamber eddy-current settling** after a flip → `settle_us`.
10. **Uncooled duty-cycle limit** for the first test round.
11. **Hold noise** with the gain-8 stage and dither, against the 0.1 %
    spec — decides whether the gain + bias change or a shunt-sense inner
    loop is needed (future_upgrades.md sections 7, 8d).

*Full technical record: [mvp_architecture.md](mvp_architecture.md) (the
MVP power stage), [register_map.md](register_map.md) (every setting),
[design.md](design.md) (loop design) and
[lab_notes_2026-08-27.md](lab_notes_2026-08-27.md) (commissioning).*
