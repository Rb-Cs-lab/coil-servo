# coil-servo

Digital current servo for the magnetic field coils of a dual-species Rb–Cs
ultracold atoms machine. One Red Pitaya STEMlab 125-14 board runs each coil
channel (MOT, X/Y/Z shims). The feedback loop itself — a PI controller plus
the H-bridge polarity-flip state machine and all safety logic — runs inside
the Red Pitaya's FPGA, where its timing is deterministic. Python is used
only for tuning, transfer-function sweeps, diagnostics, and configuration;
no control decisions are made in software.

**Start here:**

- New to the project (or to FPGAs)? Read *How the pieces fit* and *Setup*
  below.
- Full requirements and lab context: [BOOTSTRAP.md](BOOTSTRAP.md)
- Hardware facts, pinout, toolchain versions: [CLAUDE.md](CLAUDE.md)
- The control-loop design (every number in the loop, and why):
  [docs/design.md](docs/design.md)
- Register reference (how Python talks to the FPGA):
  [docs/register_map.md](docs/register_map.md)

## How the pieces fit

Three computers are involved, doing different jobs:

```
 Your PC (any OS)                 Ubuntu build machine         Red Pitaya (on the coil driver)
 ──────────────────               ────────────────────         ──────────────────────────────
 • edit code                      • Vivado 2025.1 turns        • FPGA runs the servo loop
 • run simulations (cocotb)         the Verilog into a         • Linux (official OS 3.x) runs
 • run the Python float model       "bitstream" (.bit file,      a small server so Python on
 • tune/diagnose over Ethernet      the FPGA's compiled          your PC can read/write the
                                    program)                     FPGA's control registers
```

Vocabulary used throughout the repo:

- **FPGA** — a chip whose logic circuits are configured by a file you build;
  our servo is a circuit, not a program, so nothing can preempt or delay it.
- **Bitstream** — the compiled configuration file loaded into the FPGA.
- **Register** — a small named value inside the FPGA (a gain, a limit, a
  status flag) that Linux/Python can read or write over a memory bus. All
  runtime knobs are registers; see [docs/register_map.md](docs/register_map.md).
- **cocotb** — a Python framework that runs our Verilog in a simulator and
  checks it against pass/fail assertions, so the logic is verified before it
  ever touches hardware.

## Repository tour

| Path | What it is |
|---|---|
| `cores/*.v` | Verilog building blocks (each becomes a Vivado IP block). `coil_servo_top.v` is the whole servo |
| `modules/*.v` | The servo submodules (PI, decimator, error path, output mux, flip FSM, heartbeat) instantiated by `coil_servo_top` |
| `projects/coil_servo/` | The servo's block design (Tcl script — text, diffable, no GUI) |
| `projects/playground/` | Known-good upstream demo; used to smoke-test a new Vivado install |
| `scripts/`, `Makefile`, `cfg/` | Build machinery and pin constraints (from pavel-demin/red-pitaya-notes, tag `20251012`, MIT) |
| `docs/` | Design doc and register map |
| `model/` | Python reference model: plant + float PI (trusted) + bit-exact fixed-point mirror. Run its tests with `pytest` after `pip install -e .` |
| `sim/` | cocotb testbenches: each servo core is simulated with Icarus Verilog and compared bit-for-bit against the Python fixed-point model. Runs as part of `pytest` |
| `host/` | Lab-PC tools: `deploy` (bitstream + register server onto the board), `check` (sanity), `step` and `sweep` (measurements → CSV), `config/channels.toml` (every provisional value, per board). Boards are reached over **wired Ethernet only** — see [docs/bringup.md](docs/bringup.md) |

## Setup

### A. Simulation and model environment (any machine, including Windows)

Needed by anyone touching the loop design; no Vivado required.

1. Install Python ≥ 3.11.
2. Install [Icarus Verilog](https://steveicarus.github.io/iverilog/)
   (the free simulator cocotb drives): Ubuntu `sudo apt install iverilog`;
   Windows: the bundled installer from the Icarus site works fine.
3. From the repo root: `pip install cocotb pytest numpy scipy matplotlib`
   (a `pyproject.toml` will pin these once `model/` lands).

### B. FPGA build machine (Ubuntu 24.04 or 22.04 only)

Only needed to produce bitstreams. Versions are **not** interchangeable:
Red Pitaya OS 3.x requires Vivado/Vitis **2025.1** exactly
([why](https://redpitaya.readthedocs.io/en/latest/developerGuide/fpga/getting_started/vivado_install.html)),
and AMD supports 2025.1 only on Ubuntu 22.04/24.04.

1. Download the "Vivado ML" 2025.1 installer from
   [amd.com](https://www.xilinx.com/support/download.html) (free AMD account
   required). During install select **Vivado ML Standard** (free edition —
   it covers our Zynq-7010 chip) and install to the default `/opt/Xilinx/`.
   Also install **Vitis 2025.1** (same installer, tick the box) — the OS 3.x
   toolchain expects the pair.
2. `sudo apt install libtinfo5 make python3` (Vivado needs the first).
3. Smoke-test with the known-good demo project before trusting anything:

   ```bash
   source /opt/Xilinx/Vivado/2025.1/settings64.sh
   make NAME=playground bit
   ```

   ~10–20 min. If this succeeds, the toolchain is good.

### C. The Red Pitaya board

1. Flash the official **Red Pitaya OS 3.x** image
   ([downloads](https://redpitaya.readthedocs.io/en/latest/quickStart/download/download.html))
   onto the SD card (balenaEtcher or similar). We use the official OS —
   *not* pavel-demin's Alpine image, despite building on his FPGA flow.
2. Connect Ethernet; the board announces itself as `rp-xxxxxx.local`
   (sticker on the Ethernet jack). `ssh root@rp-xxxxxx.local`
   (default password `root` — change it).
3. **Before connecting the analog front end**, check the input jumpers on
   IN1/IN2 are set to **LV (±1 V)** — the firmware's calibration assumes it.

## Building and loading the servo bitstream

On the Ubuntu machine:

```bash
source /opt/Xilinx/Vivado/2025.1/settings64.sh
make bit                    # builds tmp/coil_servo.bit  (NAME defaults to coil_servo)
make coil_servo.bit.bin     # byte-swapped copy the board's loader accepts
scp coil_servo.bit.bin root@rp-xxxxxx.local:/root/
```

On the board:

```bash
fpgautil -b /root/coil_servo.bit.bin
```

Loading a bitstream is instant and non-persistent: a power cycle reverts to
the stock Red Pitaya image until you load again (deployment scripts in
`host/` will automate load-on-boot later). All four boards run the *same*
bitstream — per-channel differences (full-scale current, gains, limits,
timings) are runtime register settings from `host/config/channels.toml`.

## Changing things later — what's a knob vs. what's a rebuild

Everything you are likely to touch is a **runtime register write from
Python — no Vivado, no rebuild, takes effect immediately**:

| Want to change | How |
|---|---|
| PI gains, output clamp, deadband | CFG registers (words 2–7) |
| Flip timing: zero window, dead time, settle delay, timeout | CFG registers (words 8–12) |
| Setpoint source (analog IN2 vs. register), boost-cap mode | `ctrl` register bits |
| Polarity/active level of the DIO *inputs* (flip request, arm, fault) | `dio_invert` register (word 13) |
| Sensor scaling after a burden-resistor change, or LV→HV jumper move | edit `I_FS` in `host/config/channels.toml` (it's a calibration constant — one number) |

Things that deliberately **do** require editing a file and rebuilding the
bitstream (~15 min, then reload):

| Change | Where | Why it's build-time |
|---|---|---|
| Which physical pin a signal uses | `projects/coil_servo/block_design.tcl` | pin routing is part of the compiled circuit |
| Polarity of DIO *outputs* (bridge enable/polarity, boost) | named constant in the safety core | the FPGA's power-on state must be safe with zero configuration, so "pin low = FETs off" is fixed in the fabric, not in a register a bug could flip |
| Word widths, decimation ratio | Verilog + `docs/design.md` | changes the arithmetic; must re-pass the testbench and re-review the design doc |

## Safety model (summary — full list in CLAUDE.md)

The FPGA enforces, independent of any software: a hard output clamp at 100 %
of each channel's rated current; power-on state = everything off; OUT1/OUT2
never active simultaneously; bridge polarity never flips at nonzero current
(and a flip that can't reach zero current times out into a safe hold instead
of proceeding); a heartbeat on DIO6 that an external monostable can use to
kill the bridge if the FPGA hangs. The hardware interlock chain does not go
through the Red Pitaya at all — the firmware can *report* a fault (DIO5) but
has no way to clear one.

## Project status

| # | Deliverable | State |
|---|---|---|
| 1 | Repo + toolchain + CLAUDE.md | ✅ done |
| 2 | Fixed-point design doc | ✅ reviewed 2026-08-26 |
| 3 | Python float reference model (`model/`) | ✅ 29 tests green (`pytest`) |
| 4 | cocotb testbench (`sim/`) | ✅ decimator + PI benches green under Icarus |
| 5 | PI core + flip FSM Verilog (`cores/`) | ✅ all six servo cores benched (PI, decimator, error path, output mux, flip FSM, heartbeat) |
| 6 | Vivado build of the full design | Tcl written; integration top simulated ✅ — needs the first `make bit` on the Ubuntu machine |
| 7 | HIL scripts (`host/`), dummy-load first | ✅ written + protocol/config/math tested without hardware; first real run follows [docs/bringup.md](docs/bringup.md) |
