> **Repo note (2026-11-10).** This is the rewrite brief exactly as received,
> kept as the working plan for the remaining rewrite steps (§11). The
> architecture description it contains has been written up in
> [mvp_architecture.md](mvp_architecture.md), which is authoritative where
> the two differ. During step 1 the following parts of this brief were found
> inconsistent and are flagged **⚠ REVIEW** there, pending a decision or a
> bench measurement:
>
> - **§3.3 turn-down physics** — the H-bridge body diodes form a freewheel
>   loop that clamps the bank near V_rail + 0.7 V, so the 5KP12CA never
>   conducts in normal operation and down-ramps/snap-offs are ~1 ms L/R-type
>   decays, not TVS-limited 0.2 ms ramps. The step-2 model tests listed in
>   §11 ("servoed down-ramp at the TVS-limited rate", "snap-off decay matches
>   the TVS curve") depend on this and must be revised before step 2.
> - **§6 clamp = 4096 counts** — that is 100 A on the sensor (IN1) scale; the
>   clamp acts on the OUT1 command, whose current depends on G_pass, the dead
>   zone and FET temperature. The clamp must be calibrated, and the supply's
>   OCP is the true hard limit in the MVP.
> - **§3.1 loopback plant gain** — measured 2.03 on 2026-08-27, not 1, so the
>   gain rescale factor is up to ~4, not ~8.
> - **§3.4 ramp-up slack** — "~1–1.5 V near 100 A" contradicts the §7 power
>   budget (2.8–3.2 V across the FETs at 100 A).
> - **§5 monostable timeout** — 1.1 ms at 68 kΩ predicts ~3.6 ms at 220 kΩ,
>   not ~5 ms; measure.
> - New risk, not in the brief: the gain-8 op-amp stage makes one OUT1 DAC
>   count ≈ 0.14–0.4 % of rated current, and ~1–1.6 A per mV of ground
>   difference (mvp_architecture.md §3.1).

---

# Rewrite brief v2: coil-servo repo → MVP power stage as built (Nov 2026)

*Paste this into a Claude Code session opened in `Rb-Cs-lab/coil-servo`. It replaces `BOOTSTRAP.md` and the power-stage parts of every doc. The firmware core (PI, flip FSM, heartbeat, output clamp, register hub) was commissioned on 2026-08-27 and stays. The power stage it drives has changed, and the repo has to describe, model and test the hardware that is actually being built.*

*Sources of truth, newest first:*
1. *OneNote "Construction" section (Santi + Jeff, 2026-11-02 to 11-06): MOSFET threshold tests, RP breakout board build and test, water-cooled plate machining, bus-bar plan, pass-bank assembly (11-04), H-bridge assembly (11-06).*
4. *Decisions from Santi, 2026-11-10: rescale the PI gains by G_pass (§3.1); no fault feedback wiring, rely on outputs-off at power-up (§5).*
2. *OneNote "Minimum Viable Product" and "MVP Component List" (Santi + Jeff, 2026-10-16).*
3. *Drive sheet "components to purchase" → tab "coil driver order".*

*Where v2 corrects v1 of this brief, the correction is flagged with a bracketed note.*

---

## 0. What changed and why

The repo documents the **full architecture** from Aug–Sep 2026: a pass bank with per-FET source-sense op-amp loops, an **active clamp on OUT2** for controlled down-ramps, a **~3 V hold rail plus ~15 V boost capacitor** switched by DIO2_P, and an interlock chain with a latching relay.

In Oct 2026, after reviews with Jim and Ben Brown, Santi and Jeff decided to **build a simplified MVP first**, because it was unclear whether the active clamp was needed at all. Parts were bought in Nov 2026 and assembly is under way. **Reproduce the MVP in the repo as it is. Do not redesign it.** The extras from the full architecture (active clamp, boost rail, source-sense loops, hold capacitor, relay interlock chain) move to `docs/future_upgrades.md`. No main doc, config default, model default or testbench default may assume them.

**MVP scope:** the MOT channel only (one Red Pitaya, one supply, one H-bridge, one pass bank). The shim channels keep their parameter rows but are not being built yet.

**Fixed requirements:** 100 A max on the MOT coils, 60 A max on the shims, and one fixed low-voltage rail (MOT ≤ 6 V, shims ≤ 10 V).

---

## 1. Topology

### 1.1 High-current loop. The pass bank is LOW-SIDE, after the bridge and coils

```
 SE-600-5 (+5 V) ══4/0══► RAIL BAR ──D Q1 S──► OUT BAR A ══4/0══► coil 1 (top) ──mid lead──► coil 2 (bottom) ══4/0══► OUT BAR B
                               └──D Q2 S──► OUT BAR B                 [varistor 1]   (LEM LF 310-S)   [varistor 2]
                          OUT BAR A ──D Q3 S──┐   OUT BAR B ──D Q4 S──┐
                                              └──────► RETURN BAR ◄────┘
 RETURN BAR ══4/0══► pass-bank DRAIN bar ─12 AWG (equal length)─► 6× [IXTN200N10L2 D→S → MP930 0.03 Ω ballast] ─► SOURCE bar ══4/0══► supply −  (0 V)
                                  5KP12CA TVS: drain bar ↔ source bar (across the whole bank)
```

- **Forward:** Q1 + Q4 on → rail → A → coil 1 → LEM → coil 2 → B → return.
  **Reverse:** Q2 + Q3 on → rail → B → coil 2 → LEM → coil 1 → A → return.
- Both MOT coils are in series inside one bridge, so the whole field reverses. That is what lets the MOT move to either side of zero.
- The LEM sits on the lead between the two coils. Every point in the series loop carries the same current.
- Each varistor sits at its own coil's terminals.
- Every pass-bank MOSFET source is referenced to supply 0 V, so the op-amp needs no floating supply. The bridge legs float, so they need isolated gate drivers (§4).

### 1.2 Control signals

```
 computer control AO ─÷10─► IN2 (setpoint)          LEM ─► 10 Ω burden ─► AD8421 ─► IN1 (measured I)
 Red Pitaya OUT1 ─► OPA994 ×8 (non-inv) ─► R1 24.9 Ω ─► 6 gates (R4 47 kΩ to source)
 Red Pitaya E1 DIO0/1/6 ─► breakout logic board (§5) ─► 4× UCC5350 ─► bridge gates
```

---

## 2. Parts as built (per MOT channel)

| Block | Part | Key numbers / status |
|---|---|---|
| Supply | Mean Well **SE-600-5**, 5 V / 100 A fixed switcher, 120 V outlet | ~150 mV p-p ripple at ~50 kHz. Reordered 2026-11-02 after the wrong model arrived. *(The design note's arithmetic and the logic-board diagram say "6 V". The ordered unit is 5 V.)* |
| Main cabling | ~20 ft **4/0 AWG** Cu, Panduit lugs, hydraulic crimp | ≈ 0.049 mΩ/ft, so ~2 mΩ round trip plus contacts. *(The design note says 4 AWG. The Nov order is 4/0, so use 4/0.)* |
| Pass MOSFETs | **6 × IXYS IXTN200N10L2** (SOT-227), bolted with thermal paste to a water-cooled plate | V_DSS 100 V, I_D25 178 A, P_D 800 W, R_DS(on) 11 mΩ at 10 V, "designed for linear operation". 10 bought and matched (§2.1). **Installed devices: #1, 2, 4, 7, 9, 10** |
| R1 gate damping | 6 × Yageo MFR-25 **24.9 Ω**, at the gate pin | Lower bound 2√(L/C) ≈ 4.2 Ω (100 nH, C_iss 23 nF) |
| R2 ballast | 6 × Caddock **MP930-0.030** (0.030 Ω, 30 W), plate-mounted | 0.5 V drop at 16.7 A per FET, ~8.4 W each |
| R4 gate pull-down | 6 × **47 kΩ** gate→source | Discharge τ ≈ 1 ms |
| Op-amp stage | **OPA994IDR** on a breakout, non-inverting, R_f 6.98 kΩ / R_g 1 kΩ → **gain ≈ 8**, R_g to supply 0 V | Maps OUT1 0…1 V to a 0…8 V gate command. **No inner current loop:** the R3 shunt and current-sense amp in the concept text are NOT in the parts list. Model the stage as open-loop voltage-to-gate. Op-amp supply rail is not documented (§9). |
| TVS (bank) | Littelfuse **5KP12CA** (bidirectional; V_RWM 12 V, V_BR ≥ 13.3 V), **installed between the two pass-bank bus bars** | *(The full-architecture design used 5KP30A. The MVP order is 5KP12CA.)* This part sets the turn-off behaviour (§3.3). |
| Per-FET legs | 12 AWG, **equal length**, crimp ring terminals, to two covered terminal bus bars labelled **"input → drain"** and **"output → to source"** | Unequal lengths unbalance the sharing. Gate and source leads and the ballast resistors are soldered. **MP930 leads are fragile: don't yank or bend them hard.** |
| Op-amp board | OPA994 on a small perfboard beside the plate, BNC input from OUT1. SOIC pins: 2 IN−, 3 IN+, 4 VCC−, 6 OUT, 7 VCC+ | Supply rail still to be documented (§9) |
| Bridge MOSFETs | **4 × IXFN420N10T** (SOT-227) on their own water-cooled plate | V_DSS 100 V, I_D 420 A, R_DS(on) 2.3 mΩ. **Installed devices: #2, 5, 6, 9** |
| Bridge bus bars | Rail, return, out A, out B; 4/0 lugs on 1/4-20 studs | Terminal bus bars ordered 2026-11-06 (Amazon B0051P1L52) |
| Gate drivers | **UCC5350SBDR** isolated, one per leg; 3.3 V logic in | 10 Ω turn-on / 4.7 Ω turn-off gate resistors, 10 kΩ gate–source. Gate high only when IN+ is high AND IN− is low. |
| Driver supplies | **RO-0515S** isolated 5 V→15 V DC-DC, one per driver, 1 µF ‖ 10 µF decoupling | Each floats on its own MOSFET's source |
| Varistor | **EPCOS B72220S0200K101** (S20K20), one across each coil | V_DC max 26 V. V_V(1 mA) ≈ 33 V, ~80 V at 100 A. Bidirectional. |
| Current sensor | **LEM LF 310-S** (all channels) | 2000:1, 0.5 mA/A, DC–100 kHz, 0.05 % linearity |
| Burden | **MPR24000Z1009BCU00**, **10 Ω**, 5 ppm/°C | 50 mA × 10 Ω = **0.5 V at 100 A** |
| In-amp | **AD8421BRZ** → IN1 | 0.2 µV/°C offset drift. Gain not fixed yet: assume **G = 1** and make it a parameter. |
| Mounting | Plate holes: SOT-227 **#8-32**, MP930 **#4-40**, plate mounting **1/4-20** | |

### 2.1 Measured MOSFET thresholds (2026-11-02)

Method: gate tied to drain, series R_set from a bench supply, and a DMM across gate–source. V_GS(th) is the DMM reading once (V_supply − V_GS)/R_set equals the datasheet test current.

| Set | Test current | Range measured | Selected devices | Spread within selection |
|---|---|---|---|---|
| Pass bank IXTN200N10L2 (spec 2.0–4.5 V) | 3 mA | 3.35–3.56 V | 1, 2, 4, 7, 9, 10 (3.50–3.56 V) | **0.06 V** |
| Bridge IXFN420N10T (spec 2.5–5.0 V) | 8 mA | 3.51–3.59 V | 2, 5, 6, 9 (3.51–3.52 V) | 0.01 V |

What this means for the model: set `V_th = 3.53 V` (pass bank, measured). From the design note's sharing formula, ΔI/I ≤ ΔV_th / V_ballast = 0.06 / 0.5 ≈ **12 %**, compared with the 32 % budgeted for a 0.25 V spread. Put the per-device values in `channels.py` as a list so per-FET sharing can be simulated.

---

## 3. Pass-bank behaviour: what OUT1 actually does

### 3.1 Transconductance with a dead zone

The path is OUT1 → OPA994 (×8) → gate → 6 FETs in linear mode with 0.03 Ω ballast → common source bar. Per FET:

  **I = g_fs (V_gate − V_th) / (1 + g_fs R2)**

So the bank is a **voltage-controlled current source** with a dead zone:

- Nothing flows until V_gate > V_th ≈ 3.53 V, i.e. OUT1 ≲ 0.44 V (or ≈ 0.22 V of nominal DAC range if OUT1 is not terminated, §3.2).
- Above that, current is roughly linear until the bank runs out of compliance against the rail.
- If g_fs·R2 ≫ 1, the ballast dominates. Each FET is then ≈ 1/R2 = 33 A/V at the gate, so the bank is **≤ 200 A/V at the gate and ≤ 1600 A/V at OUT1**.
- At 100 A that works out to V_gate ≈ V_th + 0.5–0.7 V ≈ 4.1–4.2 V, i.e. OUT1 ≈ 0.5 V. Most of the 0–8 V gate range is unused headroom.

The PI integrator absorbs the dead zone, but **the model must include it**. Parameters: `V_th` (per FET), `g_fs` (datasheet, PROVISIONAL), `R2`, `N_fets`, `gain_opamp`, `out1_termination`. `G_pass` (A/V at OUT1) and the dead-zone volts are derived from them.

**[v2 correction — plant structure]** v1 described the plant as "RL load with a natural pole near 100 Hz". That holds for a voltage actuator. This bank is a current actuator. While it has compliance, the coil current follows `G_pass·(OUT1 − dead zone)` with a lag set only by gate RC (~0.6 µs), op-amp bandwidth and LEM response. The L/R pole is not in the loop. L only shows up as **slew limits** at the compliance edges (§3.3). Consequences:

- The normalized plant gain is G_pass × R_burden × G_inamp × (2000:1)⁻¹, about **up to ~8**. The 2026-08-27 loopback had a plant gain of 1.
- **The loopback-derived kp = 0.5 and ki = 15080 do not transfer.** Start real-load tuning with gains scaled down by the measured G_pass ratio, and treat the result as unverified until the bench run.

### 3.2 OUT1 loading (check before the first power-on)

OUT1 is specified for ±1 V into 50 Ω. Into the op-amp's high-impedance input it swings about 2×, giving 0…2 V at OUT1 and **0…16 V at the gate**. Either terminate OUT1 in 50 Ω at the op-amp or halve the gain. Record which choice was made in `channels.toml` (it changes `G_pass`), and add the check to `bringup.md`.

### 3.3 How current goes down in the MVP

There is no active clamp. The actuator is unipolar: the bank sources current or sources less.

**[v2 correction — turn-down path]** v1 said down-ramps run at the L/R rate and snap-offs decay through the varistors at ~5 A/µs. That is wrong for the circuit as wired. Here is what actually happens (analysis, unverified until a scope looks at V_DS):

1. **Controlled down-ramp (servoed).** When the PI lowers the gate command, the bank resists the coil current and the drain-bar voltage rises above its ~3 V operating point. The coil sees L dI/dt = −(V_DS − V_rail + I·R_total). The bank is now acting as the clamp, so the loop **stays in control on the way down**.
   - The rate limit is set by how far V_DS can rise before the 5KP12CA conducts (≈ 13.3–20 V): |dI/dt|max ≈ (13.3 − 5)/16 µH ≈ **0.5 A/µs**, so 100 A → 0 takes ≈ 0.2 ms.
   - The FETs briefly dissipate up to ~13 V × 16.7 A ≈ 220 W each. Check this against the FBSOA (Fig. 14, T_C = 75 °C) for ~0.2 ms.
2. **Snap-off (PI output at 0, FETs off).** The coil drives the drain bar up until the **TVS across the bank** clamps (≈ 13.3 V at low current, up to ~20 V at I_PP). Decay is ≈ linear at (V_TVS − V_rail)/L ≈ 0.5–1 A/µs, so 100 A → 0 takes ~0.1–0.2 ms. The energy (½LI² ≈ 0.08 J plus V_rail·∫I dt) goes into the TVS. The servo is open-loop during the decay.
   *Why the varistors stay off (they are not the wrong part):* at turn-off the coil voltage rises until the **lowest-voltage** path conducts. The two paths are in parallel:

   | Path | Voltage needed before it conducts |
   |---|---|
   | TVS across the pass bank | V_TVS − V_rail ≈ 13.3 − 5 ≈ 8 V across the coil pair (≈ 4 V per coil) |
   | S20K20 across each coil | ≈ 33 V per coil at 1 mA, ~80 V at 100 A |

   The TVS wins by roughly 8×. No S20 varistor is low enough to beat it: the lowest, K11, still needs ~18 V. The varistors were sized for the original design, where nothing else clamped the coil.

   - If you want faster snap-offs, **raise the TVS, not the varistor.** Turn-off dI/dt ≈ (V_TVS − V_rail)/L. With a 5KP30A/CA (V_BR ≈ 33 V, the full-design part) that is ≈ 1.75 A/µs, so 100 A → 0 in ~60 µs. This stays well inside the 100 V FET rating. Make `V_tvs` a model parameter and leave the choice to the bench.
3. **The varistors do NOT conduct in either case.** The coil sees at most ~8–15 V, well below the S20K20's ~33 V knee. They only matter if the bridge opens at current (heartbeat-latch trip, §5). Even then, the body-diode path (Q2/Q3 body diodes, back through the supply, through the pass-bank body diodes) conducts at ≈ V_rail + 3 diode drops ≈ 7–8 V, before the varistors. That **dumps the coil energy into the SE-600-5's output capacitors** and may trip its OVP. Flag this as a bring-up risk and an open question (§9). Don't hide it.

Consequences for firmware:

- The PI output's useful range is **0 … +CLAMP**. When u ≤ 0, OUT1 = 0, and the integrator must be anti-wound at the zero bound exactly as it is at the upper clamp.
- Node F's `u < −D → OUT2 = −u` branch becomes `u ≤ 0 → OUT1 = 0, OUT2 = 0` while OUT2 is disabled. `D` becomes an OUT1-only turn-on hysteresis.
- Add STS flag `u_floor`: PI output at or below zero, decay only.

### 3.4 Ramp up

dI/dt ≈ (V_rail − I·R_total − V_DS,min − V_ballast)/L. That is ≈ 0.3 A/µs at zero current, falling toward zero near 100 A because the 5 V rail leaves only ~1–1.5 V of slack at full current (§7). With no boost, a 0 → 100 A step is compliance-limited, not loop-limited. Model the compliance edge explicitly.

---

## 4. H-bridge

- One IXFN420N10T per leg, switched fully on or off through isolated UCC5350 drivers on RO-0515S floating 15 V supplies (driver side fed from +5 V logic).
- **Flips only at zero current, never at current.** Down-ramps use the pass bank.
- Complementary diagonals come from one polarity bit (DIO0_P) through a 74HC04 on the logic board.
- Gate drive per switch, from the 11-06 assembly notes:

| Switch | IN+ (pin 2) | Drain | Source = local 0 V of its driver | Conducts when |
|---|---|---|---|---|
| Q1 | POL (DIO0_P, E1 pin 3) | rail bar (+5 V) | left coil node | POL high, DISABLE low |
| Q2 | NOT-POL (74HC04 out) | rail bar (+5 V) | right coil node | POL low, DISABLE low |
| Q3 | NOT-POL | left coil node | return bar (= pass-bank input, **not ground**) | POL low, DISABLE low |
| Q4 | POL | right coil node | return bar | POL high, DISABLE low |

  - UCC5350SB pins: 1 VCC1 (+3.3/5 V logic, shared); 2 IN+; 3 IN− = **DISABLE, active high, from the 74HC10 output, shared by all four**; 4 GND1 (logic GND, shared); 5 VCC2 ← RO-0515S +Vout (+15 V); 6 OUTH → 10 Ω → gate; 7 OUTL → 4.7 Ω → gate; 8 VEE2 ← RO-0515S −Vout, tied to that MOSFET's source.
  - RO-0515S pins: 1 −Vin (logic GND, shared); 2 +Vin (+5 V, shared); 3 −Vout → driver pin 8 / MOSFET source; 4 +Vout → driver pin 5.
  - The four floating grounds must never touch each other or logic GND.
- **No dead time is generated on the logic board.** The FSM's enable-low window (`deadtime`, 1 µs default) is the only shoot-through protection. `deadtime_us` must stay ≥ driver propagation plus MOSFET switching time. The testbench must assert that every polarity edge falls strictly inside the enable-low window.

---

## 5. Red Pitaya breakout / safety logic board (built and tested 2026-11-03)

Solderable breadboard, 14/16-pin DIP sockets, +3.3 V logic rail. Chip order top to bottom: **SN74HC04N → SN74HC10N → CD74HC123E → SN74HC74N → SN74HC04N**. Every E1 line has a 10 kΩ pull-down.

| E1 pin | Signal | Logic |
|---|---|---|
| 3 | DIO0_P polarity | 74HC04 #1 → complement. POL drives the Q1/Q4 diagonal, NOT-POL drives Q2/Q3, into UCC5350 IN+ |
| 5 | DIO1_P bridge enable | Input to 74HC10 3-input NAND |
| 15 | DIO6_P heartbeat (~954 Hz square = 125 MHz / 2¹⁷) | Retriggers the CD74HC123E monostable. Q stays high while the heartbeat runs |
| 13 | DIO5_P fault in | **Not wired in the MVP** (decision 2026-11-10, below) |

- **Monostable timing:** R_x/C_x = **220 kΩ / 100 nF**. With 68 kΩ the timeout was only ~1.1 ms, about two heartbeat half-periods, and a pulled heartbeat could recover on an edge. With 220 kΩ the drop is consistent at **≈ 5 ms** (read off the scope captures; the text there was cut off, so confirm). Record the measured value in `bringup.md`.
- **74HC74 latch** with a **B3F-1000 reset button**. When the monostable drops, the latch trips and stays tripped until someone presses reset. The board never re-arms itself, and it **powers up tripped**.
- **74HC10 NAND:** drivers are enabled (IN− low) only if DIO1_P AND monostable OK AND latch not tripped.
- **74HC04 #2 drives the status LEDs** (1 kΩ each): green = ALIVE (heartbeat OK), amber = BRIDGE EN (drivers enabled), red = FAULT (latch tripped).

LED test sequence as observed (put this in `bringup.md` as the acceptance test):

| Step | Action | Green | Amber | Red |
|---|---|---|---|---|
| 1 | Power board, heartbeat running | on | off | **on** (powers up in fault) |
| 2 | Press reset | on | off | off |
| 3 | DIO4_P to 3.3 V (arm → FSM asserts DIO1_P) | on | on | off |
| 4 | Pull DIO6_P | off | off | on |
| 5 | Re-plug DIO6_P | on | off | on (stays latched) |
| 6 | Press reset | on | **on** | off |

**Power-up and fault behaviour (decision 2026-11-10):** no fault feedback from the latch to the Red Pitaya in the MVP. Instead, **the Red Pitaya outputs nothing until explicitly armed**:

- At bitstream load and after any reset, OUT1 = OUT2 = 0, every DIO output is low (bridge disabled), the integrator is cleared, the FSM is in IDLE, and `servo_enable` = 0. This is already safety invariant 2. Keep it, and add a cocotb test plus a `deploy` post-check that reads STS and refuses to report success unless `u14 = 0`, `bridge_en = 0` and `armed = 0`.
- Leaving the servo after load needs both `servo_enable` from the host and an arm (DIO4) edge. A level that is already high at load must not count as an arm.
- Without a heartbeat (Red Pitaya off, rebooting, wrong bitstream, or the bitstream not yet reloaded after a reboot), the monostable holds the latch tripped. The bridge stays open no matter what OUT1 does.
- **Remaining gap (accepted):** if the heartbeat is lost while the Red Pitaya keeps running (loose DIO6 wire, as in test step 4), firmware doesn't know the bridge opened. It keeps integrating against zero current, so pressing reset re-closes the bridge with OUT1 possibly at the clamp. **Operating rule: disarm (DIO4 low) before pressing reset.** Put this in `ONBOARDING.md`, `bringup.md` and scenario F5.

The full-design interlock chain (flow switch, thermal cutouts, leak pad, OC/OT comparators → latching relay) is **not in the MVP**. The heartbeat latch is the only automatic trip. Do not invent interlock hardware in the docs.

---

## 6. Sensing and scaling

- LEM 0.5 mA/A → 10 Ω → AD8421 (G = 1) gives **I_FS = 200 A at +1.000 V on IN1**. The repo currently assumes 125 A.
  - 1 ADC count = 24.4 mA.
  - The 100 % clamp = 100/200 × 8192 = **4096 counts**.
  - Shims at 60 A would use 0.3 V of the ±1 V range.
- Make `I_FS = 1 V / (0.5 mA/A × R_burden × G_inamp)` a derived value in `channels.py`, and flag the resolution cost. The alternative, a gain-2 in-amp (I_FS = 100 A, clamp at full scale), goes in the open questions.
- IN2 must keep the **same scaling as IN1**, so the setpoint scaling changes with it. Update the "÷10" note so computer control knows what volts mean what amps.
- **Measured offset:** on 2026-11-03, `check mot` read +0.179 A with the servo disabled at the old I_FS = 125 A, i.e. ≈ 1.4 mV on IN1. Add an offset-calibration step (AOUT0 trim or a host-side constant) before the zero window (±0.5 A) is trusted for flips.

---

## 7. Supply, ripple, power and cooling

**Ripple pass-through** (worst case: the bank rejects nothing and only the coil impedance opposes, I_noise = V_ripple / 2πfL). Add `V_ripple` and `f_ripple` to the model.

| Channel | SE-600-5 (150 mV, ~50 kHz) | Acopian W5MT100 (80 mV, 55 kHz) | Delta SM15-100 (8 mV, 100 kHz) |
|---|---|---|---|
| MOT 16 µH, 100 A | 30 mA / 0.030 % | 14 mA / 0.014 % | 0.8 mA / 0.0008 % |
| Z shim 29 µH, 60 A | 16 mA / 0.027 % | 8 mA / 0.013 % | 0.4 mA / 0.0007 % |
| X/Y shim 57 µH, 60 A | 8 mA / 0.014 % | 4 mA / 0.007 % | 0.2 mA / 0.0004 % |

Mean Well is acceptable for the MOT. Delta-class supplies are reserved for science-side coils.

**Power budget at 100 A CW, MOT** (replaces interface brief §1c):

| Term | Value | Power |
|---|---|---|
| From rail | 5 V × 100 A | 500 W |
| Coil pair | 6.4 mΩ (PROVISIONAL) | 64 W |
| Bridge, 2 legs conducting | 2 × 2.3–4 mΩ (hot) | 50–80 W |
| Cable + contacts | ~2–3 mΩ | 20–30 W |
| Ballast | 6 × 8.4 W | 50 W |
| **Pass MOSFETs** | remainder ≈ 2.8–3.2 V × 100 A | **≈ 280–320 W (≈ 47–53 W each)** |

The design limit is ≤ 100 W per FET (plate at 50 °C, 0.2 °C/W, 40 % derating), so steady state is inside limits **with water cooling**. The plates are machined and the FETs are mounted with thermal paste (2026-11-04). **The first test round runs without water flow.** Bring-up must use short pulses at reduced current with an explicit duty limit. Say this in `bringup.md` and `ONBOARDING.md`.

---

## 8. What stays exactly as it is

- **Board and toolchain.** STEMlab 125-14 Low Noise, Zynq 7010, one board per channel. Vivado/Vitis 2025.1, OS 3.x, Ubuntu 24.04 under WSL2 on the lab NUC, pavel-demin `20251012` base. `axi_hub` at 0x40000000. `docs/register_map.md` is the single source of truth.
- **Ports.** IN1/IN2/OUT1/DIO0,1,3,4,5,6/AIN0,1/AOUT0 keep their assignments. **OUT2 and DIO2_P stay assigned but are marked unused in the MVP** and must sit at 0 V / low by default.
- **Loop.** PI at 976.6 kS/s (÷128), the Q-format path in `design.md` nodes A–E and G, the hard clamp at 100 % of rated, reset state off, an unconditional heartbeat, anti-windup, zero-window flip gating, and `servo_enable` 1→0 = graceful ramp to zero.
- **Loop targets.** Crossover 2–5 kHz, settle to 0.1 % in < 1 ms, stability < 0.1 %. Note next to them that large steps are compliance-limited (§3.3–3.4).
- **Coils.** Radia-computed, Kelvin measurement outstanding: MOT 16 µH / 6.4 mΩ / 100 A; z shim 29 µH / 11.6 mΩ / 60 A; x,y racetrack shims 57 µH / 14.2 mΩ / 60 A.
- **Coil constants.** MOT 0.524 G/cm/A; z shim 0.663 G/A; diagonal racetracks 0.155 G/A each, rotated 45° from the beamline (drive both with rotation-matrix currents).
- **Verification.** The layered scheme (float model ↔ fixed-point mirror ↔ cocotb bit-exact ↔ host mock) and the 2026-08-27 commissioning record.

---

## 9. Open unknowns: parameters only, never hardcoded

Flag any decision that depends on one of these:

- Coil R and L (Kelvin + LCR outstanding) and the 4/0 cable + contact resistance → `R_total` and the compliance at 100 A.
- `g_fs` of the IXTN200N10L2 in this regime, the measured `G_pass`, and whether OUT1 is 50 Ω-terminated.
- Op-amp (OPA994) supply rail. It is not in the parts list and must exceed the max gate command.
- In-amp gain (1 vs 2) → `I_FS` of 200 A vs 100 A.
- 5KP12CA actual clamp voltage vs current during snap-off, and TVS energy per event. Whether to move to a higher-voltage TVS (e.g. 5KP30CA) for faster snap-offs (§3.3).
- **Bridge-open-at-current energy path** (heartbeat trip at current): supply output-cap overvoltage / OVP behaviour, and whether a reverse-energy path (diode + cap or TVS across the supply) is needed.
- Measured monostable timeout (~5 ms, confirm) and the IN1 offset (~1.4 mV).
- Whether a hold capacitor goes across the rail (`C_hold`).
- Chamber eddy-current settling time → `settle_us`.
- Uncooled duty-cycle limit for the first test round.
- Shim-channel supply (HP/Agilent 6671A candidates), which is outside the MVP.

---

## 10. Bring-up log so far (put this in `docs/lab_notes_2026-11.md`)

- **11-02:** V_GS(th) of all 10 + 10 MOSFETs measured (§2.1); devices selected.
- **11-03:** Logic board wired and tested on the MOT Red Pitaya (still holding the 2026-08-27 bitstream). `pip install -e .` and then `python -m coil_servo_host.check mot` **failed at first**. The fix was to SSH in and run:
  - `/opt/redpitaya/bin/fpgautil -b /root/coil_servo.bit.bin`
  - `nohup python3 /root/coil_servo_server.py --port 9001 >/dev/null 2>&1 &`

  After that, `check` reported: heartbeat ADVANCING, LED walk done, state=IDLE armed=0 fault=0 bridge_en=0 polarity=0, measured +0.179 A. The LED sequence in §5 passed. The monostable R was changed from 68 kΩ to 220 kΩ.
- **11-04:** Water-cooled plates machined (bridge: 4 SOT-227; pass bank: 6 SOT-227 + 6 MP930). FETs mounted with thermal paste.
- **11-04:** Pass bank assembled: 12 AWG drain legs to the "input → drain" bus bar; gate/source leads and ballast resistors soldered; source legs to the "output → to source" bus bar; 5KP12CA between the bars; parts labelled; OPA994 perfboard with BNC input wired.
- **11-06:** H-bridge assembly started. Gate-drive pinout fixed (§4) and bridge bus bars ordered.

**Repo fix from 11-03:** `check` should detect "server unreachable / bitstream not loaded" and print the exact `deploy mot` command instead of failing opaquely. Booting with the bitstream and server auto-loaded (systemd) is optional. Propose it, don't do it unasked, since it changes what state the board boots into.

---

## 11. Work to do, in order

One subsystem per session. `pytest` must be green before moving on.

1. **`CLAUDE.md` + new `docs/mvp_architecture.md`**
   - Write §1–7 above into them, including the parts table and the measured thresholds.
   - Move `BOOTSTRAP.md` to `docs/history/` with a header saying it describes the superseded full architecture.
   - Rewrite `CLAUDE.md`'s plant section: low-side current-source pass bank, single 5 V rail, no boost, no active clamp, TVS-limited turn-down, I_FS = 200 A, MVP = MOT channel. Also update the open-unknowns list.
2. **Model (`model/`)**
   - Rewrite `plant.py`. The chain is: op-amp gain and OUT1 termination → gate (R1·C_iss lag) → N parallel FETs with per-device `V_th`, `g_fs` and `R2`, giving the dead zone and transconductance. That feeds series `R_total = R_coil + R_cable + 2·R_bridge`, with compliance `0 ≤ V_DS ≤ V_TVS(I)` and the rail at `V_rail`. Add a bridge-open branch (body diodes → supply) for the fault case.
   - Optional: `C_hold`, `V_ripple`/`f_ripple`. Keep boost and clamp behind flags, off by default.
   - New `channels.py` parameters, all PROVISIONAL unless measured: `V_rail`, `R_cable`, `N_fets`, `V_th` (list, measured), `g_fs`, `R_ballast`, `gain_opamp`, `out1_termination`, `G_pass` (derived), `R_burden`, `G_inamp`, `I_FS` (derived), `V_tvs` curve, `V_varistor` curve, `C_hold`.
   - Tests: dead zone absorbed without limit-cycling; up-step compliance-limited on 5 V; servoed down-ramp at the TVS-limited rate; snap-off decay matches the TVS curve; the PI never commands below zero; per-FET sharing within 12 % for the installed V_th list; the loop is stable with the plant gain at both ends of the `G_pass` range.
3. **Fixed-point mirror + HDL**
   - Change `docs/register_map.md` first: new CFG bit `out2_enable` (default 0), `boost_mode` default 0, new STS flag `u_floor`.
   - Then update `registers.py`, `servo_output_mux.v`, `servo_pi.v` (lower-bound anti-windup), `servo_flip_fsm.v` (an arm level already high at load is not an arm edge), `coil_servo_top.v`, and the cocotb benches.
   - New assertions: OUT2 ≡ 0 when `out2_enable = 0`; DIO2_P ≡ low with boost off; no integrator wind-up while `u_floor`; after reset/load, u14 = 0 and bridge_en = 0 until servo_enable AND an arm edge; polarity edge strictly inside the enable-low window.
4. **Host (`host/`)**
   - `channels.toml`: the parameters above, `boost_mode = 0`, `out2_enable = 0`, clamp derived from I_FS = 200 A, kp/ki marked "loopback values, rescale by G_pass". Keep the shim sections with PLACEHOLDER hosts.
   - `config.py`: refuse `V_rail > 6` (MOT) or `> 10` (shims); print `G_pass`, the dead-zone volts and I_FS.
   - `check.py`: show `u_floor`, OUT2/boost disabled, fault; give a clear "server not running → run deploy" message.
5. **Docs**
   - `servo_interface_brief.md`: §1b/1c → 5 V rail, §3.3 ramp rates, §7 budget. Move §2b (active clamp) to `future_upgrades.md`. §3 (digital interface) → the §5 board. §6 → the §9 list.
   - `operating_scenarios.md`:
     - S3: ramp on the 5 V rail, compliance-limited, no boost.
     - S5: snap-off via the TVS, open-loop during the decay.
     - S6: flip ramp-down through the bank.
     - New scenario: controlled down-ramp.
     - F5: heartbeat latch needs a physical reset (disarm first).
   - `bringup.md`:
     - SE-600-5 dummy-load campaign.
     - OUT1 termination check **before** connecting the op-amp.
     - The LED acceptance table.
     - Measure the monostable timeout.
     - Uncooled duty limits.
     - Kelvin + LCR on coil and cable.
     - Measure `G_pass` and the dead zone.
     - Scope V_DS during a snap-off to confirm the TVS clamp.
     - IN1 offset calibration.
   - `future_upgrades.md`: active clamp, boost rail, source-sense loops, shunt-sense inner loop, hold capacitor, relay interlock chain, Delta supply for science coils, keeping the reasoning already in the old docs.
   - `README.md` / `ONBOARDING.md`:
     - Status: MVP parts bought Nov 2026, logic board tested, plates machined, assembly with Jeff.
     - Setup, including the 11-03 deploy gotcha.
     - Safety rules: no sustained current without water flow; OUT2/DIO2_P unconnected; disarm before pressing reset after any heartbeat trip.
6. **Bitstream.** Rebuild on the NUC after step 3, update `bitstreams/README.md`, and keep the 2026-08-27 image as `coil_servo_fullarch_20260827.bit.bin`.

---

## 12. Working style (unchanged)

- The audience is physicists, so explain jargon on first use.
- Narrow sessions, small commits, tests before implementation.
- Simulation before hardware. If something can't be verified in simulation, say "unverified until the bench run".
- Never invent register offsets: change `docs/register_map.md` first.
- Push back on anything here that is physically or internally inconsistent instead of picking a plausible number. That includes the §3 analysis, which is Claude's and has not been bench-verified.
