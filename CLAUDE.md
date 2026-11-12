# Coil current servo — Red Pitaya firmware + software

Digital current servo for magnetic field coils on a dual-species Rb–Cs
ultracold atoms machine. **Hybrid architecture (decided): PI loop and H-bridge
flip FSM in FPGA fabric; Python for tuning, sweeps, diagnostics, config only.**
Never propose a Linux userspace control loop.

**The hardware being built is the MVP (Nov 2026), MOT channel only:**
[docs/mvp_architecture.md](docs/mvp_architecture.md) is the plant/power-stage
reference. The original full-architecture brief is
[docs/history/BOOTSTRAP.md](docs/history/BOOTSTRAP.md) (superseded for the
power stage; its firmware requirements still hold). The plan for bringing the
rest of the repo in line is [docs/mvp_rewrite_brief.md](docs/mvp_rewrite_brief.md)
§11 — work it in order, one step per session. This file is the
quick-reference facts; if they conflict, flag it — don't silently pick one.

**Status (2026-11-12).** Rewrite steps 1–6 done: model, fixed-point
mirror, HDL, cocotb benches, host tools and docs describe the MVP. **Register
map rev 4 is on main and deployed** (`bitstreams/coil_servo.bit.bin`, build
ID commit `103a127`, built on the **lab laptop** — WSL2 Ubuntu 24.04, Vivado
2025.1; the NUC is gone; the August image is kept as
`coil_servo_fullarch_20260827.bit.bin`). Rev 4 = rev 3 plus IN1/IN2
zero-offset registers (CFG W21/W22, subtracted from every sample; measure
with `python -m coil_servo_host.zero <channel> --write --apply`), a build-ID
register (STS W6) and the PI integrator held cleared while `open_loop` = 1.
STS word 0 bits 31:24 carry the revision; `Board` refuses CFG writes to any
other revision. Dashboard work lives in the separate "Coil-servo web
dashboard" session.

**Bring-up campaign** ([docs/test_campaign.md](docs/test_campaign.md), results in
[docs/lab_notes_2026-11.md](docs/lab_notes_2026-11.md), plots in
[docs/benchtop_test_log.md](docs/benchtop_test_log.md)): Tests 0–3 done
(Test 0 = pre-checks: FET matching, logic board, loopback, hand-driven
bridge; then gate stage, sensor zero, bench supply ≤ 3 A on a temporary 12-turn test
coil, profile `mot_bench`). **Next: control-computer integration on the bench
(IN2 setpoint, DIO3 flip, DIO4 arm), then Test 4 — 100 A on the SE-600-5
with both MOT coils, water, `mot` profile.** Key results (11-11/12):

- **IN1 scale measured: `in1_fs_volts` = 1.12** (full-scale ADC code at
  1.12 V, raw codes, factory calibration not applied) → **I_FS = 224 A,
  27.3 mA per count** (vs ammeter, closed-loop 2 A hold). IN2's own scale is
  not measured yet. Offsets drift a little after power-up — re-zero.
- **Turn-off path confirmed: freewheel through the bridge body diode**
  (drain bar at rail + one diode drop; the TVS is never reached). OUT1 → 0:
  2 A → 0 in ≈ 170 µs (≈ 0.5 V/45 µH), then a ≈ −0.2 A swing into the
  damper capacitor for ≈ 100 µs; bank turn-off rings the drain bar to
  12.1 V for µs (bank TVS ≈ 13.3 V — will clamp at 100 A, scope in Test 4).
  **Closed-loop setpoint → 0 has a PI tail** (2 → 0.35 A at the freewheel
  rate, then 2 % only at ≈ 1.3 ms: the integrator unwinds through the
  bank's turn-on region).
- **LEM moved inside the coil–damper loop** (bridge → damper/varistor tap →
  LEM → coil; 11-12) so it reads true coil current; loop unaffected. For
  the MOT: LEM between the coils, damper across the whole pair. Bridge
  opened at 2 A (firmware trip, `trip_timeout` 0): energy into the damper,
  current zero in ≈ 25 µs, gone by ≈ 200 µs. Size the MOT damper near
  critical damping (R ≈ 2√(L/C)).
- **Unexplained 5 A surge (11-12):** during a 2 A hold awaiting a heartbeat
  pull, the bridge apparently re-closed with OUT1 wound to the clamp; the
  firmware over-current trip caught it. Latch re-checked (holds through a
  2 s heartbeat loss). Root weakness: **the firmware cannot see the
  latch** — proposed: latch output → DIO5 + firmware stop/integrator clear,
  and/or stall detection. Not done.
- Firmware trip fires at the set level (2.85 A for 2.79 A), flips happen
  only inside the zero window, **dead time measured 1.0 µs each side** of the
  polarity edge (at the Red Pitaya pins), heartbeat-latch trip opens the
  bridge at current (bench: rail spike +2.5–3.2 V for ~10 µs from 90 µJ).
- **Deploying (or a Red Pitaya reboot) trips the logic-board latch**
  (the heartbeat pauses while the bitstream loads): reset the latch after
  every deploy, with the heartbeat present.
- **Bench power stage rings at 10–20 kHz** (±0.1–0.2 A, f ∝ 1/√I) when
  the coil is not damped by nearby metal — not the PI loop (same in open
  loop), not the gate stage, not a capacitor; the bank changes its current
  at fixed gate voltage near threshold. **Cured by an RC damper across the
  coil** (3.2 Ω + 10 µF on the 45 µH test coil). **The MOT pair needs its own
  damper** (sized for 16 µH, pulse-rated at 100 A) — check for ringing at
  low currents in Test 4. Mechanism not yet understood.
- Bench PI gains tuned on hardware: kp 31.2 counts/A, ki 942 counts/(A·ms)
  (×2 more oscillates at 21 kHz); bench `I_rated` 2.2 A. Dither makes no
  difference on the bench (one count ≈ 8 mA there); its real test is at
  100 A. Hold average stable within ≈ 0.5 mA over 10 min at 2 A.
- Software arm/flip for bench tests: pulse `dio_invert` bit 1 (arm) / bit 0
  (flip request). **Flips need the arm *level* high** (`flip_req && armed`),
  so a pulsed software arm does not allow flips.

Firmware decisions in force: **snap to zero** (decided 2026-11-10) — a
setpoint target inside the zero window (`zero_win`) bypasses the slew limit.
It does NOT force OUT1 to 0 — the PI still winds OUT1 down, which leaves a
slow tail near zero (measured 11-12); only the over-current trip and open
loop drop OUT1 to 0 at once. **Ramps enforced
in fabric** (decided 2026-11-10; model: raw steps undershoot going down and
overshoot up to ~20 % on compliance-limited up-steps): ≤ 200 A/ms away from
zero, ≤ 50 A/ms toward zero (`servo_slew.v`, CFG W15/W20); the control
computer may step. **OUT1 sigma-delta dither** (decided 2026-11-10,
`servo_dither.v`, ctrl b20 `dither_enable`) for the gain-8 resolution (one
OUT1 count ≈ 0.12–0.14 A at 100 A). Turn-off keeps the bridge closed
(freewheel). Register-map features (register_map.md): 32 CFG words;
`out2_enable` (OUT2 ≡ 0); RUN-only PI floor `u_floor` (W16); over-current
trip `trip_level`/`trip_ticks`/`trip_timeout` (W17–19) + `trip_ack`; FSM
states TRIP/TRIPPED; arm *edge* required to leave IDLE (set `servo_enable`
first, then raise arm). `host/config/channels.toml` holds the measurable
physics; `config.py` builds the model `Channel` from it and DERIVES I_FS, the
OUT1 clamp and the floor (overridable by measured counts) and refuses unsafe
files; `python -m coil_servo_host.config mot` prints the derived values.
Still queued: shot-synchronous capture (proposal in docs/dashboard.md);
**"snap OUT1 to zero"** option (OUT1 → 0, integrator → floor when the
setpoint enters the zero window; removes the PI tail, works on the present
hardware — user: not yet, 2026-11-12; **decided: trigger only on a zero
setpoint, not the whole ±0.5 A zero window**, so small currents can still
be held — for the analog IN2 path "zero" needs a few-count threshold
because IN2 noise is ±1–3 counts, set from measured IN2 noise); bridge-open fast turn-off (needs the
TVS + blocking diode, waits for parts); latch visibility (latch → DIO5,
stall detection); level-sensitive latch trip review; dashboard "hold arm"
mode for software flips.
**GUI rule (user, 2026-11-11):** anything like this — a new setting, status
or calibration — also goes into the dashboard (separate dashboard session);
ask if unsure.

## Board

Red Pitaya STEMlab 125-14 Low Noise, Zynq 7010 (`xc7z010clg400-1`).
One board per coil channel, four boards total.

| Parameter | Value |
|---|---|
| ADC / DAC | 14 bit, 125 MS/s (8 ns/sample) |
| Fast inputs | ±1 V (LV, what we use), DC-coupled, 1 MΩ, DC–60 MHz |
| Fast outputs | ±1 V into 50 Ω, DC–60 MHz |
| E1 digital | 3.3 V LVCMOS, unbuffered, NOT 5 V tolerant |
| E2 slow analog in (XADC) | 4 ch, 12 bit, 0–3.5 V |
| E2 slow analog out (PWM+LPF) | 4 ch, 12 bit, 0–1.8 V |

DIO6/DIO7 (P and N) are reassignable to CAN via housekeeping register 0x34.

## Toolchain (verified against official docs, 2026-08)

| Item | Value |
|---|---|
| Red Pitaya OS | 3.x (official image on the board — NOT pavel-demin's Alpine) |
| Vivado + Vitis | **2025.1** (both; OS 3.00+ requires this pairing per redpitaya.readthedocs.io) |
| Build host | Ubuntu 24.04 LTS (AMD UG973: 2025.1 supports Ubuntu 22.04.x/24.04.x only) |
| Install path | `/opt/Xilinx/` (AMD default) |
| This Windows machine | simulation + model + host-script development only; no Vivado |
| Upstream base | pavel-demin/red-pitaya-notes tag `20251012` (its Vivado 2025.1 release), merged with history — removed files are recoverable via `git log 20251012` |

Bitstream loading on the board: `fpgautil -b coil_servo.bit.bin`
(byte-swapped .bin, produced by `make coil_servo.bit.bin`).

## Make targets (Ubuntu build host only)

- `make cores` — package `cores/*.v` as Vivado IP (`scripts/core.tcl`)
- `make xpr` — project from `projects/coil_servo/block_design.tcl` (`scripts/project.tcl`)
- `make bit` — bitstream to `tmp/coil_servo.bit`
- `make coil_servo.bit.bin` — byte-swapped for fpgautil / OS ≥ 2.0
- Simulation (any machine, no Vivado): `pytest` from the repo root runs
  `model/` + `sim/` (cocotb + Icarus) + `host/tests/`.

## Register map

- Single `axi_hub` core on the PS GP0 port at physical base **0x40000000**
  (userspace: `mmap /dev/mem`, 128 MiB window).
- Decode (from `cores/axi_hub.v`): **address bits [27:24] select the hub port**
  — port 0 = CFG register word (write), port 1 = STS register word (read),
  ports 2+ = BRAM/AXI-Stream. Bits [23:2] address 32-bit words within a port.
  So CFG = 0x40000000, STS = 0x41000000, port n = 0x4n000000.
- Field layout: `docs/register_map.md` is the single source of truth,
  mirrored in `model/coil_servo_model/registers.py` (used by both the
  testbench and the host tools) and implemented by the CFG/STS slicing in
  `cores/coil_servo_top.v`. **Never invent register offsets — read
  `docs/register_map.md` and change it first.**
- Capture FIFO pops at 0x42000000 (stream port 2); XADC BRAM at 0x44000000.

## Port assignment

| Port | Dir | Signal |
|---|---|---|
| IN1 | in | Measured coil current (signed); same scaling as IN2 |
| IN2 | in | Setpoint from control system analog out, ÷10 to ±1 V (nominally 20 A per computer volt at I_FS = 200 A; IN2's own scale not yet measured — IN1 measured 224 A full scale) |
| OUT1 | out | Pass bank command → op-amp (gain ≈ 8) → pass-FET gates |
| OUT2 | out | Active clamp command — **unused in the MVP, must sit at 0 V** |
| DIO0_P | out | Bridge polarity (via logic board → UCC5350 drivers) |
| DIO1_P | out | Bridge enable (low = all four FETs off = safe), ANDed on the logic board with heartbeat-OK and latch-not-tripped |
| DIO2_P | out | Boost-cap switch enable — **unused in the MVP, must sit low** |
| DIO3_P | in | Flip request (TTL, level-shifted) |
| DIO4_P | in | Shot trigger / arm |
| DIO5_P | in | Fault flag — READ ONLY, no clear path; **not wired in the MVP** |
| DIO6_P | out | Watchdog heartbeat (toggles unconditionally) → logic-board monostable + latch |
| AIN0 | in | Coil temperature (logging only) |
| AIN1 | in | Rail voltage monitor |
| AOUT0 | out | Transducer offset trim |

## Plant (MVP — details and analysis in docs/mvp_architecture.md)

Coils (Radia-computed; resistances PROVISIONAL — Kelvin measurement pending).
MVP builds the MOT channel only; shim rows are kept for later.

| Channel | L (pair) | R (pair) | I max | Coil constant |
|---|---|---|---|---|
| MOT anti-Helmholtz | 16 µH | 6.4 mΩ | 100 A | 0.524 G/cm/A |
| Z shim (Helmholtz) | 29 µH | 11.6 mΩ | 60 A | 0.663 G/A |
| X shim (racetrack) | 57 µH | 14.2 mΩ | 60 A | 0.155 G/A |
| Y shim (racetrack) | 57 µH | 14.2 mΩ | 60 A | 0.155 G/A |

(X/Y racetracks are rotated 45° from the beamline — drive them with
rotation-matrix currents.)

Power stage as built:

- **Single fixed rail**, no boost: Mean Well SE-600-5, **5 V / 100 A**
  (limits: MOT ≤ 6 V, shims ≤ 10 V). 4/0 cabling.
- **H-bridge** (4 × IXFN420N10T, isolated UCC5350 drivers) on the high side,
  coils in series inside it, LEM on the lead between the coils.
- **Pass bank LOW-SIDE**, after the bridge: 6 × IXTN200N10L2 in linear mode,
  0.03 Ω ballast each, gates from OUT1 through an OPA994 non-inverting
  gain ≈ 8 stage. **No per-FET source-sense loops, no active clamp.** It is a
  voltage-controlled current source with a **dead zone** (measured
  V_th ≈ 3.53 V → OUT1 ≈ 0.44 V) and up to ~1600 A/V at OUT1. The coil L/R
  pole is not in the loop; L only appears as slew limits at the compliance
  edges. Normalized plant gain up to ~8 (loopback was 2.03), so the
  commissioned PI gains must be rescaled before real-load tuning. Near
  threshold (bench, ≤ 2 A) the bank is square-law, ≈ 0.008 A per OUT1 count,
  current onset ≈ 3540 counts, and its current rises as it warms (the loop
  absorbs ≈ 0.2 A over 10 min at 2 A).
- **Coil damper:** the bank and coil ring at 10–20 kHz unless damped; an RC
  damper across the coil cures it (bench, 11-12). Plan one for the MOT pair.
- **Unipolar actuator:** the bank only sources current or sources less.
  **Decided 2026-11-10: turn-off keeps the bridge closed** — the firmware
  never opens the bridge at current. The coil then freewheels through the
  bridge body diodes (~1.3 ms from 100 A on the MOT, mvp_architecture.md
  §3.3). **Confirmed on the bench 2026-11-12** (drain bar at rail + one diode
  drop, TVS never reached); the TVS-clamped model stays behind a flag.
- **Sensing:** LEM LF 310-S → 10 Ω burden → AD8421 (G = 1, REF to GND) →
  49.9 Ω → IN1. Nominal I_FS = 200 A at +1 V; **measured: full-scale code at
  1.12 V → I_FS = 224 A, 27.3 mA per ADC count** (`in1_fs_volts`).
- **Safety logic board** (built, tested 2026-11-03): heartbeat → CD74HC123
  monostable → 74HC74 latch with manual reset (powers up tripped) → NAND
  with DIO1_P → driver DISABLE. The only automatic trip in the MVP; no relay
  interlock chain, no fault feedback to the Pitaya.
- **Cooling:** both FET plates water-cooled (~47–53 W per pass FET at 100 A
  CW); **first test round runs without water** — short pulses, reduced
  current, explicit duty limit.

**Loop targets:** crossover 2–5 kHz, settle to 0.1 % in < 1 ms, stability
< 0.1 %. Large steps are compliance-limited, not loop-limited (0 → 100 A
≈ 0.4 ms on 5 V). PI decimated to ~1 MS/s (ratio 128 → 976.6 kS/s). Do not
optimise firmware for speed — the analog side sets the bandwidth.

## Safety invariants (in FPGA fabric, asserted in every testbench)

1. Hard fixed-point clamp on OUT1/OUT2 at **100 %** of channel rated current
   (review decision 2026-08-26; supersedes BOOTSTRAP's "~110 %").
   **MVP caveat (⚠ REVIEW, mvp_architecture.md §3.5):** the clamp limits the
   OUT1 *command*; the current that command produces depends on G_pass, the
   dead zone and FET temperature, so the clamp value must be calibrated from
   measured G_pass and is not by itself a current limit. Never derive the
   clamp from the sensor scale (I_FS). **The current limit is the
   over-current trip (decided 2026-11-10, MOT 100 A = the supply's
   rating):** |IN1| > `trip_level` for `trip_ticks` samples → both DACs to
   zero at once, coil freewheels with the bridge closed, bridge opens in
   the zero window (or after `trip_timeout`), latched in TRIPPED until
   `trip_ack` + a fresh arm edge. The SE-600-5's OCP is the backup.
2. Reset state is off: all DIO outputs low, both DACs zero, integrator
   cleared, FSM in IDLE. Leaving IDLE needs `servo_enable` and an arm
   *edge* (a level already high at load, after a stop or after a trip
   doesn't count).
3. OUT1 and OUT2 mutually exclusive at all times (deadband handoff); in the
   MVP OUT2 ≡ 0 (`out2_enable` = 0). Both DACs are exactly zero on the same
   clock the bridge-enable pin drops.
4. Bridge enable requires active assertion; enable-low is safe in both
   polarity states. The logic board generates no dead time — the FSM's
   enable-low window is the only shoot-through protection.
5. Heartbeat (DIO6) toggles unconditionally while the fabric runs.
6. Integrator anti-windup, including during clamp phase and bridge-disabled
   — and, in the MVP, at the lower bound (0, or `u_floor` in RUN): the
   integrator never goes below it.

The hardware protection does not pass through the Pitaya. In the MVP it is
only the heartbeat latch on the logic board; DIO5_P is unwired. Firmware
never writes code that tries to clear a fault. **Operating rule: stop the
servo (`servo_enable` = 0, wait for IDLE — bridge_en low) before pressing the
latch reset** — if the heartbeat dropped while the Pitaya kept running, the
integrator may be wound up and reset would re-close the bridge with OUT1 at
the clamp. (Corrected 2026-11-11: the earlier "disarm (DIO4 low)" rule did
not work — losing arm mid-run never drops the bridge, by design. To run
again after the reset: `servo_enable` = 1, then a fresh arm edge.) **Also
press reset only with the heartbeat present (green on):** the latch trips
on the monostable's *edge* (74HC74 CLK ← CD74HC123 1Q̄; that wire was
missing on 11-11 and has been added), so a reset during heartbeat loss
clears it with no new trip when the heartbeat returns — design weakness,
level-sensitive trip to be reviewed (lab_notes_2026-11.md).

## Open unknowns — config values only, never hardcoded

Each of these must be a named, commented-provisional parameter
(`host/config/channels.toml` + `model/coil_servo_model/channels.py`):

- Coil R and L (Kelvin + LCR outstanding) and 4/0 cable + contact
  resistance → R_total, compliance at 100 A
- Pass bank: g_fs, measured G_pass and dead zone (and their temperature
  dependence), OUT1 50 Ω termination yes/no → also sets the OUT1 clamp value
- OPA994 supply rail (undocumented; must exceed the max gate command)
- In-amp gain (1 vs 2) → I_FS 200 A vs 100 A
- ~~Turn-down path: bridge freewheel vs bank TVS~~ — freewheel, confirmed on
  the bench 2026-11-12
- Bridge-open-at-current energy path: SE-600-5 output-cap overvoltage / OVP
  (bench preview: +2.5–3.2 V rail spike for ~10 µs from 90 µJ; at 100 A
  ~0.08 J — measure in Test 4)
- Coil damper values for the MOT pair (16 µH; pulse rating at 100 A), and
  whether the 10–20 kHz ringing appears on the real coils
- Actuator resolution and ground noise at a steady hold vs the 0.1 % spec
  (gain-8 stage: one OUT1 count ≈ 0.14–0.4 % of rated)
- IN1 offset: Red Pitaya input offset ≈ +0.26–0.29 A (LEM offset ≈ 0),
  calibrated with the zero tool; drifts slightly after power-up — re-zero
  before trusting the ±0.5 A flip zero window. IN2 scale not yet measured
- Hold capacitor across the rail (C_hold) yes/no
- Chamber eddy-current settling time (limits post-flip field settling; FSM
  delay constants are placeholders)
- Uncooled duty-cycle limit for the first test round
- PI gains for the 100 A plant (bench gains are tuned: kp 31.2 counts/A,
  ki 942 counts/(A·ms); the MOT plant has ~17× more gain per count — tune
  from measured steps in Test 4)

If a design decision depends on one of these, flag it explicitly.

## Working style

**Audience: the lab are physicists, not software engineers.** READMEs and
docs must explain jargon on first use (registers, bitstreams, Q-format,
cocotb), spell out setup steps end-to-end, and prefer plain language over
terse engineering shorthand. Code comments follow normal engineering style.

One subsystem per session, small commits, tests green before moving on.
Simulation before hardware; if something can't be verified in simulation, say
so. HDL is developed against the cocotb bench and compared to the float model
in `model/` within a stated tolerance. Push back on inconsistent specs.
