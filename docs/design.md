# Coil servo — fixed-point signal-path design

**Status: FOR REVIEW — no HDL until this is signed off.**
Session-2 deliverable per [BOOTSTRAP.md](../BOOTSTRAP.md): Q-format and
gain-per-LSB at every node, decimation ratio, and every saturation point.
Decisions that need explicit sign-off are marked **⚑ REVIEW**.

Notation: `sN` = N-bit signed two's complement. `Qm.f` = signed fixed point,
m integer bits (excluding sign), f fractional bits, total width 1+m+f.
Numeric examples use the MOT channel; formulas are per-channel.

---

## 0. Per-channel normalization

Everything in the loop is normalized to the ADC full scale:

- `I_FS` = coil current that produces +1.000 V at IN1
  = `2000 / (R_M · G_ia)` amps (LEM ratio 2000:1, burden `R_M`, in-amp gain `G_ia`).
- **⚑ REVIEW / provisional:** choose `R_M`/`G_ia` so that **rated current ≈ 80 %
  of full scale**, i.e. `I_FS ≈ 1.25 × I_rated`. This is what makes a hard
  clamp at 110 % of rated representable *inside* the DAC range with headroom
  left to *measure* overshoot above the clamp. Example (MOT, 100 A rated):
  `I_FS = 125 A` → `R_M · G_ia = 16 Ω`, e.g. `R_M = 16 Ω, G_ia = 1` (LEM
  LF 310-S R_M table allows this at ±15 V compliance — verify) or
  `R_M = 8 Ω, G_ia = 2`. Burden value is an Open Unknown; only the *product*
  enters the firmware, as `I_FS` in `channels.yaml`.
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

**⚑ REVIEW — bridge-frame rotation.** The transducer is on the coil side, so
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
zero). A `sp_sign_mismatch` STS bit reports it. Alternative (rejected):
firmware takes |setpoint| and ignores its sign — hides wiring errors.
Confirm the convention.

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

DSP48-friendly: every product is (≤25-bit) × (≤18-bit).

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
  2. *Hold input:* external `int_hold` (from the safety core) freezes the
     accumulator — asserted whenever bridge enable is low or the FSM is in
     any state other than RUN. Covers "during the clamp phase and while the
     bridge is disabled".
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
- `CLAMP` = counts equivalent of **110 % of rated current**: MOT
  `110 A / 125 A × 8192 = 7209` counts; shims `66/75 × 8192 = 7209` (same
  number by construction of the 80 % rule). Written once by Python at config
  time, **enforced in fabric** — no integrator state or setpoint can exceed
  it (**saturation point #4**, the safety-critical one; asserted in every
  testbench).
- Conversion s24 Q3.20 → s14 Q1.13 happens here: round-to-nearest on the
  dropped 7 bits, then clamp. (**saturation point #5**, subsumed by #4 since
  CLAMP < full scale.)

### Node F — output mux / deadband handoff  (safety invariant #3)

**⚑ REVIEW.** BOOTSTRAP says the handoff is "on the sign of the error", but a
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
  holds. Confirm.
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
  Python model, as `G_pass` in `channels.yaml`.

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

Timeout guard: RAMP_DOWN not reaching the zero window within `FLIP_TIMEOUT`
(u32 ticks) → FSM goes to FAULT-ish HOLD state (bridge stays in its current
safe configuration, STS flag set, requires CFG acknowledge to retry — never
flips at nonzero current, which is the entire point).

**⚑ REVIEW — DIO4 "shot trigger / arm" semantics (unspecified in BOOTSTRAP).**
Assumed: DIO4 high = armed; bridge enable can only be asserted while armed,
and flip requests are only honored while armed. DIO4 falling edge does *not*
kill the bridge mid-shot (that's the interlock's job). Confirm or correct.

**⚑ REVIEW — boost cap (DIO2) policy (unspecified).** Assumed: CFG-selectable
between manual (register bit) and auto (asserted by FSM from ENABLE until
current first reaches 90 % of setpoint, then dropped). Default manual-off.

Heartbeat (invariant #5): free-running counter toggles DIO6 at
125 MHz / 2¹⁷ ≈ 954 Hz, combinationally independent of every other block —
it stops only if the fabric clock stops.

---

## 4. Loop-dynamics sanity check (float model verifies this, session 3)

MOT: L = 16 µH, R_loop ≈ 12 mΩ (provisional) → pole at R/2πL ≈ 119 Hz.
Target crossover f_c = 2–5 kHz → |plant| there ≈ R·(f_pole/f_c)…: with
G_pass ≈ I_FS per volt normalized plant ≈ f_pole/f (above pole), so
Kp ≈ f_c/f_pole ≈ 17–42 — comfortably inside the s18+shift gain range with
~10⁻⁵ relative resolution. Ki ≈ 2π f_c Kp /10 places the PI zero a decade
below crossover. Digital delay budget (0.51 µs decimator + ≤3 PI ticks +
DAC) ≈ 3.6 µs → 6.5° at 5 kHz: the digital path is not the phase budget;
the analog chain is, as BOOTSTRAP states. PI gains remain an Open Unknown —
set from the measured step response; the numbers above only show the
formats don't constrain them.

---

## 5. Open items blocking nothing, tracked in channels.yaml

`I_FS` (burden × in-amp product), `G_pass`, `R_loop` (Kelvin pending),
`ZERO_WIN`, `DEADTIME`, `SETTLE` (eddy), `D` (deadband), PI gains.
Every one is a named config value with a `# PROVISIONAL` comment; none is
hardcoded in HDL — they are all CFG registers or model parameters.
