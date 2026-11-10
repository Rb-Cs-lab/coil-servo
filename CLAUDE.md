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

**Rewrite status (2026-11-10): steps 1–2 of 6 done.** The model (`model/`)
now describes the MVP: `channels.py` / `plant.py` (dead zone, measured V_th,
5 V compliance, freewheel turn-off, bridge-open branch, I_FS = 200 A), float
PI with a lower bound. The HDL, fixed-point mirror, host config and remaining
docs still describe the full architecture until steps 3–5; the cocotb
benches, fixed-point closed-loop tests and the channels.toml test therefore
run against the legacy `model/coil_servo_model/fullarch.py`, which is
deleted once they move over. Model findings (see `model/tests/test_float_loop.py` docstring): steps from
zero wait ~2.7 ms on the dead zone — **decided 2026-11-10: step 3 adds a
configurable RUN-only PI/integrator floor at ~90 % of the measured dead
zone** (mvp_architecture.md §3.1); large down-steps undershoot unless the
setpoint is ramped at ≤ the freewheel rate (operating rule for the control
computer).
Turn-off decision made (bridge stays closed, freewheel model by default), so
step 2's model tests use the freewheel decay, not the brief's TVS-limited
rates. Remaining ⚠ REVIEW items in mvp_architecture.md (clamp meaning,
actuator resolution) feed step 3.

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
| IN2 | in | Setpoint from control system analog out, ÷10 to ±1 V (at I_FS = 200 A: 20 A per computer volt) |
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
  commissioned PI gains must be rescaled before real-load tuning.
- **Unipolar actuator:** the bank only sources current or sources less.
  **Decided 2026-11-10: turn-off keeps the bridge closed** — the firmware
  never opens the bridge at current. The coil then freewheels through the
  bridge body diodes (~1.3 ms from 100 A on the MOT, mvp_architecture.md
  §3.3); model this as the default, with the brief's TVS-clamped behaviour
  behind a flag until a scope capture of the drain bar confirms it.
- **Sensing:** LEM LF 310-S → 10 Ω burden → AD8421 (G = 1, PROVISIONAL) →
  IN1: **I_FS = 200 A** at +1 V, 24.4 mA per ADC count.
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
   measured G_pass and is not by itself a current limit. The SE-600-5's
   overcurrent protection is the real hard limit until a measured-current
   trip exists. Never derive the clamp from the sensor scale (I_FS).
2. Reset state is off: all DIO outputs low, both DACs zero, integrator
   cleared, FSM in IDLE. In the MVP, leaving IDLE also needs an arm *edge*
   (a level already high at load doesn't count) — rewrite step 3.
3. OUT1 and OUT2 mutually exclusive at all times (deadband handoff); in the
   MVP OUT2 ≡ 0 (`out2_enable` = 0, rewrite step 3).
4. Bridge enable requires active assertion; enable-low is safe in both
   polarity states. The logic board generates no dead time — the FSM's
   enable-low window is the only shoot-through protection.
5. Heartbeat (DIO6) toggles unconditionally while the fabric runs.
6. Integrator anti-windup, including during clamp phase and bridge-disabled
   — and, in the MVP, at the lower bound (PI output ≤ 0, OUT1 = 0).

The hardware protection does not pass through the Pitaya. In the MVP it is
only the heartbeat latch on the logic board; DIO5_P is unwired. Firmware
never writes code that tries to clear a fault. **Operating rule: disarm
(DIO4 low) before pressing the latch reset** — if the heartbeat dropped while
the Pitaya kept running, the integrator may be wound up and reset would
re-close the bridge with OUT1 at the clamp.

## Open unknowns — config values only, never hardcoded

Each of these must be a named, commented-provisional parameter
(`host/config/channels.toml` + `model/coil_servo_model/channels.py`):

- Coil R and L (Kelvin + LCR outstanding) and 4/0 cable + contact
  resistance → R_total, compliance at 100 A
- Pass bank: g_fs, measured G_pass and dead zone (and their temperature
  dependence), OUT1 50 Ω termination yes/no → also sets the OUT1 clamp value
- OPA994 supply rail (undocumented; must exceed the max gate command)
- In-amp gain (1 vs 2) → I_FS 200 A vs 100 A
- Turn-down path: bridge freewheel vs bank TVS (one scope capture decides)
- Bridge-open-at-current energy path: SE-600-5 output-cap overvoltage / OVP
- Actuator resolution and ground noise at a steady hold vs the 0.1 % spec
  (gain-8 stage: one OUT1 count ≈ 0.14–0.4 % of rated)
- IN1 offset (~1.4 mV ≈ 0.29 A at I_FS = 200 A — calibrate before trusting
  the ±0.5 A flip zero window)
- Hold capacitor across the rail (C_hold) yes/no
- Chamber eddy-current settling time (limits post-flip field settling; FSM
  delay constants are placeholders)
- Uncooled duty-cycle limit for the first test round
- PI gains (loopback values; rescale by measured plant gain, then set from
  the measured step response)

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
