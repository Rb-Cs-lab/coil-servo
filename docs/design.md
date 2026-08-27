# Coil servo — fixed-point signal-path design

**Status: REVIEWED and signed off 2026-08-26.** This document is the
contract the HDL is written against; if the HDL and this document disagree,
the HDL is wrong. Decisions made at review are marked **✓ DECIDED**.

**How to read this document.** The FPGA cannot do floating-point cheaply, so
every signal in the loop is an integer that *stands for* a physical quantity,
and this document pins down the exchange rate at every point. Notation:
`sN` = N-bit signed integer (two's complement). `Qm.f` = a signed
fixed-point number with m integer bits and f fractional bits (total width
1 + m + f); think of it as an integer with an agreed-upon decimal point —
e.g. Q1.13 spans −2.0…+2.0 in steps of 2⁻¹³. "1 LSB" = the physical value of
the least-significant bit, i.e. the smallest representable step. The reason
this document exists: fixed-point scaling errors are silent — a gain that is
wrong by 2¹⁴ still simulates *something* — so every conversion is written
down here where it can be checked by eye, instead of living implicitly in
the Verilog. Numeric examples use the MOT channel; formulas are per-channel.

---

## 0. Per-channel normalization

Everything in the loop is normalized to the ADC full scale:

- `I_FS` = coil current that produces +1.000 V at IN1
  = `2000 / (R_M · G_ia)` amps (LEM ratio 2000:1, burden `R_M`, in-amp gain `G_ia`).
- **✓ DECIDED (value still provisional):** choose `R_M`/`G_ia` so that
  **rated current ≈ 80 % of full scale**, i.e. `I_FS ≈ 1.25 × I_rated`.
  The output *command* is clamped at 100 % of rated (Node E), but the
  *measurement* range extends 25 % above rated so the ADC can still see and
  log an overshoot or a fault transient instead of pegging at the rail —
  you cannot diagnose what you cannot measure. Example (MOT, 100 A rated):
  `I_FS = 125 A` → `R_M · G_ia = 16 Ω`, e.g. `R_M = 16 Ω, G_ia = 1` (LEM
  LF 310-S R_M table allows this at ±15 V compliance — verify) or
  `R_M = 8 Ω, G_ia = 2`. Burden value is an Open Unknown; only the *product*
  enters the firmware, as `I_FS` in `channels.toml`.
- IN2 (setpoint) uses the **same** `I_FS` by construction (÷10 from the
  control computer's ±10 V), so error is a direct code subtraction, no gain
  correction anywhere.

| Channel | I_rated | I_FS (provisional) | amps per ADC LSB |
|---|---|---|---|
| MOT | 100 A | 125 A | 15.26 mA |
| Z shim | 60 A | 75 A | 9.16 mA |
| X shim | 60 A | 75 A | 9.16 mA |
| Y shim | 60 A | 75 A | 9.16 mA |

---

## 1. Node-by-node signal path

```
IN1 ──ADC──┐                                       ┌──▶ OUT1 (pass bank)
           ├─ sub ─ decimate ÷128 ─ PI ─ clamp ─ mux
IN2 ──ADC──┘  ▲                                    └──▶ OUT2 (active clamp)
              └ setpoint mux (IN2 / register / FSM-forced zero)
              └ polarity rotation (×pol)
```

### Node A — fast ADC (IN1, IN2), 125 MS/s

- Raw: 14-bit offset binary; `axis_red_pitaya_adc` converts to **s14**
  two's complement (sign-extended to s16 in the 32-bit stream word,
  IN1 = bits [15:0], IN2 = bits [31:16]).
- Format: **s14 = Q1.13**, 1.0 ≡ +1 V ≡ +I_FS.
- 1 LSB = 2 V / 2¹⁴ = **122.07 µV** = `I_FS/8192` amps
  (MOT: 15.26 mA; shims: 9.16 mA).

### Node B — setpoint selection and polarity rotation

`sp` (s14) = one of: IN2 (run mode) / CFG setpoint register (tuning, HIL) /
**0, forced by the flip FSM** during a flip (overrides both).

**✓ DECIDED — bridge-frame rotation.** The transducer is on the coil side, so
measured current is signed and the plant gain *sign flips with bridge
polarity* (pass bank can only source magnitude through whichever diagonal is
on). The PI must therefore run in the drive frame:

```
e_drive = pol · (sp − I_meas)        pol ∈ {+1, −1} = bridge polarity bit
```

Both operands s14 → `e_drive` is **s15, Q2.13** (max |e| = 2.0 when setpoint
and measurement sit at opposite rails). Consequence of this convention: the
control computer's setpoint sign must agree with the current bridge polarity;
a mismatched sign produces a large negative drive-frame error, which drives
the clamp toward zero current — i.e. the failure mode is *safe* (sits at
zero). A `sp_sign_mismatch` STS bit reports it, and the host tools warn
loudly when it is set. Alternative (rejected at review): firmware takes
|setpoint| and ignores its sign — simpler to think about, but it silently
accepts a control-system misconfiguration that the signed convention turns
into a visible, safe error. The sign convention is documented in exactly two
places (here and the port table) so it stays easy to track.

**DIO input polarity** is runtime-configurable: the `dio_invert` CFG
register (see register map) flips the sense of the three E1 *inputs* —
flip request (DIO3), arm (DIO4), fault (DIO5) — so a level-shifter or
wiring inversion is a register write, not a rebuild. E1 *output* polarities
(bridge enable, bridge polarity, boost) are deliberately **not**
runtime-configurable: the reset state of the FPGA must be safe with zero
configuration, and "0 V on the pin = FETs off" is a hardware contract with
the gate drivers. If an output ever needs inverting, it is a one-line named
constant in the safety core plus a rebuild — by design.

### Node C — decimator, 125 MS/s → 976.5625 kS/s

- **Ratio R = 128** (power of two: the divide is a binary-point move, not
  hardware). PI sample time **T_s = 1.024 µs**.
- Boxcar accumulate-and-dump: sum of 128 × s15 → **s22** exact, no rounding,
  no saturation possible (7 bits of growth, 15+7 = 22).
- Reinterpret the sum as the *average* by moving the binary point:
  **s22 = Q2.20**, 1.0 ≡ I_FS. 1 LSB = `I_FS/2²⁰` = **119.2 µA** (MOT) —
  the averaging buys 7 bits of error resolution (and √128 ≈ 11× noise
  reduction), which is what makes the 0.1 % stability target ≈ 0.08 % of
  reading *per LSB* → ~1000× finer than the spec needs.
- Group delay: 64 fast samples = 0.51 µs → 0.9° of phase at 5 kHz.
  Negligible against the analog poles; decimating harder would still be fine,
  but 128 already makes the arithmetic comfortable. **Not** using
  `axis_decimator` CIC-style IP — a plain accumulator is 10 lines and
  exactly analyzable.

### Node D — PI arithmetic (runs at 976.6 kHz)

Every product is (≤25-bit) × (≤18-bit) so it fits one DSP48 — the FPGA's
hardwired multiplier block, whose native size is 25×18 bits; staying inside
it keeps the arithmetic fast and cheap.

**P path**
```
p_full = e · Kp_mant          s22 × s18 → s40  (exact)
p      = sat24( p_full >>> Kp_shift )          → s24, Q3.20
```
- `Kp_mant`: **s18** integer mantissa; `Kp_shift`: **u5** (0–31) arithmetic
  right shift. Effective gain `Kp = Kp_mant · 2^(−Kp_shift)`; range
  ~7.6 × 10⁻⁶ … 1.3 × 10⁵, relative resolution 2⁻¹⁷. (Expected operating
  point: crossover/pole ≈ 2–5 kHz / ~100 Hz → Kp of order 20–50 in
  normalized units, assuming the pass bank is scaled so 1 V command ≈ I_FS —
  that transconductance is provisional; gains come from measurement.)
- **Saturation point #1**: the post-shift value saturates into Q3.20
  (±8.0). Cannot overflow silently.

**I path**
```
acc    = acc + e · Ki_mant     s48 accumulator += s40 product, SATURATING
i      = sat24( acc >>> Ki_shift )             → s24, Q3.20
```
- `Ki_mant`: **s18**, `Ki_shift`: **u6** (0–48). Effective per-tick integral
  gain `Ki_tick = Ki_mant · 2^(−Ki_shift)`; physical `Ki = Ki_tick / T_s` s⁻¹.
- Accumulator: **s48, saturating add** (**saturation point #2** — never
  wraps). With Ki_shift = 28 the accumulator bottom bits give the integrator
  ~2⁻²⁸ of head-resolution below one output LSB: no dead zone, no limit
  cycle at the 0.1 % level.
- **Anti-windup (three mechanisms, all in fabric):**
  1. *Conditional integration:* skip the accumulate when the output clamp
     (Node E) is engaged **and** sign(e) would push further into the clamp.
  2. *Hold input:* external `int_hold` (from the flip FSM) freezes the
     accumulator — asserted exactly while the bridge is open (IDLE, DISABLE,
     FLIP), because then the loop cannot actuate and would wind up. It is
     deliberately NOT asserted in RAMP_DOWN/SETTLE/TIMEOUT_HOLD: there the
     loop is actively servoing the current to zero and a frozen (stale,
     positive) integrator would fight the ramp-down. Covers "during the
     clamp phase and while the bridge is disabled". (Refined from an earlier
     draft that held in every non-RUN state — that version was wrong for
     RAMP_DOWN.)
  3. *Clear input:* `int_clear` (from FSM on re-enable after a flip, and
     from CFG for tuning) zeroes the accumulator.

**Sum**
```
u = sat24( p + i )            → s24, Q3.20   (saturation point #3)
```

### Node E — hard output clamp  (safety invariant #1)

```
u_clamped = min(max(u, −CLAMP), +CLAMP)      CLAMP: u14 CFG register, Q1.13 counts
```
- `CLAMP` = counts equivalent of **100 % of rated current**: MOT
  `100 A / 125 A × 8192 = 6554` counts; shims `60/75 × 8192 = 6554` (same
  number by construction of the 80 % rule). Written once by Python at config
  time, **enforced in fabric** — no integrator state or setpoint can exceed
  it (**saturation point #4**, the safety-critical one; asserted in every
  testbench).
- **✓ DECIDED at review (changed from BOOTSTRAP's "~110 %"):** the clamp sits
  at 100 % of rated, not 110 %. The 110 % figure treated the clamp purely as
  a fault backstop, leaving headroom so a well-tuned transient never touches
  it; at 100 % the clamp also bounds normal-operation overshoot, which is
  more conservative for the coils and pass bank. The cost is that an
  aggressive setpoint step will ride the clamp briefly (anti-windup
  mechanism 1 handles this cleanly — verified in the testbench). It is a
  runtime register, so revisiting the choice is a one-line config change,
  no rebuild.
- Conversion s24 Q3.20 → s14 Q1.13 happens here: round-to-nearest on the
  dropped 7 bits, then clamp. (**saturation point #5**, subsumed by #4 since
  CLAMP < full scale.)

### Node F — output mux / deadband handoff  (safety invariant #3)

**✓ DECIDED — follow the physics.** BOOTSTRAP says the handoff is "on the
sign of the error", but a
literal error-sign handoff can't hold current: at steady state e ≈ 0 while
the *integrator* holds OUT1 at the pass-bank operating point, and e dithers
around zero — the outputs would chatter. The workable rule, which matches the
physics (pass bank sources current up/hold, clamp pulls it down), is the
**sign of the PI output u**:

```
u_clamped >  +D :  OUT1 = u_clamped,  OUT2 = 0
u_clamped <  −D :  OUT1 = 0,          OUT2 = −u_clamped   (magnitude)
|u_clamped| ≤ D :  OUT1 = 0,          OUT2 = 0
```
- `D` = deadband, u14 CFG register in Q1.13 counts (provisional; also
  provides handoff hysteresis-by-gap so the stages never fight).
- During a down-step the integrator winds down, u goes negative, the clamp
  engages — dynamically this *is* "sign of the error" behavior; statically it
  holds.
- OUT2 polarity: assumed the clamp stage takes a *positive* command for
  "pull harder" (magnitude, as above). One CFG bit (`out2_invert`) flips it
  if the analog conditioning turns out inverted.
- Structure guarantees **mutual exclusion**: one mux, one source; both-active
  is unrepresentable in the logic (and still asserted in the testbench).

### Node G — fast DAC (OUT1, OUT2)

- s14 Q1.13 → `axis_red_pitaya_dac` packed word (OUT1 = [13:0],
  OUT2 = [29:16]). 1 LSB = **122.07 µV** at the SMA.
- Downstream (analog, out of firmware scope, provisional): pass-bank
  transconductance `G_pass` A/V set by the source-sense resistors; if scaled
  so +1 V ≈ I_FS, 1 DAC LSB ≈ 15.26 mA commanded (MOT). Enters only the
  Python model, as `G_pass` in `channels.toml`.

### Reset / power-up state (safety invariant #2)

Every register in nodes B–G resets to 0: DACs output exactly 0 V from the
first fabric clock, all E1 DIO outputs low (bridge disabled, polarity 0,
boost off). The stub block design already pins the DAC to zero before any
servo core exists.

---

## 2. Format summary

| Node | Signal | Format | 1 LSB equals (MOT, provisional I_FS = 125 A) |
|---|---|---|---|
| A | IN1, IN2 code | s14 Q1.13 | 122.07 µV ≡ 15.26 mA |
| B | e_drive (fast) | s15 Q2.13 | 15.26 mA |
| C | e (decimated) | s22 Q2.20 | 119.2 µA |
| D | Kp_mant, Ki_mant | s18 int + u5/u6 shift | gain quantum 2^(−shift) |
| D | p, i, u | s24 Q3.20 | 119.2 µA (commanded) |
| D | integrator acc | s48 saturating | 119.2 µA × 2^(−Ki_shift) |
| E/F | u_clamped, OUT1/OUT2 | s14 Q1.13 | 122.07 µV ≡ ~15.26 mA |

Saturation happens at exactly five points (P-shift, accumulator, P+I sum,
output clamp, DAC quantize — #4 is the safety invariant, #1–#3 protect
arithmetic, #5 is subsumed by #4). Nothing else in the path can overflow:
the subtract and decimator are exact by width.

---

## 3. Flip FSM (timing view; logic per BOOTSTRAP)

Runs at the 125 MHz fabric clock; thresholds compared against *decimated*
current (s22) for noise immunity. All durations are CFG registers in 8 ns
ticks, **provisional until the eddy-current measurement exists**.

```
IDLE ─DIO3 edge (debounced, while armed)─▶ RAMP_DOWN   FSM forces sp = 0
RAMP_DOWN ─ |I_meas| < ZERO_WIN for HOLDOFF ticks ─▶ DISABLE
     ZERO_WIN: u14, counts; ±0.5 A = 33 counts (MOT). HOLDOFF: qualify the
     window over ~64 µs of decimated samples so noise can't fake a zero cross.
DISABLE: bridge enable low ─▶ wait DEADTIME (u16 ticks, default 125 = 1 µs)
FLIP: toggle polarity bit ─▶ wait DEADTIME again
ENABLE: bridge enable high, pulse int_clear ─▶ wait SETTLE (u32 ticks,
     placeholder for chamber eddy settling — genuinely unmeasured)
RUN: release setpoint mux back to IN2/register
```

**Graceful stop:** `servo_enable` 1→0 must never drop the bridge at current
(that dumps the coil's stored energy into body diodes — the exact event the
FSM exists to prevent). Instead it enters RAMP_DOWN with a stop flag: servo
to zero, qualify the window, open the bridge after the dead time, park in
IDLE — no polarity toggle. The software "off switch" is therefore always a
controlled ramp.

Timeout guard: RAMP_DOWN not reaching the zero window within `FLIP_TIMEOUT`
(u32 ticks) → FSM goes to FAULT-ish HOLD state (bridge stays in its current
safe configuration, STS flag set, requires CFG acknowledge to retry — never
flips at nonzero current, which is the entire point).

**✓ DECIDED — DIO4 "shot trigger / arm" semantics.**
DIO4 high = armed; bridge enable can only be asserted while armed,
and flip requests are only honored while armed. DIO4 falling edge does *not*
kill the bridge mid-shot (that's the interlock's job). Active level
adjustable via `dio_invert`.

**✓ DECIDED — boost cap (DIO2) policy.** CFG-selectable
between manual (register bit) and auto (asserted by FSM from ENABLE until
current first reaches 90 % of setpoint, then dropped). Default manual-off.

Heartbeat (invariant #5): free-running counter toggles DIO6 at
125 MHz / 2¹⁷ ≈ 954 Hz, combinationally independent of every other block —
it stops only if the fabric clock stops.

---

## 4. Loop-dynamics sanity check (float model verifies this, session 3)

MOT: L = 16 µH, R_loop ≈ 12 mΩ (provisional) → pole at R/2πL ≈ 119 Hz.
The gains needed depend on which of two regimes the composite plant is in
— unknown until measured:

- **Voltage-mode regime** (the loop sees the L/R pole): plant ≈ f_pole/f
  above the pole, so Kp ≈ f_c/f_pole ≈ 17–42 for crossover at 2–5 kHz,
  with Ki ≈ 2π f_c Kp/10 placing the PI zero a decade below crossover.
- **Transconductance regime** (the pass bank's local source-sense loops
  make the plant flat with unity normalized gain): the crossover is set by
  the integrator instead — Ki ≈ 2π f_c and Kp ≈ 0.5 for phase lead. This
  is what the reference model in `model/` assumes, and it is why the
  shipped provisional defaults in `channels.toml` are kp = 0.5 and
  ki ≈ 15,000 s⁻¹ rather than 17–42 — the two documents are consistent,
  they just describe different regimes.

Both regimes sit comfortably inside the s18+shift gain range with ~10⁻⁵
relative resolution — which is all this section needs to establish.
Digital delay budget (0.51 µs decimator + ≤3 PI ticks + DAC) ≈ 3.6 µs →
6.5° at 5 kHz: the digital path is not the phase budget; the analog chain
is, as BOOTSTRAP states. PI gains remain an Open Unknown — set from the
measured open-loop step response; the numbers above only show the formats
don't constrain them.

---

## 5. Open items blocking nothing, tracked in channels.toml

`I_FS` (burden × in-amp product), `G_pass`, `R_loop` (Kelvin pending),
`ZERO_WIN`, `DEADTIME`, `SETTLE` (eddy), `D` (deadband), PI gains.
Every one is a named config value with a `# PROVISIONAL` comment; none is
hardcoded in HDL — they are all CFG registers or model parameters.
