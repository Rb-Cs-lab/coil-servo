# Bring-up test campaign — who does what

The plan for testing the MVP coil driver from here to the 100 A build, run
as a team: **you** do the hardware (wiring, probes, power switches, jumpers,
passwords, anything physical), **Claude** runs the commands on the lab PC —
configures the Red Pitaya, drives OUT1, records with the Red Pitaya and the
test oscilloscope, analyses the data, updates `host/config/channels.toml`
and writes the results into `docs/lab_notes_2026-11.md`.

Each test below lists: the hardware setup (you), the scope channels, what
Claude runs, and the pass criteria. Detailed background for each step is in
[bringup.md](bringup.md) and [bench_supply_test.md](bench_supply_test.md).

*Written 2026-11-11. Firmware: register map rev 4, commit 103a127.*

## How a step works

1. Claude says **"HARDWARE: …"** — what to connect, switch or check. You do
   it and answer "done" (or what you saw).
2. Claude says **"RUNNING: …"**, runs it, and shows the result (numbers,
   plots, pass/fail).
3. Anything physical that Claude cannot do or see — an LED, a smell, a hot
   part, a supply's front panel — you report.

Words used below: **open loop** = OUT1 set directly by Claude, no feedback;
**closed loop** = the servo regulating the current; **arm edge** = unplugging
and re-plugging the arm jumper (E1 pin 11 → pin 1); **DUT** = the circuit
under test.

## Safety rules for every powered test

- **Your hand is on the supply's output switch.** Switching the supply off
  is always safe: the current dies in the coil's own freewheel loop.
- **STOP** in the dashboard (or Claude's stop command) = graceful ramp to
  zero. It is always allowed and never refused.
- **Before pressing the logic board's latch reset:** the servo must be
  stopped (IDLE) and the green LED on. Claude confirms IDLE first.
- **Scope ground clips all go to ONE point: supply 0 V (the pass-bank
  source bar).** Never clip a probe ground onto the rail, bridge node A/B or
  the drain bar: through the scope's mains earth that would short the node.
  The bench supply's output must be floating (not earthed on its + side).
- No sustained current without water (bench run at ≤ 3 A is fine: ~15 W in
  the whole pass bank).
- If anything smells, clicks or gets hot: supply off first, talk after.

## One-time setup

| Item | Who | Status |
|---|---|---|
| Rev-4 firmware on the MOT board | — | ✓ 2026-11-11 |
| **ssh key on the board** so Claude can deploy (after any Red Pitaya power cycle the firmware must be reloaded). Run once, type the board password once: `type $env:USERPROFILE\.ssh\id_ed25519.pub \| ssh root@192.168.0.65 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"` | you | open |
| **Restart the dashboard** (Ctrl+C, then `.venv\Scripts\python.exe -m coil_servo_host.dashboard`, open http://localhost:8081) — shows firmware ID, zero offsets, zero calibration | you | open |
| Test scope (Rigol DHO914, `test` = 192.168.0.190) on the lab network, reachable by `scope-daq` (`C:\code\scope-daq`) | — | ✓ checked |
| **4 × ×10 probes** on the test scope; probe attenuation set to ×10 on the scope's channel menu | you | — |
| Jumper wires for **arm** (pin 11 → pin 1), **flip request** (pin 9 → pin 1, a brief touch) and the **heartbeat** wire (pin 15) within reach | you | — |

## Test 1 — Gate stage (no high current)

*Checks that OUT1 reaches the FET gates correctly: termination, op-amp
gain, op-amp supply headroom, every gate following.*

**You:** high-current supply **off**; op-amp board powered — **tell Claude
its supply voltage**; OUT1 (SMA) → op-amp BNC input; logic board connected;
arm jumper out.

**Scope:** CH1 = op-amp input (OUT1 at the BNC), CH2 = op-amp output,
CH3 = gate of pass FET #1, CH4 = gate of pass FET #10 (the other end of the
bank). All grounds on supply 0 V.

**Claude runs:** sets open loop, asks for the arm edge, then steps OUT1
through 0 → 4474 counts (the clamp) in ~10 points; at each point records
the four DC voltages with the scope (`scopedaq.measlog`) and the Red
Pitaya's command; then fits.

**Pass:** OUT1 at the op-amp input = 1× (terminated) or 2× (unterminated)
the DAC value — sets `out1_terminated`; op-amp gain 7.98 ± 2 %; no clipping
below the clamp (else the op-amp supply is too low); both gates equal the
op-amp output within a few mV; no oscillation (CH3/CH4 captured at fast
timebase). Finally **you** switch the op-amp supply off and Claude captures
the gates decaying through the 47 kΩ pull-downs (τ ≈ 1 ms).

## Test 2 — Sensor zero calibration

*Every new LEM / sensor chain.*

**You:** LEM ±15 V on (warmed up ~10 min), its output into the AD8421 →
IN1 as built; **no current anywhere** (bridge supply off); control
computer's analog output at 0 V or disconnected from IN2.

**Claude runs:** `python -m coil_servo_host.zero mot --write --apply`
(also for `mot_bench`), then `check`.

**Pass:** `check` reads ~0 A afterwards. An IN1 offset bigger than a few
hundred mA means the sensor needs attention (the 11-11 LEM read −5.8 A:
demagnetise or replace it, then repeat).

## Test 3 — Bench supply run (≤ 3 A, one coil) — profile `mot_bench`

**You:** bench supply **5.0 V, current limit 3.0 A**, output off, in place
of the SE-600-5; **one coil** of the pair on 4/0, LEM on that lead (one
pass); H-bridge and pass bank wired as built; logic board connected, latch
reset (green on, red off); arm jumper out.

**Scope:** CH1 = IN1 (the AD8421 output, via a BNC tee — the Red Pitaya
stays connected), CH2 = pass-bank **drain bar** (= V_DS of the bank),
CH3 = bridge **node A**, CH4 = bridge enable (E1 pin 5) or polarity (pin 3)
during flips. All grounds on supply 0 V.

| Step | What | You | Claude runs | Pass / result |
|---|---|---|---|---|
| 3a | Load the bench profile | supply output **on** | `check mot_bench` | banner shown, Red Pitaya LED 7 on, read-back OK |
| 3b | Zero calibration of the real chain | — | `zero mot_bench --write --apply` | as Test 2 |
| 3c | **Dead zone and G_pass** (open loop) | arm edge when asked | steps OUT1 with `step mot_bench --open-loop --counts N` across the turn-on region; Red Pitaya + scope record the current | current vs OUT1 fit → dead zone and G_pass written into `[mot_bench]` (`u_floor_counts`, `out_clamp_counts`, `g_fs`/`V_th`) |
| 3d | Closed-loop steps 0.5 / 1 / 2 / 2.5 A | arm edge | `step mot_bench --amps …`; scope captures each step | settles inside ~1 ms after the ramp, no overshoot; PI gains refined from the measured response |
| 3e | **Over-current trip** | — | temporarily `I_rated = 2.9`, asks for 2.9 A | trips at 2.8 A (measured), current falls, bridge opens, TRIPPED; acknowledge |
| 3f | **Turn-off (freewheel) capture** — the decisive scope shot | — | from 2 A, setpoint → 0; scope triggers on CH1 falling | drain bar (CH2) sits at ~rail + 0.7 V during the decay (freewheel, the decided model) — **not** at ~13 V (that would be the TVS); decay time → body-diode drop V_f |
| 3g | **Flips** | touch pin 9 → 3V3 briefly when asked | runs at 1 A; scope CH1 current, CH4 bridge enable, then polarity | bridge opens only inside the ±0.5 A window; polarity changes only with enable low, dead time visible; current returns with the other sign |
| 3h | **Heartbeat trip at current** | pull pin 15 when asked; later reset the latch | at 1–2 A; scope CH1 and the rail | current dies in < 1 ms, latch red; Claude stops the servo (IDLE), then you reset with green on |
| 3i | Hold noise, dither on vs off | — | 2 A hold, Red Pitaya decimated capture + scope CH1 high-resolution | noise vs the 0.1 % spec; decides future_upgrades §7 |
| 3j | Short duty test | read plate temperature by hand/IR | 2.5 A for a few minutes | plate barely warm (≈ 15 W total) |

**Results (11-11/12):** [lab_notes_2026-11.md](lab_notes_2026-11.md) and the
benchtop test log (Tests 0–3: pre-checks, gate stage, sensor chain, bench supply) with all plots, [benchtop_test_log.md](benchtop_test_log.md).
3j moved to Test 4 (≤ 15 W on the bench).

## Test 4 — 100 A build (later, with water)

Kelvin + LCR on both coils and the 4/0 cabling; water cooling on; SE-600-5;
both coils in series; `mot` profile; zero calibration again; then 3c–3i at
increasing current (10 → 30 → 60 → 100 A) with a written duty limit until
the water is proven. 4 AWG bridge jumpers: check their temperature at
100 A CW.

## Where the data goes

- Red Pitaya captures and step/sweep CSVs: the coil-servo folder (dashboard
  captures under `logs/captures/`).
- Scope records: `C:\code\scope-daq\data\scope\<date>_<time>_<label>\`
  (`.npz` per record + `settings.json`); Claude labels each run with the
  test number, e.g. `t3f-freewheel-2A`.
- Results, plots and any changed settings: `docs/lab_notes_2026-11.md`,
  committed with the data summary.
