# Welcome to coil-servo

This is the digital current servo for the magnet coils on the Rb–Cs
machine: one Red Pitaya board per coil channel, with the feedback loop (a
PI controller), the H-bridge polarity-flip state machine, and all safety
logic running inside the board's FPGA. Python is only used for tuning,
measurements, and configuration — never for control decisions. You don't
need to be a software engineer or an FPGA developer to work here; the
documentation is written for physicists and defines its jargon as it goes.

**Project status:** everything is written and verified in simulation
(54 automated tests). Nothing has run on real hardware yet — the first
Vivado build and the dummy-load bring-up are the next milestones.

## Your first hour

1. Clone the repo and install the Python environment (any OS):
   Python ≥ 3.11, then from the repo root:

   ```bash
   pip install -e .[dev]
   ```

2. Run the whole test suite — models, simulated FPGA logic, host tools:

   ```bash
   pytest
   ```

   You should see **54 passed** in ~15 s (you'll need Icarus Verilog
   installed for the FPGA simulations — see README "Setup A").

3. Read, in this order:
   - [README.md](README.md) — how the three computers fit together, all
     setup steps, and the "what's a knob vs. what's a rebuild" table.
   - [docs/design.md](docs/design.md) — the signed-off control-loop
     design. Every number in the loop and why. This is the contract all
     the code implements; if code and this document disagree, the code is
     wrong.
   - [docs/register_map.md](docs/register_map.md) — how Python talks to
     the FPGA.
   - [docs/bringup.md](docs/bringup.md) — the first-hardware checklist,
     when you get there.
   - [BOOTSTRAP.md](BOOTSTRAP.md) — the original project brief, for full
     context.

## How to review or extend the work

The verification is layered — each layer is checked against the one below:

1. `docs/design.md` defines the design (review this with physics eyes).
2. `model/` implements it in trusted floating-point Python, including the
   plant (coil + pass bank + clamp). Tests assert the loop targets:
   crossover 2–5 kHz, settling to 0.1 % in < 1 ms.
3. `model/coil_servo_model/fixed_point.py` is a bit-exact integer mirror.
4. The Verilog in `modules/` and `cores/` is compared against that mirror
   **bit-for-bit, every update cycle** by the testbenches in `sim/`.
5. `host/` (deploy, check, watch, step, sweep) is tested against a mock
   server, without hardware.

So: change any layer, run `pytest`, and disagreements between layers are
caught immediately. If you add or change behavior, extend the matching
test — the repo's rule is *tests green before moving on*.

**Not yet verified (be appropriately suspicious):** the Vivado build
itself (`projects/coil_servo/block_design.tcl`, pin constraints, timing)
has never been run — first build happens on the Ubuntu machine per README
"Setup B"; the E1 pin table and analog scalings come from documentation,
not measurement; and every value marked PROVISIONAL in
`host/config/channels.toml` is a placeholder awaiting a real measurement
(coil resistance via Kelvin probe, eddy settling time, burden resistor,
PI gains from the measured step response).

## Working with Claude in this repo

Open a Claude Code session in the repo folder and it automatically loads
[CLAUDE.md](CLAUDE.md) — the hardware facts, register decode, safety
rules, and working conventions. Useful habits established here:

- **One subsystem per session, small commits, tests green before moving
  on.** Ask Claude to run `pytest` before committing anything.
- **Never let it invent register offsets** — `docs/register_map.md` is
  the single source of truth; changes go there first (CLAUDE.md already
  tells it this).
- **Simulation before hardware.** If something can't be verified in
  simulation, the honest answer is "unverified until the bench/board run",
  and Claude should say so.
- Good first prompts: *"read CLAUDE.md and docs/design.md, then explain
  the flip state machine to me"*, *"run pytest and walk me through what
  the integration bench verifies"*, or *"here's the Vivado build log from
  the Ubuntu machine — fix what it complains about."*

## Safety ground rules (also enforced in the FPGA, but know them)

- The hard output clamp is 100 % of rated current, in fabric; OUT1/OUT2
  can never drive simultaneously; the bridge never flips at nonzero
  current; reset state is everything-off.
- The hardware interlock chain does not pass through the Red Pitaya.
  Firmware only *reports* a fault (DIO5) — never write or accept code
  that tries to clear one.
- `servo_enable` off = graceful ramp-to-zero, not an instant bridge drop.
- First hardware runs: dummy resistive load only, per
  [docs/bringup.md](docs/bringup.md).

Questions with no written answer yet probably belong in the docs — add
them there (or have Claude do it) rather than answering only in chat.
