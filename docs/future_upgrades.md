# Future upgrades — designed, shelved, not scheduled

Options we have thought through and deliberately *not* built. Each entry
records why it would be wanted, what exactly would change, what it costs,
and what must be measured first — so the reasoning survives without
re-deriving it. Nothing here blocks current operation; the commissioned
servo meets the 0.1 % spec with margin (see
[lab_notes_2026-08-27.md](lab_notes_2026-08-27.md)).

A guiding fact for all of these: the FPGA loop, the flip state machine,
the safety invariants, and the host tooling carry over **unchanged**.
Every upgrade below happens at the edges of the loop.


## 1. ppm-class current stability (discussed 2026-08-28)

**Why:** field-sensitive work — the canonical Rb–Cs example is Feshbach
magnetoassociation of RbCs molecules, which wants sub-mG control on
~180 G bias fields, i.e. a few parts per million (ppm). Note this is
typically a *dedicated bias coil pair* — likely a future fifth channel
rather than a change to the MOT/shim channels.

**Where the current design's floor comes from (~100 ppm = 0.01 %):**
the feedback loop nulls everything downstream of the sensor, so what
survives is anything that changes what "measured current" means — LEM
LF-series transducer drift (~50–100 ppm/K), burden resistor temperature
coefficient, the Red Pitaya front-end/ADC-reference gain drift
(~10–50 ppm/K), and the control computer's 16-bit analog setpoint
(~15 ppm quantization plus its own drift).

**Upgrade tiers** (each includes the previous):

| Tier | Expected class | What changes | Recurring cost/channel |
|---|---|---|---|
| 0 (today) | ~100 ppm | — | — |
| 1 | ~10 ppm | Ultrastab-class transducer (LEM IT/ITN series, ~1 ppm/K, needs ±15 V supply); metal-foil burden (≤ 0.5 ppm/K, no oven needed at that tempco); auto-zero between shots (small host/firmware feature — the servo already sits at zero current between shots, record and subtract the offset); enclosure/thermal care for the board | ~$1–2k (transducer dominates) |
| 2 | 1–3 ppm | Digitize the **error, not the signal**: small analog board subtracts a precision reference from the burden voltage and amplifies the difference into IN1, so the board's gain drift multiplies a near-zero number. Precision moves to a 20-bit reference DAC (AD5791/DAC11001 class) — which also means the **setpoint goes digital** (register-driven ramp tables in fabric, triggered off the existing arm line; firmware: setpoint-source mux + BRAM tables). Frees the IN2 input (see §2). | Tier 1 + custom analog board (one-time design, ~$300–500 parts) |

**Standardization recommendation:** build the *electronics* identically
on all channels (the engineering is one-time; identical channels mean
shared spares and one calibration procedure) but choose the transducer
per channel — the MOT gradient physically doesn't benefit from ppm, so
it keeps the LF-series sensor in the same socket.

**Measure first:** the Red Pitaya front end's actual gain tempco (an
afternoon with a stable source and `watch` — nobody specs it tightly);
and remember ppm *current* is not ppm *field* if the coil moves or the
eddy environment changes.

## 2. Eddy-current feedforward (pre-emphasis) (discussed 2026-08-28)

**Why:** the servo regulates coil *current*; eddy currents in the
chamber make the *field* at the atoms lag it with exponentially decaying
tails (time constants τᵢ — our top open unknown). Compensation does not
fight the loop — it reshapes the setpoint: ask for
I(t) = I_target(t) + Σᵢ bᵢ·e^(−t/τᵢ) after each transient so field =
what you wanted. This is the "pre-emphasis" technique standard on MRI
gradient systems. Typical suppression of eddy tails: 10–100×.

**Three injection points, increasing machinery:**

1. **Today, zero changes:** the control computer programs the
   pre-emphasized waveform into the analog setpoint (IN2). Works with
   the hardware as commissioned.
2. **With Tier 2's digital setpoint (natural home):** ramp tables
   computed offline, or a real-time filter in fabric (a few first-order
   sections — cheap, and verifiable bit-for-bit against the Python model
   like everything else). Per-channel τᵢ, bᵢ in channels.toml.
3. **Analog feedforward input on the freed IN2** (only meaningful after
   Tier 2): a summing node into the setpoint path with its own gain and
   enable bit (spare CFG word available). Feasible and safety-neutral —
   the 100 % clamp sits downstream of everything — but largely redundant
   once digital tables exist.

**Better use of a freed IN2 — pickup coil.** A small sense coil near the
chamber measures dB/dt, which is exactly the eddy signature. **No
upgrade needed to start:** IN2 already routes through the `capture_sel`
mux into the capture FIFO, so a pickup coil on IN2 plus one `step` run
records the eddy response synchronously with the current step, today.
That measurement is the prerequisite for pre-emphasis anyway (it turns
the bᵢ, τᵢ into fitted numbers) and retires the biggest unknown in the
[operating-scenarios timing table](operating_scenarios.md). The
end-game — integrating the pickup signal and closing a slow *field* loop
around the fast current loop — is architecturally compatible but a
separate project with its own drift problems.

**Limits:** pre-emphasis needs headroom — the overshoot must fit under
the 100 % clamp and the boost rail's slew, so it works best below rated
current. It assumes linearity: fine for a stainless chamber, but
mu-metal or other ferromagnetic hardware nearby responds hysteretically
and won't pre-emphasize away. Validate with atoms, not just the pickup
coil.

## 3. Setpoint slew-rate limiter (decision 2026-08-27: not needed)

Moved here from design.md §4b. Today a setpoint step ramps the current
as fast as the rail allows (V/L — that speed is the requirement, and the
boost cap exists to provide it). If a *physical* reason to ramp slower
ever appears — mechanical impulse on the coil mounts from fast dB/dt, or
eddy heating in chamber metal — the right fix is a ramp generator in
fabric between the setpoint mux and the error subtraction (design.md
Node B), with the rate as a CFG register (**word 15 is reserved for
it** — see [register_map.md](register_map.md)). It must NOT be done in
Python (can't time µs) or by detuning the loop (ruins disturbance
rejection). Small, well-contained addition; revisit only if the hardware
people raise dB/dt as a concern.

## 4. Firmware supervisory temperature soft-stop (discussed 2026-08-31)

**Why:** unattended long runs at rated current (e.g. MOT characterization
at 100 A CW). A firmware layer that watches the coil temperature and ends
the run *politely* — before the hardware interlock has to end it
abruptly.

**What it is — and is not:** a *supervisory* layer at a conservative
threshold, on top of (never instead of) the hardware interlock chain's
thermal switches. Protection must not depend on the FPGA being alive and
correctly programmed; that principle stands. Building this formally
revises the earlier "no firmware overtemperature action" decision — the
new wording should be: no firmware *protection* role; a lower-threshold
supervisory soft-stop is allowed.

**Design sketch:** coil temperature already enters on AIN0 (XADC,
0–3.5 V, 12 bit; currently logging-only). Add a fabric comparator: AIN0
above a CFG threshold triggers the existing graceful-stop path (ramp to
zero via the clamp, then bridge disable), latches a status flag, and
requires operator acknowledgment to restart (same pattern as
TIMEOUT_HOLD — no auto-restart, to avoid thermal cycling). XADC's slow
sample rate is fine; thermal timescales are seconds.

**Design choices when built (all config, PROVISIONAL like everything
else):** sensor calibration (volts → °C) and threshold; fail-safe sensor
wiring so a broken wire reads *hot*, not cold (e.g. NTC with pull-up
arranged so open circuit crosses the threshold).

**Scope:** one proper session — comparator + CFG threshold word + status
flag in HDL, model mirror, cocotb tests (fully simulable; bench-testable
with a voltage divider into AIN0), register map + docs. Pairs naturally
with the pending XADC readout tool (§5).

## 5. Smaller shelved items (one-liners, for completeness)

- **Auto-boost mid-run re-trigger:** today auto boost arms only at servo
  enable and post-flip re-enable; a large mid-run setpoint step slews on
  the hold rail. If a sequence ever needs boosted mid-run ramps, add an
  error-threshold re-trigger (small HDL change) — or just use manual
  boost mode around that ramp.
- **Software flip-trigger register bit:** flips are hardware-pulse-only
  (E1 pin 9). A CFG bit that injects the same edge would help bench work
  without a function generator; deliberately excluded so far to keep the
  flip authority in one place.
- **AOUT0 transducer offset trim** (register word reserved) and an
  **XADC temperature/rail readout tool** — pending features rather than
  design decisions; listed in the repo TODOs.

## 6. Fast turn-off for the MVP: blocking diode + TVS across the bridge input (discussed 2026-11-10)

**Why:** in the MVP as built ([mvp_architecture.md](mvp_architecture.md)
§3.3) the actuator is unipolar and the coil current can only fall through
the H-bridge's freewheel loop (one conducting switch + the opposite
low-side body diode), at a diode drop plus I·R: **~1.3 ms from 100 A on
the MOT** (Z shim ~1.5 ms, X/Y ~2.7 ms). If sequences need faster
snap-offs — e.g. switching the MOT gradient off before imaging — the coil
must be made to push against a higher voltage, since dI/dt ≈ −V/L.

**Why the obvious fixes don't work:** a higher-voltage TVS across the pass
bank, or a varistor across the supply on its own, never conducts. With
the bridge closed the freewheel loop doesn't pass through the bank at
all; with the bridge open the current returns through the bank's body
diodes and into the supply's output capacitors at ~7.5 V, and a part in
parallel with those can't push the voltage above them. The coil varistors
(S20K20, knee ~33 V each) likewise stay off — and if they were ever made
to clamp, the two coils in series would put ~2 × 80 V ≈ 160 V at 100 A
across the 100 V bridge MOSFETs.

**What it is:**

1. **Blocking diode** in series with the supply's + lead (before the rail
   bar), rated for 100 A continuous — a power Schottky on a heatsink.
   Lets supply current flow forward, stops returning coil current from
   charging the supply.
2. **TVS across the bridge input** (rail bar ↔ return bar), e.g. a
   5KP30CA-class part (V_BR ≈ 33 V). With the bridge opened at current,
   the coil current flows through Q2's and Q3's body diodes into this TVS,
   which holds the coil at ≈ −V_TVS: dI/dt ≈ 30 V / 16 µH ≈ **2 A/µs**,
   **100 A → 0 in ~50 µs** on the MOT. The energy (½LI² ≈ 0.08 J plus
   V_TVS·∫I dt ≈ 0.15 J total per event) is well within a 5KP-class part.
   Keep V_TVS comfortably below the 100 V MOSFET rating and below the
   coil-varistor knee (2 × 33 V for the pair) so the varistors stay out of
   it.
3. **Firmware:** a deliberate "snap-off" action that opens the bridge at
   current (a new FSM path and CFG/trigger — register map first). This
   relaxes the current rule that the bridge only opens at zero current or
   on a fault, so it needs a safety review: the bridge-open window must
   still never toggle polarity, and re-enabling must wait for the zero
   window as in a flip.

**Bonus:** the same two parts fix the open bring-up risk for heartbeat
trips at current — the coil energy lands in the TVS instead of the
SE-600-5's output capacitors (no overvoltage-protection trip).

**Costs:** the blocking diode drops ~0.5 V at 100 A (~50 W, needs
cooling) and takes that headroom off the 5 V rail — compliance at 100 A
falls from ~2.8–3.2 V to ~2.3–2.7 V across the bank, still adequate.
During a snap-off the servo is open-loop until the current reaches zero.

**Measure first:** the actual freewheel turn-off on the scope (drain bar
and node A during a throttle-to-zero at moderate current) and whether
~1 ms is fast enough for the experiment's sequences. If it is, this stays
shelved.

## 7. Finer pass-bank drive: lower op-amp gain plus a fixed gate bias (discussed 2026-11-10)

**Why:** the MVP's OPA994 stage is non-inverting with gain ≈ 8 (R_f
6.98 kΩ / R_g 1 kΩ), mapping OUT1 0…1 V to a 0…8 V gate command. But
the pass bank only conducts between V_th ≈ 3.53 V (zero current) and
≈ 4.2 V (100 A), so almost all of OUT1's range is spent below threshold
or as unused headroom. Consequences ([mvp_architecture.md](mvp_architecture.md)
§3.1):

- one OUT1 DAC count ≈ **0.14–0.2 A** of coil current (0.27–0.39 A if
  OUT1 is unterminated) — larger than the 0.1 % stability spec (0.1 A),
  and the current-source plant doesn't low-pass it, so the loop's ±1-count
  hunting can appear directly as current noise;
- ~1.1–1.6 A per millivolt of ground difference between the Red Pitaya
  and supply 0 V;
- the integrator must climb through the ~0.44 V dead zone before any
  current flows, delaying small steps from zero.

**Why a resistor swap alone isn't enough:** the gate must still reach
~4.2 V for 100 A plus margin, so the lowest workable gain is ~5
(terminated OUT1). That only improves resolution ~1.6× (≈ 0.09–0.12 A per
count) — still at the spec limit.

**What it is:** gate = V_bias + G·OUT1, so the full OUT1 range covers only
the useful window. Example: V_bias ≈ 3.0–3.2 V, G ≈ 1.5 → ≈ **0.025–0.04 A
per OUT1 count (5–7× finer)**, with the same factor of immunity to ground
noise and almost no dead-zone delay. Simplest implementation: keep the
OPA994, sum OUT1 and the bias into its + input through two resistors, and
set the gain for the ~1.5 V span.

**Constraints:**

- **V_bias below the hottest device's threshold.** V_th falls ~5 mV/K
  (check the datasheet), so 3.53 V at room temperature is ~3.3 V at
  +40 K. With the bias above threshold, OUT1 = 0 no longer means zero
  current.
- **The bias must be quiet and stable:** the bank is ~200 A per gate
  volt, so 1 mV of bias noise or drift ≈ 0.2 A. Use a precision reference
  (REF5030-class), divided and RC-filtered — not a divider off a supply
  rail. The integrator removes slow drift, not noise.
- Op-amp supply rail must still cover the maximum gate command.

**Firmware:** no code change. G_pass changes, so the PI gains are rescaled
and the OUT1 clamp recalibrated (both already required for the MVP).

**Measure first:** coil-current noise at a steady hold with the gain-8
stage as built, against the 0.1 % spec. If it passes, this stays shelved.
