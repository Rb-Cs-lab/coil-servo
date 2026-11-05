# Welcome to coil-servo

This is the digital current servo for the magnet coils on the Rb–Cs
machine: one Red Pitaya board per coil channel, with the feedback loop (a
PI controller), the H-bridge polarity-flip state machine, and all safety
logic running inside the board's FPGA. Python is only used for tuning,
measurements, and configuration — never for control decisions. You don't
need to be a software engineer or an FPGA developer to work here; the
documentation is written for physicists and defines its jargon as it goes.

## Where the project stands

- **Firmware: done and commissioned on a real board.** The design is
  verified in simulation (56 automated tests), the FPGA build meets
  timing, and it was brought up on the MOT channel's Red Pitaya on
  2026-08-27: heartbeat and its cut-out, arm/fault/flip inputs, the hard
  output clamp, OUT1/OUT2 mutual exclusion, and the full flip sequence
  with dead time (scope-captured) all behave exactly as simulated. A
  loopback test (OUT1 cabled to IN1) closed the first real feedback loop:
  ~3.5 kHz bandwidth, on-target to 0.04 %, and the step/sweep measurement
  tools were validated against known truth. Record:
  [docs/lab_notes_2026-08-27.md](docs/lab_notes_2026-08-27.md).
- **Power electronics: being designed, not built.** The pass bank, active
  clamp, H-bridge, current sensor chain, rails, and protection circuits
  are being worked out with an external circuit designer (Ben Brown,
  Harvard). The contract between the servo and that hardware is
  [docs/servo_interface_brief.md](docs/servo_interface_brief.md) —
  revised 2026-09-08 after his first review; section 6 lists the open
  questions.
- **Not yet built:** the heartbeat watchdog monostable (external,
  required before running real current unattended) and thermal switches
  in the interlock chain.
- **Next hardware milestone:** dummy-load campaign per
  [docs/bringup.md](docs/bringup.md) §4, once a power stage exists. Values
  marked PROVISIONAL in `host/config/channels.toml` get replaced by
  measurements then.

## Your first hour

1. Clone `github.com/Rb-Cs-lab/coil-servo` and set up Python (any OS,
   Python ≥ 3.11). From the repo root, make a virtual environment and
   install:

   ```bash
   python -m venv .venv
   ```

   then activate it (Windows: `.venv\Scripts\activate`; Linux/WSL:
   `source .venv/bin/activate`) and run:

   ```bash
   pip install -e .[dev]
   ```

2. Run the whole test suite — models, simulated FPGA logic, host tools:

   ```bash
   pytest
   ```

   You should see **56 passed** in ~20 s (Icarus Verilog must be
   installed for the FPGA simulations — see README "Setup A"). If you get
   `No module named coil_servo_host`, you're running a Python other than
   the activated `.venv` one.

3. Read, in this order:
   - [README.md](README.md) — how the three computers fit together, all
     setup steps, and the "what's a knob vs. what's a rebuild" table.
   - [docs/servo_interface_brief.md](docs/servo_interface_brief.md) —
     the system as a black box, with the full schematic: what each port
     does, what the servo can do (ramp rates, stability), what it needs
     from the power stage, and the power budget.
   - [docs/design.md](docs/design.md) — the signed-off control-loop
     design. Every number in the loop and why. If code and this document
     disagree, the code is wrong.
   - [docs/operating_scenarios.md](docs/operating_scenarios.md) — what
     happens in every situation an experimental cycle produces (ramps,
     snap-offs, field reversals, faults). Read this before writing
     sequences.
   - [docs/register_map.md](docs/register_map.md) — how Python talks to
     the FPGA.
   - [docs/bringup.md](docs/bringup.md) — the hardware checklist.
   - [docs/future_upgrades.md](docs/future_upgrades.md) — designed but
     shelved options (ppm-class stability, eddy-current feedforward,
     temperature soft-stop, slew limiter), with their reasoning.

## The lab setup

- **Build machine:** the lab NUC, running Ubuntu under WSL2 with Vivado
  2025.1. Only needed to rebuild the FPGA bitstream
  (`make coil_servo.bit.bin` there). This Windows PC has no Vivado.
- **Boards:** reached over **wired Ethernet only, never WiFi**. The MOT
  board is at `192.168.0.65` (channel name `mot` in
  `host/config/channels.toml`).
- **Day-to-day tools** (from the activated `.venv`):
  `python -m coil_servo_host.deploy mot` loads the bitstream (refuses if
  the servo is active), `...check mot` is a quick health check,
  `...watch mot` is the live status display, `...step` / `...sweep`
  measure the loop.
- **The bitstream does not survive a reboot** — after a power cycle,
  redeploy.

## How to review or extend the work

The verification is layered — each layer is checked against the one below:

1. `docs/design.md` defines the design (review this with physics eyes).
2. `model/` implements it in trusted floating-point Python, including the
   plant (coil + pass bank + clamp).
3. `model/coil_servo_model/fixed_point.py` is a bit-exact integer mirror.
4. The Verilog in `modules/` and `cores/` is compared against that mirror
   **bit-for-bit, every update cycle** by the testbenches in `sim/`.
5. `host/` tools are tested against a mock server, without hardware.
6. The bench campaign confirmed the built bitstream matches all of the
   above on a real board.

Change any layer, run `pytest`, and disagreements between layers are
caught immediately. If you add or change behavior, extend the matching
test — the repo's rule is *tests green before moving on*.

## Working with Claude in this repo

Open a Claude Code session in the repo folder and it automatically loads
[CLAUDE.md](CLAUDE.md) — the hardware facts, register decode, safety
rules, and working conventions. Habits established here:

- **One subsystem per session, small commits, tests green before moving
  on.** Ask Claude to run `pytest` before committing anything.
- **Never let it invent register offsets** — `docs/register_map.md` is
  the single source of truth; changes go there first.
- **Simulation before hardware.** If something can't be verified in
  simulation, the honest answer is "unverified until the bench run".
- **Deliverables for the lab** (notes, summaries, briefs) go out as
  OneNote-pasteable HTML; the repo keeps `.md` copies so GitHub renders
  them.
- Good first prompts: *"read the interface brief and explain the power
  stage to me"*, *"walk me through what happens during a field reversal"*,
  *"run pytest and explain what the integration bench verifies"*, or
  *"here are Ben's answers to section 6 — update the brief and
  channels.toml."*

## Safety ground rules (also enforced in the FPGA, but know them)

- The hard output clamp is 100 % of rated current, in fabric; OUT1/OUT2
  can never drive simultaneously; the bridge never flips at nonzero
  current; reset state is everything-off.
- The hardware interlock chain does not pass through the Red Pitaya.
  Firmware only *reports* a fault (pin 13) — never write or accept code
  that tries to clear one. Thermal protection belongs in that chain too.
- `servo_enable` off = graceful ramp-to-zero, not an instant bridge drop.
- Don't run real current unattended until the heartbeat monostable
  exists.
- First power-stage runs: dummy resistive load only, per
  [docs/bringup.md](docs/bringup.md).

Questions with no written answer yet probably belong in the docs — add
them there (or have Claude do it) rather than answering only in chat.
