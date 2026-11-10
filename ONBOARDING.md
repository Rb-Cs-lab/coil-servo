# Welcome to coil-servo

This is the digital current servo for the magnet coils on the Rb–Cs
machine: one Red Pitaya board per coil channel, with the feedback loop (a
PI controller), the H-bridge polarity-flip state machine, and all safety
logic running inside the board's FPGA. Python is only used for tuning,
measurements, and configuration — never for control decisions. You don't
need to be a software engineer or an FPGA developer to work here; the
documentation is written for physicists and defines its jargon as it goes.

## Where the project stands (Nov 2026)

- **The hardware being built is the MVP, MOT channel only**
  ([docs/mvp_architecture.md](docs/mvp_architecture.md)): one 5 V / 100 A
  Mean Well supply, an H-bridge of four IXFN420N10T with isolated drivers,
  the two MOT coils in series with a LEM current sensor, and a **low-side
  linear pass bank** of six IXTN200N10L2 driven from the Red Pitaya's OUT1
  through a gain-8 op-amp stage. A small logic board turns the Red
  Pitaya's heartbeat into a latching hardware trip.
  - Status: parts bought; FET thresholds measured and matched (11-02);
    logic board built and LED-tested (11-03); water-cooled plates
    machined and FETs mounted (11-04); pass bank assembled; H-bridge
    assembly in progress with Jeff.
- **Firmware: rewritten for the MVP and verified in simulation (Nov
  2026).** On top of the August design it now has a dead-zone floor for
  the pass bank, an over-current trip at 100 A, setpoint ramp limits, an
  OUT1 dither for finer resolution, and a required arm *edge*. **The new
  bitstream still has to be built on the NUC** (rewrite step 6); the board
  runs the August image until then, and the new host tools refuse to
  configure it.
- **Commissioned in August on a loopback** (OUT1 cabled to IN1, no power
  stage): ~3.5 kHz bandwidth, on-target to 0.04 %, the flip sequence
  scope-captured. Record: [docs/lab_notes_2026-08-27.md](docs/lab_notes_2026-08-27.md).
  Bring-up of the MVP so far: [docs/lab_notes_2026-11.md](docs/lab_notes_2026-11.md).
- **Next hardware milestone:** the bring-up checklist,
  [docs/bringup.md](docs/bringup.md) — first at low current (a 3 A bench
  supply test version is planned), then the dummy-load campaign on the
  SE-600-5. Values marked PROVISIONAL in `host/config/channels.toml` get
  replaced by measurements then.

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

   You should see **111 passed** in about 2 minutes (Icarus Verilog must
   be installed for the FPGA simulations — see README "Setup A"). If you
   get `No module named coil_servo_host`, you're running a Python other
   than the activated `.venv` one.

3. Read, in this order:
   - [README.md](README.md) — how the three computers fit together, all
     setup steps, the safety model, and the "what's a knob vs. what's a
     rebuild" table.
   - [docs/mvp_architecture.md](docs/mvp_architecture.md) — the power
     stage as built, with the schematic, the measured parts, and the
     analysis of what OUT1 actually does (dead zone, freewheel turn-off).
   - [docs/servo_interface_brief.md](docs/servo_interface_brief.md) — the
     servo as a black box: every port, what it guarantees, the power
     budget, the open questions.
   - [docs/operating_scenarios.md](docs/operating_scenarios.md) — what
     happens in every situation an experimental cycle produces (ramps,
     turn-offs, field reversals, trips). Read this before writing
     sequences.
   - [docs/design.md](docs/design.md) — the control-loop arithmetic (the
     signal path is unchanged by the MVP; its output-stage sections
     describe the original full architecture).
   - [docs/register_map.md](docs/register_map.md) — every setting and
     status flag.
   - [docs/bringup.md](docs/bringup.md) — the hardware checklist.
   - [docs/future_upgrades.md](docs/future_upgrades.md) — designed but
     shelved options, including the parts of the original design the MVP
     left out (active clamp, boost rail, interlock chain …).

## The lab setup

- **Build machine:** the lab NUC, running Ubuntu under WSL2 with Vivado
  2025.1. Only needed to rebuild the FPGA bitstream
  (`make coil_servo.bit.bin` there). The Windows lab PC has no Vivado.
- **Boards:** reached over **wired Ethernet only, never WiFi**. The MOT
  board is at `192.168.0.65` (channel name `mot` in
  `host/config/channels.toml`).
- **Day-to-day tools** (from the activated `.venv`):
  - `python -m coil_servo_host.deploy mot` loads the bitstream (refuses if
    the servo is active) and checks the board came up safe;
  - `...check mot` is a quick health check;
  - `...watch mot` is the live status display;
  - `...config mot` prints the register values derived from
    channels.toml;
  - `...step` / `...sweep` measure the loop.
- **The bitstream does not survive a reboot** — after a power cycle,
  redeploy (the tools tell you so).
- **Starting the servo:** set `servo_enable` from the host first, *then*
  raise arm (DIO4). An arm line that was already high does nothing until
  it is dropped and raised again.

## How to review or extend the work

The verification is layered — each layer is checked against the one below:

1. `docs/mvp_architecture.md` and `docs/design.md` define the plant and the
   loop (review these with physics eyes).
2. `model/` implements them in trusted floating-point Python, including
   the MVP plant (pass bank with dead zone, 5 V compliance, freewheel
   turn-off, bridge-open fault path).
3. `model/coil_servo_model/fixed_point.py` is a bit-exact integer mirror.
4. The Verilog in `modules/` and `cores/` is compared against that mirror
   **bit-for-bit, every update cycle** by the testbenches in `sim/`; the
   integration bench closes the loop around the MVP plant and checks the
   safety rules on the pins every clock.
5. `host/` tools are tested against a mock server, without hardware.
6. The bench campaign confirms the built bitstream on a real board.

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
- Good first prompts: *"read mvp_architecture.md and explain the power
  stage to me"*, *"walk me through what happens during a field reversal"*,
  *"what happens if the heartbeat stops at 50 A?"*, or *"here are the
  dummy-load measurements — update channels.toml."*

## Safety ground rules (know them; the firmware enforces its part)

- **No sustained current without water flowing** through the cold plates.
  The first test round is uncooled: short pulses, reduced current, a
  written duty limit.
- The firmware trips at 100 A measured current, limits setpoint ramps,
  never flips the bridge at current, starts everything-off, and needs an
  arm edge to start. `servo_enable` off = graceful ramp-to-zero.
- The only automatic hardware trip is the heartbeat latch on the logic
  board (it powers up tripped). **Disarm before pressing its reset button**
  — after a heartbeat loss the firmware may be wound up.
- OUT2 and DIO2_P stay unconnected in the MVP; DIO5 (fault) is not wired.
- Never write or accept code that tries to clear a hardware fault.
- First power-stage runs: low current, dummy resistive load, per
  [docs/bringup.md](docs/bringup.md).

Questions with no written answer yet probably belong in the docs — add
them there (or have Claude do it) rather than answering only in chat.
