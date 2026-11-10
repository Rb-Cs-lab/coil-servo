# MVP power stage — architecture as built (Nov 2026)

**What this is:** the description of the hardware actually being built for
the first working channel (MOT only). It supersedes the power-stage parts
of the August–September documents, which described a fuller design (active
clamp, boost rail, per-FET source-sense loops, relay interlock chain). Those
extras are not in the MVP and now live in
[future_upgrades.md](future_upgrades.md). The firmware core — PI loop, flip
state machine, heartbeat, output clamp, register hub — was commissioned on
2026-08-27 and carries over; what changes is the plant it drives.

**Sources:** OneNote "Construction" (Santi + Jeff, 2026-11-02 to 11-06),
"Minimum Viable Product" and "MVP Component List" (2026-10-16), the
"coil driver order" purchase sheet, and Santi's decisions of 2026-11-10.
The working plan for bringing the rest of the repo in line is
[mvp_rewrite_brief.md](mvp_rewrite_brief.md).

**Status of the analysis here:** parts, pinouts and measurements are facts
from the build notes. The circuit behaviour in sections 3 and 7 is
*analysis*, unverified until a scope looks at the real thing. Items marked
**⚠ REVIEW** are places where this document disagrees with the rewrite
brief and a decision or a bench measurement is needed before the model
(step 2 of the rewrite) is written against them.

**Fixed requirements:** 100 A max on the MOT coils, 60 A max on the shims;
one fixed low-voltage rail per channel (MOT ≤ 6 V, shims ≤ 10 V). MVP scope
is the MOT channel only.

---

## 1. Topology

![MVP system schematic: SE-600-5 supply, H-bridge with body diodes and the two coils (LEM on the mid lead, varistor across each coil), low-side pass bank with ballast and TVS, OPA994 gate stage, LEM/burden/AD8421 sensing chain, safety logic board, isolated gate drivers, Red Pitaya and control computer](img/mvp_schematic.svg)

*MVP system schematic (MOT channel), checked against the hardware
2026-11-10. Thick lines: 100 A path; copper: bus bars; solid blue: analog
signals; dashed blue: 3.3 V logic. Body diodes are drawn because they set
the turn-off behaviour (3.3).*

### 1.1 High-current loop — the pass bank sits LOW-SIDE, after the bridge and coils

```
 SE-600-5 (+5 V) ══4/0══► RAIL BAR ──D Q1 S──► OUT BAR A ══4/0══► coil 1 ──mid lead──► coil 2 ══4/0══► OUT BAR B
                              └──D Q2 S──► OUT BAR B          [varistor 1]  (LEM LF 310-S)  [varistor 2]
                         OUT BAR A ──D Q3 S──┐   OUT BAR B ──D Q4 S──┐
                                             └──────► RETURN BAR ◄────┘
 RETURN BAR ══4/0══► pass-bank DRAIN bar ─12 AWG, equal length─► 6× [IXTN200N10L2 D→S → MP930 0.03 Ω] ─► SOURCE bar ══4/0══► supply − (0 V)
                                5KP12CA TVS: drain bar ↔ source bar (across the whole bank)
```

- **Forward:** Q1 + Q4 on → rail → A → coil 1 → LEM → coil 2 → B → return.
  **Reverse:** Q2 + Q3 on → rail → B → coil 2 → LEM → coil 1 → A → return.
- Both MOT coils are in series inside one bridge, so the whole field
  reverses — that is what lets the MOT field go to either side of zero.
- The LEM sits on the lead between the two coils; every point of the series
  loop carries the same current, so it reads the true *signed* coil current.
- Each varistor sits across its own coil's terminals.
- Every pass-bank MOSFET is referenced to supply 0 V, so its driving op-amp
  needs no floating supply. The bridge legs float, so they get isolated
  gate drivers (section 4).

### 1.2 Control signals

```
 control computer AO ─÷10─► IN2 (setpoint)          LEM ─► 10 Ω burden ─► AD8421 ─► IN1 (measured I)
 Red Pitaya OUT1 ─► OPA994, non-inverting gain ≈ 8 ─► 24.9 Ω per gate ─► 6 gates (47 kΩ to source)
 Red Pitaya E1 DIO0/1/6 ─► breakout logic board (section 5) ─► 4× UCC5350 ─► bridge gates
```

---

## 2. Parts as built (MOT channel)

| Block | Part | Key numbers / status |
|---|---|---|
| Supply | Mean Well **SE-600-5**, 5 V / 100 A fixed switcher | ~150 mV p-p ripple at ~50 kHz. Reordered 2026-11-02 after the wrong model arrived. (Older notes say "6 V"; the ordered unit is 5 V.) |
| Main cabling | ~20 ft **4/0 AWG** Cu, Panduit lugs, hydraulic crimp | ≈ 0.049 mΩ/ft → ~2 mΩ round trip plus contacts. (Older notes say 4 AWG; the order is 4/0.) |
| Pass MOSFETs | **6 × IXYS IXTN200N10L2** (SOT-227), thermal paste on a water-cooled plate | V_DSS 100 V, I_D25 178 A, P_D 800 W, R_DS(on) 11 mΩ at 10 V, designed for linear operation. 10 bought and matched (2.1). **Installed: #1, 2, 4, 7, 9, 10** |
| Gate damping R1 | 6 × Yageo MFR-25 **24.9 Ω**, at each gate pin | Lower bound 2√(L/C) ≈ 4.2 Ω for 100 nH, C_iss 23 nF |
| Ballast R2 | 6 × Caddock **MP930-0.030** (0.030 Ω, 30 W), plate-mounted | 0.5 V and ~8.4 W each at 16.7 A per FET. **Leads are fragile — don't yank or bend.** |
| Gate pull-down R4 | 6 × **47 kΩ** gate→source | Discharge τ ≈ 1 ms with the op-amp unpowered |
| Op-amp stage | **OPA994IDR** on perfboard beside the plate, BNC in from OUT1. Non-inverting, R_f 6.98 kΩ / R_g 1 kΩ (R_g to supply 0 V) → **gain ≈ 8** | Maps OUT1 0…1 V to 0…8 V gate command. **No inner current loop** — no shunt or current-sense amp is in the parts list; the stage is open-loop volts-to-gate. Op-amp supply rail not yet documented. |
| Bank TVS | Littelfuse **5KP12CA** (bidirectional, V_RWM 12 V, V_BR ≥ 13.3 V), between the two bank bus bars | See section 3.3 for when it does (and doesn't) conduct |
| Per-FET legs | 12 AWG, **equal length**, crimp rings, to covered bus bars "input → drain" / "output → to source" | Unequal legs unbalance the sharing. Gate/source leads and ballast soldered. |
| Bridge MOSFETs | **4 × IXFN420N10T** (SOT-227) on their own water-cooled plate | V_DSS 100 V, I_D 420 A, R_DS(on) 2.3 mΩ. **Installed: #2, 5, 6, 9** |
| Bridge bus bars | Rail, return, out A, out B; 4/0 lugs on 1/4-20 studs | Terminal bus bars ordered 2026-11-06 |
| Gate drivers | **UCC5350SBDR** isolated, one per switch, 3.3 V logic in | 10 Ω turn-on / 4.7 Ω turn-off, 10 kΩ gate–source. Gate high only when IN+ high AND IN− low |
| Driver supplies | **RO-0515S** isolated 5 V → 15 V, one per driver, 1 µF ‖ 10 µF | Each floats on its own MOSFET's source |
| Varistors | **EPCOS B72220S0200K101** (S20K20), one across each coil | V_V(1 mA) ≈ 33 V, ~80 V at 100 A, bidirectional |
| Current sensor | **LEM LF 310-S** | 2000:1 (0.5 mA/A), DC–100 kHz, 0.05 % linearity |
| Burden | **MPR24000Z1009BCU00**, **10 Ω**, 5 ppm/°C | 0.5 V at 100 A |
| In-amp | **AD8421BRZ** → IN1 | Gain not fixed yet; assume G = 1, keep it a parameter |
| Mounting | SOT-227 #8-32, MP930 #4-40, plates 1/4-20 | |

### 2.1 Measured MOSFET thresholds (2026-11-02)

Method: gate tied to drain, series R_set from a bench supply, DMM across
gate–source; V_GS(th) is the reading once (V_supply − V_GS)/R_set equals
the datasheet test current.

| Set | Test current | Range measured | Selected | Spread in selection |
|---|---|---|---|---|
| Pass bank IXTN200N10L2 (spec 2.0–4.5 V) | 3 mA | 3.35–3.56 V | 1, 2, 4, 7, 9, 10 (3.50–3.56 V) | **0.06 V** |
| Bridge IXFN420N10T (spec 2.5–5.0 V) | 8 mA | 3.51–3.59 V | 2, 5, 6, 9 (3.51–3.52 V) | 0.01 V |

Current sharing in the bank: with the ballast dominant, the spread in
current is ΔI/I ≤ ΔV_th / V_ballast = 0.06 / 0.5 ≈ **12 % at 100 A**
(vs. 32 % budgeted for a 0.25 V spread). The spread grows as 1/I at lower
current, where the per-FET dissipation is small enough not to matter. The
model keeps the per-device thresholds as a list so per-FET sharing can be
simulated.

---

## 3. Pass-bank behaviour: what OUT1 actually does

### 3.1 A voltage-controlled current source with a dead zone

OUT1 → op-amp (×8) → gate → 6 FETs in linear mode, each with 0.03 Ω
ballast to the common source bar. Per FET:

  I = g_fs (V_gate − V_th) / (1 + g_fs·R2)

- **Dead zone:** nothing flows until V_gate > V_th ≈ 3.53 V, i.e. OUT1
  above ≈ 0.44 V (≈ 0.22 V of nominal DAC range if OUT1 is unterminated —
  3.2).
- **Transconductance:** with g_fs·R2 ≫ 1 the ballast dominates and each FET
  gives ≈ 1/R2 = 33 A per volt of gate, so the bank is **≤ 200 A/V at the
  gate, ≤ 1600 A/V at OUT1**. At 100 A, V_gate ≈ V_th + 0.5–0.7 V ≈ 4.1–4.2 V.
  Most of the 0–8 V gate range is unused headroom.
- **Plant structure.** While the bank has voltage headroom (compliance), the
  coil current follows G_pass·(OUT1 − dead zone) with only the gate RC
  (24.9 Ω × 23 nF ≈ 0.6 µs), op-amp and LEM lags. The coil's L/R pole is
  *not* in the loop — L only shows up as slew limits at the compliance
  edges (3.3, 3.4). This is a different plant from the one the PI was
  commissioned on.
- **Normalized plant gain** = G_pass × (0.5 mA/A × R_burden × G_inamp)
  ≈ up to **~8** with G = 1. The 2026-08-27 loopback plant had a gain of
  **2.03** (OUT1 unterminated into IN1 doubles — see the lab notes), so
  the commissioned kp = 0.5 / ki = 15080 must be scaled down by roughly
  **G_pass·sensor / 2.03 ≈ up to 4×** for the first real-load run, and
  treated as unverified until the bench step response. (The rewrite brief
  says "gain 1 → scale by up to 8"; the measured loopback factor was 2.03.)

**⚠ REVIEW — actuator resolution and ground sensitivity.** The gain-8 stage
spends almost all of OUT1's range in the dead zone and headroom: 0 → 100 A
lives in roughly OUT1 0.44 → 0.53 V, about 740 DAC counts. One OUT1 count
(122 µV terminated) is then **≈ 0.14–0.2 A of coil current** (0.27–0.39 A
unterminated) — 0.14–0.4 % of rated per count, larger than the 0.1 %
stability spec. Because the plant has no L/R filtering (above), the loop's
normal ±1-count hunting appears directly in the coil current instead of
being averaged away as in a voltage-driven coil. The same gain applies to
any voltage difference between the Red Pitaya's ground and supply 0 V
(OUT1 is single-ended; R_g is referenced to supply 0 V): **~1.1–1.6 A per
millivolt**. Static offsets are absorbed by the integrator; ground *noise*
from 100 A return currents and the 50 kHz switcher is not. Not a redesign
proposal — a bench measurement to make early: current noise at a steady
hold, against the spec. If it fails, the low-cost fixes are a lower op-amp
gain with a fixed ~3.4 V gate bias (OUT1 then spans the useful region:
~8× better resolution and ground immunity), and/or a differential receiver
for OUT1.

**Dead zone vs. the integrator.** The PI integrator absorbs the dead zone,
but with the anti-windup floor at OUT1 = 0 it must climb ~0.44 V of dead
zone before any current flows: for a small step from zero (e.g. 10 A) at
the commissioned ki that is ~0.6 ms, and several ms once ki is scaled down
for the real plant. Candidate firmware remedy for step 3 of the rewrite: a
configurable OUT1 bias/floor just below the dead-zone edge while in RUN
(outputs still exactly zero outside RUN, so the reset-off invariant is
unchanged).

Model parameters: `V_th` (per FET, measured), `g_fs` (datasheet,
PROVISIONAL), `R2`, `N_fets`, `gain_opamp`, `out1_termination`; `G_pass`
(A/V at OUT1) and the dead-zone volts are derived.

### 3.2 OUT1 loading — check before the first power-on

OUT1 is specified for ±1 V into 50 Ω. Into the op-amp's high-impedance
input it swings about 2×: 0…2 V at OUT1, **0…16 V at the gate**. Either
terminate OUT1 in 50 Ω at the op-amp or halve the gain, and record the
choice in `channels.toml` (it changes `G_pass`). This check is part of
bring-up.

### 3.3 How current goes down in the MVP

There is no active clamp: the actuator is unipolar — the bank sources
current or sources less.

**⚠ REVIEW — the bridge freewheels; the bank TVS does not set the turn-off.**
The rewrite brief (v2, §3.3) concludes that when the bank throttles back,
its drain voltage rises until the 5KP12CA conducts (~13 V), giving
~0.5 A/µs down-ramps and a 0.1–0.2 ms snap-off. That misses a path through
the H-bridge's own body diodes. With the forward diagonal on (Q1 + Q4) and
the bank turning off:

- node A is held at ≈ V_rail by Q1;
- as the return bar rises, **Q3's body diode** (anode = its source = return
  bar, cathode = its drain = A) starts conducting once the return bar
  reaches ≈ V_rail + 0.7 V;
- the coil current then circulates A → coils → B → Q4 → return → Q3 diode → A,
  a loop that contains neither the bank, the TVS, nor the supply. (Q2's
  body diode with Q1 gives an equivalent loop on the high side.)

So the bank's drain bar is clamped near **V_rail + ~0.7 V ≈ 5.7 V** — far
below the TVS's 13.3 V — and the coil decays through the freewheel loop:

  L dI/dt = −(V_f + I·R_fw), R_fw ≈ R_coil + R_bridge + coil-side cable

i.e. roughly **L/R-type decay**, not TVS-limited. With provisional values
(V_f ≈ 0.8 V, R_fw ≈ 10 mΩ for the MOT): 100 A → 0 in **≈ 1.3 ms**
(Z shim 60 A → 0 ≈ 1.5 ms; X/Y ≈ 2.7 ms). This applies equally to
servoed down-ramps (the loop stays in control but can only lower current
at the freewheel rate) and to a snap-off with OUT1 at zero. Consequences:

- the bank's FETs see at most ~6 V during down-ramps (less stress than the
  brief's 220 W/FET estimate);
- raising the TVS voltage (e.g. 5KP30CA) would **not** speed up snap-offs;
- the varistors stay off either way (agrees with the brief). Their real job
  is an *open coil lead* — a lug coming loose between bridge and coil —
  where they give the coil current somewhere to go instead of an arc;
- the bank TVS is harmless and can stay as a passive backstop, but it does
  not shape normal operation. (That matches its stated purpose in the MVP
  notes — protecting the bank from backflow — rather than the rewrite
  brief's turn-down role.)

The decisive bench test: scope the drain bar and node A during a snap-off
from a moderate current. Drain bar at ~V_rail + 0.7 V and a ~ms decay →
freewheel (this analysis); drain bar at ~13 V and ~0.2 ms → TVS (the
brief's). **If ~1 ms snap-offs are too slow for the experiment**, the
known ways to go faster are (a) opening the whole bridge at current, which
pushes the coil energy back into the supply's output capacitors through
the body diodes (OVP risk — see below), or (b) an absorber across the
bridge input with a blocking diode at the supply — i.e. the active
clamp/TVS arrangement of the full architecture, now in future_upgrades.md.
Either is a design decision, not something to slip in.

**Bridge opened at current** (heartbeat-latch trip): with all four bridge
switches off, the coil current flows through Q2's and Q3's body diodes,
backward into the supply's + terminal, and through the bank's body diodes.
The coil sees ≈ −(V_rail + 3 diode drops) ≈ −7.5 V, so it decays in
~0.2 ms, but its ~0.08 J goes **into the SE-600-5's output capacitors** and
may trip the supply's overvoltage protection. Bring-up risk and open
question (section 8).

Firmware consequences (unchanged by the freewheel question):

- The PI output's useful range is 0 … +clamp. When u ≤ 0, OUT1 = 0, and the
  integrator is anti-wound at the zero bound exactly as at the upper clamp.
- The output mux's OUT2 branch is disabled in the MVP (OUT2 ≡ 0); the
  deadband becomes an OUT1-only turn-on hysteresis.
- New status flag `u_floor`: PI output at or below zero, current decaying
  only.

### 3.4 Ramp up

dI/dt ≈ (V_rail − I·R_total − V_ballast − V_DS,min)/L. At zero current
that is ≈ 5 V / 16 µH ≈ 0.3 A/µs. At 100 A the steady-state FET drop is
2.8–3.2 V (section 7), so ~2.6–3.0 V of slack remains and dI/dt is still
≈ 0.17 A/µs. A 0 → 100 A step therefore takes ≈ 0.4 ms, compliance-limited
rather than loop-limited, with no boost rail. (The rewrite brief says the
slack falls to ~1–1.5 V near 100 A; that contradicts its own power budget
in section 7, which this document follows.) The model represents the
compliance edge explicitly.

### 3.5 What the "hard clamp" means now

**⚠ REVIEW — the clamp limits the gate command, not the current.**
Safety invariant 1 clamps the PI output (the OUT1 command) at a fixed DAC
count. In the original design that count equalled 100 % of rated current
because the actuator was specified as "0.8 V ≈ rated". In the MVP the
command-to-current map is G_pass and the dead zone, which depend on g_fs
and on V_th — and V_th falls with temperature (typically ~−5 mV/K for
power MOSFETs; check the datasheet), so a 50 K warm-up shifts the bank by
~0.25 V of gate ≈ 50 A at the same command. The rewrite brief's "clamp =
4096 counts" is the 100 A point on the *sensor* scale (IN1), not on the
command scale; at 4096 command counts the bank would give ~66–94 A
terminated, and with OUT1 unterminated the gate would reach 8 V and the
bank would sit fully on, compliance-limited at ~200 A — leaving the
supply's own overcurrent protection as the only limit.

What this means in practice:

- the clamp value must be **calibrated from the measured G_pass and dead
  zone**, with temperature margin, and is PROVISIONAL until then — never
  hardcoded from the sensor scale;
- the real hard current limit in the MVP is the **SE-600-5's overcurrent
  protection**;
- candidate firmware remedy for step 3: a fast trip on *measured* current
  (IN1 above ~105 % of rated → OUT1 to zero and latch), which restores the
  intent of invariant 1 independently of the actuator's calibration.

---

## 4. H-bridge

- One IXFN420N10T per switch, driven fully on or off through isolated
  UCC5350 drivers on RO-0515S floating 15 V supplies (driver input side on
  +5 V logic).
- **Flips only at zero current, never at current.** Down-ramps go through
  the pass bank (and the freewheel path, 3.3).
- Complementary diagonals come from one polarity bit (DIO0_P) through a
  74HC04 on the logic board.

| Switch | IN+ (pin 2) | Drain | Source = local 0 V of its driver | Conducts when |
|---|---|---|---|---|
| Q1 | POL (DIO0_P, E1 pin 3) | rail bar (+5 V) | node A (left coil) | POL high, DISABLE low |
| Q2 | NOT-POL (74HC04) | rail bar (+5 V) | node B (right coil) | POL low, DISABLE low |
| Q3 | NOT-POL | node A | return bar (= bank input, **not ground**) | POL low, DISABLE low |
| Q4 | POL | node B | return bar | POL high, DISABLE low |

- **UCC5350SB pins:** 1 VCC1 (logic, shared); 2 IN+; 3 IN− = **DISABLE,
  active high, from the 74HC10 output, shared by all four**; 4 GND1 (logic
  GND, shared); 5 VCC2 ← RO-0515S +Vout (+15 V); 6 OUTH → 10 Ω → gate;
  7 OUTL → 4.7 Ω → gate; 8 VEE2 ← RO-0515S −Vout, tied to that MOSFET's
  source.
- **RO-0515S pins:** 1 −Vin (logic GND, shared); 2 +Vin (+5 V, shared);
  3 −Vout → driver pin 8 / MOSFET source; 4 +Vout → driver pin 5.
- **The four floating grounds must never touch each other or logic GND.**
- **No dead time is generated on the logic board.** The flip state
  machine's enable-low window (`deadtime`, 1 µs default) is the only
  shoot-through protection, so `deadtime_us` must stay ≥ driver
  propagation + MOSFET switching time (scope V_GS of all four during a
  flip at bring-up). The testbench asserts every polarity edge falls
  strictly inside the enable-low window.

---

## 5. Breakout / safety logic board (built and tested 2026-11-03)

Solderable breadboard, DIP sockets, +3.3 V logic rail. Chips top to
bottom: SN74HC04N → SN74HC10N → CD74HC123E → SN74HC74N → SN74HC04N. Every
E1 line has a 10 kΩ pull-down.

| E1 pin | Signal | Logic |
|---|---|---|
| 3 | DIO0_P polarity | 74HC04 #1 → complement; POL → Q1/Q4 diagonal, NOT-POL → Q2/Q3 |
| 5 | DIO1_P bridge enable | Input to the 74HC10 3-input NAND |
| 15 | DIO6_P heartbeat (~954 Hz) | Retriggers the CD74HC123E monostable; its Q stays high while the heartbeat runs |
| 13 | DIO5_P fault in | **Not wired in the MVP** (decision 2026-11-10) |

- **Monostable timing:** R_x/C_x = 220 kΩ / 100 nF, **measured drop
  ≈ 7.6 ms, consistent** (Construction notes, 2026-11-03). It was first
  built with 68 kΩ, which timed out in only ~1.1 ms — about one heartbeat
  period, so a pulled heartbeat could recover on an edge. (The CD74HC123
  is specified at 5 V and times differently on the 3.3 V rail, so the
  datasheet formula doesn't predict either value; trust the measurement.)
- **74HC74 latch** with a B3F-1000 reset button: when the monostable drops,
  the latch trips and stays tripped until someone presses reset. The
  board never re-arms itself and **powers up tripped**.
- **74HC10 NAND:** drivers enabled (IN− low) only if DIO1_P AND monostable
  OK AND latch not tripped.
- **74HC04 #2 drives the status LEDs** (1 kΩ each): green = ALIVE, amber =
  BRIDGE EN, red = FAULT (latch tripped).

LED acceptance test as observed (goes into bringup.md):

| Step | Action | Green | Amber | Red |
|---|---|---|---|---|
| 1 | Power board, heartbeat running | on | off | **on** (powers up in fault) |
| 2 | Press reset | on | off | off |
| 3 | Arm (DIO4_P to 3.3 V) → FSM asserts DIO1_P | on | on | off |
| 4 | Pull DIO6_P | off | off | on |
| 5 | Re-plug DIO6_P | on | off | on (stays latched) |
| 6 | Press reset | on | **on** | off |

**Power-up and fault behaviour (decision 2026-11-10):** no fault feedback
from the latch to the Red Pitaya in the MVP. Instead, **the Red Pitaya
outputs nothing until explicitly armed**:

- At bitstream load and after any reset: OUT1 = OUT2 = 0, every DIO output
  low (bridge disabled), integrator cleared, FSM in IDLE, `servo_enable` =
  0 (safety invariant 2, unchanged).
- Leaving IDLE needs both `servo_enable` from the host **and** an arm
  (DIO4) *edge*; an arm level already high at load does not count.
- Without a heartbeat (Red Pitaya off, rebooting, wrong or not-yet-reloaded
  bitstream) the monostable holds the latch tripped and the bridge stays
  open whatever OUT1 does.
- **Accepted gap:** if the heartbeat is lost while the Red Pitaya keeps
  running (e.g. a loose DIO6 wire, as in test step 4), the firmware does
  not know the bridge opened; it keeps integrating against zero current,
  so pressing reset re-closes the bridge with OUT1 possibly at the clamp.
  **Operating rule: disarm (DIO4 low) before pressing reset.**

The full-design interlock chain (flow switch, thermal cutouts, leak pad,
OC/OT comparators → latching relay) is **not in the MVP**; the heartbeat
latch is the only automatic trip.

---

## 6. Sensing and scaling

- LEM 0.5 mA/A → 10 Ω → AD8421 (G = 1): **I_FS = 200 A at +1.000 V on
  IN1** (the repo previously assumed 125 A). One ADC count = 24.4 mA; 100 A
  sits at 4096 counts *on the sensor scale*; shims at 60 A would use only
  0.3 V of the ±1 V range.
- `I_FS = 1 V / (0.5 mA/A × R_burden × G_inamp)` becomes a derived value.
  Alternative on the open list: in-amp G = 2 → I_FS = 100 A, finer
  resolution, rated current at full scale (no headroom to *measure*
  overshoot).
- IN2 must keep the **same scaling as IN1**, so the control computer's
  volts-per-amp changes with it: at I_FS = 200 A, through the ÷10 divider,
  the computer outputs **20 A per volt** (100 A = 5 V).
- **Measured offset:** on 2026-11-03 `check mot` read +0.179 A with the
  servo disabled at the old I_FS = 125 A, i.e. ≈ 1.4 mV on IN1 — ≈ 0.29 A
  at the new scale, more than half the ±0.5 A flip zero window. An offset
  calibration (AOUT0 trim or host-side constant) is needed before the zero
  window is trusted for flips.

---

## 7. Supply, ripple, power and cooling

**Ripple pass-through**, worst case (the bank rejects nothing; only the
coil impedance opposes: I_noise = V_ripple / 2πfL). In practice the bank,
being a current source, should reject much of this — it lands across V_DS.

| Channel | SE-600-5 (150 mV, ~50 kHz) | Acopian W5MT100 (80 mV, 55 kHz) | Delta SM15-100 (8 mV, 100 kHz) |
|---|---|---|---|
| MOT 16 µH, 100 A | 30 mA / 0.030 % | 14 mA / 0.014 % | 0.8 mA / 0.0008 % |
| Z shim 29 µH, 60 A | 16 mA / 0.027 % | 8 mA / 0.013 % | 0.4 mA / 0.0007 % |
| X/Y shim 57 µH, 60 A | 8 mA / 0.014 % | 4 mA / 0.007 % | 0.2 mA / 0.0004 % |

Mean Well is acceptable for the MOT; Delta-class supplies are reserved for
science-side coils.

**Power budget at 100 A continuous, MOT** (replaces interface brief §1c):

| Term | Value | Power |
|---|---|---|
| From rail | 5 V × 100 A | 500 W |
| Coil pair | 6.4 mΩ (PROVISIONAL) | 64 W |
| Bridge, 2 switches conducting | 2 × 2.3–4 mΩ (hot) | 50–80 W |
| Cable + contacts | ~2–3 mΩ | 20–30 W |
| Ballast | 6 × 8.4 W | 50 W |
| **Pass MOSFETs** | remainder ≈ 2.8–3.2 V × 100 A | **≈ 280–320 W (≈ 47–53 W each)** |

The design limit is ≤ 100 W per FET (plate at 50 °C, 0.2 °C/W, 40 %
derating), so steady state is inside limits **with water cooling**. The
plates are machined and the FETs mounted (2026-11-04). **The first test
round runs without water flow**: bring-up uses short pulses at reduced
current with an explicit duty limit.

---

## 8. Open unknowns — parameters only, never hardcoded

- Coil R and L (Kelvin + LCR), 4/0 cable and contact resistance → `R_total`
  and the compliance at 100 A.
- `g_fs` of the IXTN200N10L2 in this regime, the measured `G_pass` and dead
  zone, their temperature dependence, and whether OUT1 is 50 Ω-terminated
  → also sets the OUT1 clamp value (3.5).
- OPA994 supply rail (not in the parts list; must exceed the maximum gate
  command).
- In-amp gain (1 vs 2) → I_FS 200 A vs 100 A.
- **Turn-down path** (3.3): freewheel vs TVS — settled by one scope
  capture; determines snap-off time and the model's down-ramp physics.
- **Bridge-open-at-current energy path**: SE-600-5 output-capacitor
  overvoltage / OVP behaviour; whether a reverse-energy absorber is needed.
- Actuator resolution / ground noise at a steady hold vs the 0.1 % spec
  (3.1).
- The IN1 offset (6).
- Whether a hold capacitor goes across the rail (`C_hold`).
- Chamber eddy-current settling → `settle_us`.
- Uncooled duty-cycle limit for the first test round.
- Shim-channel supplies (HP/Agilent 6671A candidates) — outside the MVP.
