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
- Hardware being built (MVP, Nov 2026): [docs/mvp_architecture.md](docs/mvp_architecture.md);
  what the servo promises the power stage: [docs/servo_interface_brief.md](docs/servo_interface_brief.md);
  original requirements and lab context: [docs/history/BOOTSTRAP.md](docs/history/BOOTSTRAP.md)
- Programming experimental sequences: [docs/operating_scenarios.md](docs/operating_scenarios.md)
- Bringing hardware up, step by step: [docs/bringup.md](docs/bringup.md)
- Options considered and shelved: [docs/future_upgrades.md](docs/future_upgrades.md)
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
| `cores/*.v` | Verilog building blocks (each is packaged as a Vivado "IP block" — a reusable circuit component). `coil_servo_top.v` is the whole servo and the only one we wrote; the `axi_hub.v`/`axis_*.v` files are unmodified upstream infrastructure you are not expected to read |
| `modules/*.v` | The servo submodules (PI, decimator, error path, setpoint slew limiter, OUT1 dither, output mux, flip FSM with over-current trip, heartbeat) instantiated by `coil_servo_top` |
| `projects/coil_servo/` | The servo's block design (Tcl script — text, diffable, no GUI) |
| `projects/playground/` | Known-good upstream demo; used to smoke-test a new Vivado install |
| `scripts/`, `Makefile`, `cfg/` | Build machinery and pin constraints (from pavel-demin/red-pitaya-notes, tag `20251012`, MIT) |
| `docs/` | MVP architecture, interface brief, operating scenarios, bring-up checklist, register map, loop design, lab notes, future upgrades |
| `model/` | Python reference model: plant + float PI (trusted) + bit-exact fixed-point mirror. Run its tests with `pytest` after `pip install -e .` |
| `sim/` | cocotb testbenches: each servo core is simulated with Icarus Verilog and compared bit-for-bit against the Python fixed-point model. Runs as part of `pytest` |
| `host/` | Lab-PC tools: `deploy` (bitstream + register server onto the board, then a safe-state check), `check` (sanity), `watch` (live current/state display), `step` and `sweep` (measurements → CSV), `config` (prints the register values derived from the configuration), `config/channels.toml` (every measured or provisional number, per board). Boards are reached over **wired Ethernet only** — see [docs/bringup.md](docs/bringup.md) |

## Setup

### A. Simulation and model environment (any machine, including Windows)

Needed by anyone touching the loop design; no Vivado required.

1. Install Python ≥ 3.11.
2. Install [Icarus Verilog](https://steveicarus.github.io/iverilog/)
   (the free simulator cocotb drives): Ubuntu `sudo apt install iverilog`;
   Windows: the bundled installer from the Icarus site works fine.
3. From the repo root: `pip install -e .[dev]` — this installs the repo's
   own Python packages (`coil_servo_model`, `coil_servo_host`) plus pytest,
   cocotb, and matplotlib. Without the `-e .` part, `pytest` fails with
   import errors.
4. Check everything: `pytest` from the repo root should end in
   "111 passed" (model tests + simulated-hardware tests + host-tool tests;
   about 2 minutes, most of it the simulated FPGA).

### B. FPGA build machine (Ubuntu 24.04 or 22.04 only)

Only needed to produce bitstreams. Versions are **not** interchangeable:
Red Pitaya OS 3.x requires Vivado/Vitis **2025.1** exactly
([why](https://redpitaya.readthedocs.io/en/latest/developerGuide/fpga/getting_started/vivado_install.html)),
and AMD supports 2025.1 only on Ubuntu 22.04/24.04.

1. Download the "Vivado ML" 2025.1 installer from
   [amd.com](https://www.xilinx.com/support/download.html) (free AMD account
   required). During install select **Vivado ML Standard** (free edition —
   it covers our Zynq-7010 chip), and under device support tick only
   **SoCs → Zynq-7000** (saves tens of GB). Install to the default
   `/opt/Xilinx/`. Red Pitaya's docs pair OS 3.x with Vivado **and** Vitis
   2025.1, but this repo's build targets only ever invoke `vivado` — Vitis
   is needed only if the removed device-tree/FSBL make targets are ever
   restored from git history, so on a disk-constrained machine you can
   skip it.
2. `sudo apt install make python3 locales && sudo locale-gen en_US.UTF-8`
   (Vivado crashes at startup with a `locale ... not valid` abort on
   minimal/WSL installs that haven't generated that locale), plus the
   `libtinfo5` library Vivado needs. On Ubuntu 22.04 that's just `sudo apt install libtinfo5`; on
   24.04 the package was removed from the archive (a well-known Vivado
   gotcha), so install it from the 22.04 pool:

   ```bash
   wget http://mirrors.edge.kernel.org/ubuntu/pool/universe/n/ncurses/libtinfo5_6.3-2ubuntu0.2_amd64.deb && sudo apt install ./libtinfo5_6.3-2ubuntu0.2_amd64.deb
   ```

   (If that 404s, the package was revised again: browse
   <http://mirrors.edge.kernel.org/ubuntu/pool/universe/n/ncurses/> and take
   the newest `libtinfo5_6.3-*_amd64.deb`.)
3. Smoke-test with the known-good demo project before trusting anything:

   ```bash
   source /opt/Xilinx/2025.1/Vivado/settings64.sh
   make NAME=playground bit
   ```

   ~10–20 min. If this succeeds, the toolchain is good.

#### B-alt: Ubuntu inside a Windows laptop (WSL2)

No dedicated Ubuntu machine? WSL2 (Microsoft's built-in "run Ubuntu inside
Windows") works for this repo because our build is pure command-line.
Caveat: AMD doesn't officially support WSL — if you hit inexplicable
failures, fall back to a real Ubuntu install. Needs ~100 GB free disk and
ideally 16 GB RAM.

1. In an **administrator** PowerShell: `wsl --install -d Ubuntu-24.04`,
   reboot, and create a Linux username/password when prompted.
2. Inside the Ubuntu terminal, install the prerequisites from step 2 above
   (including the libtinfo5 workaround — WSL's Ubuntu 24.04 has the same
   issue).
3. Download the Linux "Unified Installer" for 2025.1 on the Windows side
   (the file is named like
   `FPGAs_AdaptiveSoCs_Unified_SDI_2025.1_<date>_Lin64.bin`), then from
   Ubuntu, copy it into the Linux home first — `chmod` doesn't work on the
   Windows-mounted drive:

   ```bash
   sudo mkdir -p /opt/Xilinx && sudo chown $USER /opt/Xilinx
   cp "/mnt/c/Users/<you>/Downloads/FPGAs_AdaptiveSoCs_Unified_SDI_2025.1_"*.bin ~/ && cd ~
   chmod +x FPGAs_AdaptiveSoCs_Unified_SDI_2025.1_*.bin && ./FPGAs_AdaptiveSoCs_Unified_SDI_2025.1_*.bin
   ```

   (The installer's window appears via WSL's built-in graphics support.)
   Select Vivado ML Standard, Zynq-7000 device support only, no Vitis
   (the bundled "Vitis HLS" inside Vivado is fine and can't be removed).
   The installer creates `/opt/Xilinx/2025.1/Vivado/` — that layout is
   where the `settings64.sh` in the build commands lives.
4. Clone the repo **inside the Linux filesystem** (`~/coil-servo`, not
   `/mnt/c/...` — building on the Windows-mounted disk is painfully slow)
   and build per the section above.
5. Copy the result back where you need it:
   `cp coil_servo.bit.bin /mnt/c/Users/<you>/Desktop/` — or run the deploy
   tool directly from WSL (if `rp-xxxxxx.local` doesn't resolve inside
   WSL, use the board's IP address in channels.toml).

If a build dies with an out-of-memory kill, give WSL more RAM: create
`C:\Users\<you>\.wslconfig` containing `[wsl2]` and `memory=12GB`, then
`wsl --shutdown` and retry.

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
4. **After every board reboot** the servo bitstream and the register server
   are gone (the board boots the stock image). Re-run `deploy` (below); the
   host tools say "no register server answering … run deploy" when this
   is the problem. (On 2026-11-03 `check` failed for exactly this reason.)

## Building and loading the servo bitstream

On the Ubuntu machine:

```bash
source /opt/Xilinx/2025.1/Vivado/settings64.sh
make bit                    # builds tmp/coil_servo.bit  (NAME defaults to coil_servo)
make coil_servo.bit.bin     # byte-swapped copy the board's loader accepts
```

Then copy `coil_servo.bit.bin` to the lab PC and use the deploy tool — it
copies the bitstream and the register server to the board, loads the FPGA,
and starts the server, all in one command:

```bash
python -m coil_servo_host.deploy mot --bitstream coil_servo.bit.bin
```

(The manual equivalent, useful for debugging: `scp` the file to the board,
then `fpgautil -b /root/coil_servo.bit.bin` on the board.)

Loading a bitstream is instant and non-persistent: a power cycle reverts to
the stock Red Pitaya image until you deploy again. All four boards run the
*same* bitstream — per-channel differences (full-scale current, gains,
limits, timings) are runtime register settings from
`host/config/channels.toml`.

## Changing things later — what's a knob vs. what's a rebuild

Everything you are likely to touch is a **runtime register write from
Python — no Vivado, no rebuild, takes effect immediately**:

| Want to change | How |
|---|---|
| PI gains, OUT1 clamp, deadband | `kp`, `ki`, `out_clamp_counts`, `deadband_counts` in channels.toml (CFG words 2–7) |
| Over-current trip level, persistence, timeout | `trip_*` in channels.toml (CFG words 17–19) |
| Setpoint ramp limits | `slew_*_amps_per_ms` (CFG words 15, 20) |
| Dead-zone floor | `floor_frac` or `u_floor_counts` (CFG word 16) |
| OUT1 dither on/off | `dither_enable` (ctrl bit 20) |
| Flip timing: zero window, dead time, settle delay, timeout | CFG registers (words 8–12) |
| Setpoint source (analog IN2 vs. register) | `ctrl` register bit |
| Polarity/active level of the DIO *inputs* (flip request, arm, fault) | `dio_invert` register (word 13) |
| A new measurement: thresholds, g_fs, op-amp gain, OUT1 termination, burden, in-amp gain | edit the physics block in `host/config/channels.toml`; the sensor scale, clamp and floor are re-derived (`python -m coil_servo_host.config mot` shows them) |

Things that deliberately **do** require editing a file and rebuilding the
bitstream (~15 min, then reload):

| Change | Where | Why it's build-time |
|---|---|---|
| Which physical pin a signal uses | `projects/coil_servo/block_design.tcl` | pin routing is part of the compiled circuit |
| Polarity of DIO *outputs* (bridge enable/polarity, boost) | named constant in the safety core | the FPGA's power-on state must be safe with zero configuration, so "pin low = FETs off" is fixed in the fabric, not in a register a bug could flip |
| The register layout itself | `docs/register_map.md` first, then `registers.py` and the HDL; bump `MAP_REV` | the host refuses to configure a bitstream whose map revision it doesn't know |
| Word widths, decimation ratio | Verilog + `docs/design.md` | changes the arithmetic; must re-pass the testbench and re-review the design doc |

## Safety model (summary — full list in CLAUDE.md)

The FPGA enforces, independent of any software:

- power-on state = everything off, and leaving it needs `servo_enable`
  from the host **and** a fresh rising edge on the arm line;
- an **over-current trip on the measured current** (MOT: 100 A) that zeroes
  the outputs, lets the coil run down with the bridge closed, opens the
  bridge and latches until acknowledged — plus a hard clamp on the OUT1
  command;
- setpoint ramps limited to 200 A/ms up and 50 A/ms down;
- OUT2 and the boost line held at zero (not fitted in the MVP);
- bridge polarity never flips at nonzero current (a flip that can't reach
  zero current parks safely instead of proceeding); software "off" is a
  graceful ramp-to-zero;
- a heartbeat on DIO6 that keeps running only while the FPGA does.

The only automatic **hardware** protection in the MVP is the heartbeat
latch on the logic board: no heartbeat → bridge drivers disabled and
latched until someone presses reset (it powers up tripped). The SE-600-5's
own over-current protection backs up the firmware trip. There is no relay
interlock chain yet, and DIO5 (fault input) is not wired.

**Safety rules for the lab:**

- **No sustained current without water flowing** through the cold plates
  (~50 W per pass FET at 100 A). The first test round runs uncooled: short
  pulses, reduced current, a written duty limit.
- OUT2 and DIO2_P stay unconnected.
- **Disarm before pressing the latch reset** after any heartbeat trip — the
  firmware never saw the bridge open and may be wound up.

Losing the network does **not** stop the loop: if the Ethernet cable is
pulled or the lab PC crashes, the servo keeps running in the FPGA at its
last register settings. That is deliberate (a network glitch must not drop
a field mid-experiment).

## Project status

| # | Deliverable | State |
|---|---|---|
| 1 | Repo + toolchain + CLAUDE.md | ✅ done |
| 2 | Fixed-point design doc | ✅ reviewed 2026-08-26 |
| 3 | Python float reference model (`model/`) | ✅ 29 tests green (`pytest`) |
| 4 | cocotb testbench (`sim/`) | ✅ every core plus the integrated servo benched under Icarus |
| 5 | PI core + flip FSM Verilog (`cores/`) | ✅ all six servo cores benched (PI, decimator, error path, output mux, flip FSM, heartbeat) |
| 6 | Vivado build of the full design | ✅ built 2026-08-27 (WSL on the lab NUC), timing met after one pipeline fix |
| 7 | Hardware-in-the-loop scripts (`host/`), dummy-load first | ✅ first deploy 2026-08-27: heartbeat advancing at exactly 125 MHz, LED walk, safe IDLE state, ~1.3 mV ADC offset. Still pending: AOUT0 offset trim, XADC readout tool |
| 8 | **MVP power stage (Nov 2026)** | MVP parts bought; thresholds measured and FETs matched (11-02); logic board built and LED-tested (11-03); water-cooled plates machined and FETs mounted (11-04); pass bank assembled; H-bridge assembly in progress with Jeff |
| 9 | **MVP rewrite of the repo** ([brief](docs/mvp_rewrite_brief.md) §11) | ✅ steps 1–5 (docs, model, firmware + benches, host, docs sweep). Next: step 6 — rebuild the bitstream on the NUC; the board still runs the 2026-08-27 image, which the new host tools refuse to configure |

## License

MIT, with two copyright holders (see [LICENSE](LICENSE)): the FPGA build
framework and infrastructure cores come from Pavel Demin's
[red-pitaya-notes](https://github.com/pavel-demin/red-pitaya-notes)
(merged at tag `20251012`, history preserved); everything coil-servo
specific — the servo FPGA modules, reference model, host tools, and
documentation — is © 2026 The Rb-Cs Lab (Ni group), Harvard University.
