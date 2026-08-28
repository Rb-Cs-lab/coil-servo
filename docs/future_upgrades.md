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

Related, documented elsewhere: the setpoint slew-rate limiter is shelved
in [design.md §4b](design.md) (CFG word 15 reserved for it).

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

## 3. Smaller shelved items (one-liners, for completeness)

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
