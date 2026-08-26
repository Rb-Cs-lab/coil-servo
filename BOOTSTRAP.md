# Bootstrap prompt — Red Pitaya coil current servo codebase

*Paste this into a fresh Claude Code session with the project folder attached. Everything below is context plus the task; the "Open unknowns" section is deliberately unresolved and must stay parameterised in code.*

---

## Context

I'm building a digital current servo for the magnetic field coils on a dual-species Rb–Cs ultracold atoms machine. The power stage is already designed; what's missing is the Red Pitaya firmware and the software around it. I want a repo where you can verify what you write in simulation before anything touches hardware.

**Architecture decision (already made): hybrid.** The PI loop and the H-bridge flip state machine live in the FPGA fabric. Python handles tuning, transfer-function sweeps, diagnostics, and configuration. Do not propose a Linux userspace control loop — the loop must be deterministic.

---

## Hardware facts to pin in `CLAUDE.md`

**Board:** Red Pitaya STEMlab 125-14, Low Noise variant. Zynq 7010. One board per coil channel, four total.

| Parameter | Value |
|---|---|
| ADC / DAC | 14 bit, 125 MS/s (8 ns/sample) |
| Fast inputs | ±1 V (LV mode, what we use) / ±20 V (HV), DC-coupled, 1 MΩ, DC–60 MHz |
| Fast outputs | ±1 V, spec'd into 50 Ω, DC–60 MHz |
| E1 digital | 3.3 V LVCMOS, unbuffered, **not 5 V tolerant** |
| E2 slow analog in | 4 ch, 12 bit, 0–3.5 V |
| E2 slow analog out | 4 ch, 12 bit, 0–1.8 V, PWM + LPF |

Note: DIO6 and DIO7 (P and N) are reassignable to CAN via the housekeeping register at 0x34.

**Toolchain:**

| Item | Value |
|---|---|
| Vivado | 2025.1 |
| Red Pitaya OS | 3.x |
| Install path | AMD/Xilinx default (`/opt/Xilinx/`) |
| Host | Ubuntu (version TBC — check what 2025.1 supports) |

**Verify this pairing before writing any build scripts.** Red Pitaya's documentation is explicit that the Vivado version must match the OS version, because the automatic project build scripts are written for a specific version and will not work with a different one. Historically OS 2.x required Vivado 2020.1 exactly, and the docs note that future OS releases migrate to Vitis rather than Vivado.

So the first thing to do is check the official documentation for the OS 3.x branch and confirm what toolchain it actually expects. If OS 3.x wants a different version, or wants Vitis instead of Vivado, tell me — don't paper over the mismatch. Record whatever the docs actually say in `CLAUDE.md`, along with the register base address and address decode.

---

## The plant

Four channels, same topology, different parameters. All values are Radia-computed or vendor-quoted; coil resistances are **pending a Kelvin measurement** (see Open unknowns).

| Channel | Inductance (pair) | Resistance (pair) | Max current |
|---|---|---|---|
| MOT anti-Helmholtz | 16 µH | 6.4 mΩ | 100 A |
| Z shim (Helmholtz) | 29 µH | 11.6 mΩ | 60 A |
| X shim (racetrack pair) | 57 µH | 14.2 mΩ | 60 A |
| Y shim (racetrack pair) | 57 µH | 14.2 mΩ | 60 A |

Total loop resistance is higher than the coil alone — add pass-bank on-state and cabling, roughly 10–15 mΩ for the MOT channel. The open-loop pole for the MOT channel is therefore around 100 Hz.

Rail is ~3 V for hold, with a ~15 V boost capacitor switched in for fast ramps.

**Loop targets:** unity-gain crossover 2–5 kHz, settling to 0.1% in under 1 ms, current stability better than 0.1%.

At 125 MS/s against a 5 kHz crossover there are four orders of magnitude of margin, so decimate the PI to ~1 MS/s to simplify the fixed-point arithmetic. Loop bandwidth is set by the analog side — gate charge, source-sense op-amp bandwidth, coil L/R, chamber eddy currents — not by the digital hardware. Don't optimise firmware for speed.

---

## Signal chain

**Sensor:** LEM LF 310-S closed-loop Hall transducer on the MOT channel (2000:1, 0.5 mA/A, DC–100 kHz, 0.5 µs response, ±0.2% accuracy, 0.05% linearity). LEM LF 205-S on the 60 A shim channels. Secondary current goes across a burden resistor, then through an instrumentation amp, then into IN1. Scaled so full scale maps to approximately ±1 V.

**Actuators:** two, and the loop hands off between them on the sign of the error.

- **OUT1 → linear pass bank.** IXTK200N10L2 MOSFETs, roughly 8–10 in parallel, each with its own source-sense op-amp loop for current sharing. OUT1 drives the shared reference those loops follow. Active when commanding current up or holding.
- **OUT2 → active clamp.** Same conditioning chain, separate stage. Active when commanding current down. A deadband at zero error selects which output is live; **both must never drive simultaneously.**

**H-bridge:** IRFP4468 trench MOSFETs, switched fully on or fully off at zero current only. Not servo'd.

---

## Port assignment

| Port | Dir | Signal |
|---|---|---|
| IN1 | in | Measured coil current (signed) |
| IN2 | in | Setpoint from computer control analog out, ÷10 to ±1 V |
| OUT1 | out | Pass bank command |
| OUT2 | out | Active clamp command |
| DIO0_P | out | Bridge polarity (one bit, two diagonals) |
| DIO1_P | out | Bridge enable (all four FETs off when low) |
| DIO2_P | out | Boost-cap switch enable |
| DIO3_P | in | Flip request (TTL, level-shifted) |
| DIO4_P | in | Shot trigger / arm |
| DIO5_P | in | Fault flag from interlock (read-only) |
| DIO6_P | out | Watchdog heartbeat |
| AIN0 | in | Coil temperature (logging only) |
| AIN1 | in | Rail voltage monitor |
| AOUT0 | out | Transducer offset trim |

IN1 and IN2 use the same scaling so the error is a direct subtraction with no gain mismatch.

---

## Flip state machine

Triggered by an edge on DIO3_P:

1. Drive setpoint to zero; active clamp pulls current down.
2. Confirm current is inside a zero window (e.g. ±0.5 A) by reading signed IN1.
3. Deassert bridge enable (DIO1_P) — all four FETs off.
4. Wait dead time (~1 µs).
5. Toggle bridge polarity (DIO0_P).
6. Reassert bridge enable; release setpoint to ramp up in the new direction.

Step 2 is the point of the whole thing: flipping at nonzero current dumps the coil's stored energy into body diodes on devices that were just switched.

---

## Safety invariants — encode these in the FPGA, not in Python

These are non-negotiable and should be asserted in the testbench:

- **Hard output clamp** on OUT1 and OUT2 in fixed point, so no command can exceed ~110% of the channel's rated current regardless of integrator state or setpoint.
- **Reset state is off.** All DIO outputs low, both DAC outputs at zero. E1 pins are indeterminate during Linux boot; the firmware must not make that worse.
- **OUT1 and OUT2 mutually exclusive** at all times.
- **Bridge enable requires active assertion.** Both polarity states with enable low must be a valid, safe, non-conducting condition.
- **Heartbeat toggles unconditionally** whenever the FPGA is running, so an external monostable can drop bridge enable on a firmware hang.
- **Anti-windup** on the integrator, including during the clamp phase and while the bridge is disabled.

The interlock chain (flow switch, thermal cutouts, leak pad, overtemperature comparator, overcurrent comparator) is hardware and does not pass through the Pitaya. Firmware reads DIO5_P to report a fault and has no path to clear one. Do not write code that attempts to.

---

## What I want built

Work in this order, one subsystem per session, tests green before moving on.

**1. `CLAUDE.md`** — the hardware facts above plus toolchain paths, Vivado version, OS image, register map base address, and make targets. This exists so you stop inventing register offsets.

**2. Design doc I review before you write HDL.** Signal path with **Q-format and gain-per-LSB at every node**: ADC counts → amps, error, integrator word width, coefficient widths, DAC counts → volts → gate command → amps. State the decimation ratio and where saturation happens. Fixed-point scaling is where LLM-written DSP quietly fails, so make it explicit and reviewable rather than buried in code.

**3. Float reference model in Python.** Plant (RL load + pass bank + clamp) plus the PI, per channel, parameterised by the table above. This is the thing I trust and the HDL gets compared against it.

**4. cocotb testbench** with a simulated plant, running under Verilator or Icarus. Assertions: step response meets the settling spec, no overflow at rail-to-rail input, anti-windup holds, every safety invariant above, agreement with the float model within a stated tolerance. Iterate until it passes — run the sim yourself, don't hand me untested HDL.

**5. PI core and flip FSM in Verilog**, developed against that testbench.

**6. Vivado build as Tcl**, not a GUI block design. Scriptable means reviewable and diffable.

**7. HIL scripts, last.** Build bitstream, deploy to board, run a swept-sine open-loop transfer function measurement, dump CSV. First runs go into a dummy resistive load with no coil connected.

Before starting, read PyRPL, Linien, and Pavel Demin's red-pitaya-notes and tell me which to fork rather than starting from a blank repo. A working register map plus a Vivado project that already builds is worth more than generated scaffolding.

---

## Open unknowns — parameterise, don't hardcode

These are genuinely unmeasured. Every one of them must be a config value with a comment saying it's provisional:

- **Actual coil resistance.** The 6.4 mΩ figure is computed; a two-wire multimeter reading gave 0.2 Ω, which is a measurement artifact. A Kelvin (4-wire) measurement is outstanding. Total loop R sets the open-loop pole, so the PI gains depend on it.
- **Chamber eddy-current settling time.** This, not the electrical decay, is what actually limits how fast the field settles after a flip. Unmeasured. Any delay constant in the FSM is a placeholder until it is.
- **Burden resistor value.** Constrained by the transducer's secondary compliance against the ±15 V supply. Pick a value from the datasheet's R_M table; 10 Ω gives 0.5 V at 100 A, 20 Ω gives 1.0 V.
- **Clamp voltage.** Coupled to the bridge FET voltage rating — a higher clamp forces higher-rated devices with worse R_DS(on). The ~30 V figure used in flip-timing estimates is illustrative.
- **PI gains.** Set from the measured open-loop step response, not from the computed model.

Flag it explicitly if any design decision depends on one of these, rather than picking a plausible number and moving on.

---

## Working style

Narrow sessions, small commits, tests before implementation where possible. If something can't be verified in simulation, say so rather than asserting it works. If a spec above is internally inconsistent or physically wrong, push back — I'd rather fix it now than in silicon.
