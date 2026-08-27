# Coil current servo — Red Pitaya firmware + software

Digital current servo for magnetic field coils on a dual-species Rb–Cs
ultracold atoms machine. **Hybrid architecture (decided): PI loop and H-bridge
flip FSM in FPGA fabric; Python for tuning, sweeps, diagnostics, config only.**
Never propose a Linux userspace control loop.

Full context and requirements: [BOOTSTRAP.md](BOOTSTRAP.md). This file is the
quick-reference facts; if they conflict, flag it — don't silently pick one.

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
| IN2 | in | Setpoint from control system analog out, ÷10 to ±1 V |
| OUT1 | out | Pass bank command (current up / hold) |
| OUT2 | out | Active clamp command (current down) |
| DIO0_P | out | Bridge polarity |
| DIO1_P | out | Bridge enable (low = all four FETs off = safe) |
| DIO2_P | out | Boost-cap switch enable |
| DIO3_P | in | Flip request (TTL, level-shifted) |
| DIO4_P | in | Shot trigger / arm |
| DIO5_P | in | Fault flag from hardware interlock — READ ONLY, no clear path |
| DIO6_P | out | Watchdog heartbeat (toggles unconditionally) |
| AIN0 | in | Coil temperature (logging only) |
| AIN1 | in | Rail voltage monitor |
| AOUT0 | out | Transducer offset trim |

## Plant (per channel; resistances PROVISIONAL — Kelvin measurement pending)

| Channel | L (pair) | R (pair) | I max |
|---|---|---|---|
| MOT anti-Helmholtz | 16 µH | 6.4 mΩ | 100 A |
| Z shim (Helmholtz) | 29 µH | 11.6 mΩ | 60 A |
| X shim (racetrack) | 57 µH | 14.2 mΩ | 60 A |
| Y shim (racetrack) | 57 µH | 14.2 mΩ | 60 A |

Total loop R ≈ coil + 10–15 mΩ (pass bank + cabling); MOT open-loop pole
≈ 100 Hz. Rail ~3 V hold, ~15 V boost cap for ramps. Sensors: LEM LF 310-S
(MOT), LF 205-S (shims), burden resistor + in-amp into IN1.

**Loop targets:** crossover 2–5 kHz, settle to 0.1 % in < 1 ms, stability
< 0.1 %. PI decimated to ~1 MS/s (ratio 128 → 976.6 kS/s). Do not optimise
firmware for speed — the analog side sets the bandwidth.

## Safety invariants (in FPGA fabric, asserted in every testbench)

1. Hard fixed-point clamp on OUT1/OUT2 at **100 %** of channel rated current
   (review decision 2026-08-26; supersedes BOOTSTRAP's "~110 %").
2. Reset state is off: all DIO outputs low, both DACs zero.
3. OUT1 and OUT2 mutually exclusive at all times (deadband handoff).
4. Bridge enable requires active assertion; enable-low is safe in both
   polarity states.
5. Heartbeat (DIO6) toggles unconditionally while the fabric runs.
6. Integrator anti-windup, including during clamp phase and bridge-disabled.

The hardware interlock chain does not pass through the Pitaya. Firmware only
*reads* DIO5_P; never write code that tries to clear a fault.

## Open unknowns — config values only, never hardcoded

Each of these must be a named, commented-provisional parameter
(`host/config/channels.toml` + `model/coil_servo_model/channels.py`):

- Actual coil resistance (Kelvin measurement outstanding; PI gains depend on it)
- Chamber eddy-current settling time (limits post-flip field settling; FSM
  delay constants are placeholders)
- Burden resistor value (10 Ω → 0.5 V @ 100 A; 20 Ω → 1.0 V)
- Clamp voltage (~30 V figure is illustrative)
- PI gains (to be set from measured open-loop step response)

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
