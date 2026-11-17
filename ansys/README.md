# Ansys circuit model of the coil-servo power stage

This folder holds a circuit simulation of the power stage **as it is wired
on the bench now** (November 2026: 3 A bench supply, 45 µH test coil, RC
damper), made for Ansys Electronics Desktop, plus scripts that run the
bench tests in the simulator and compare the result with what we measured.

It answers one question: *is what we see on the scope what this circuit
should do?* It is a model of the **electronics**. The firmware is only
represented by a simple stand-in (see "What is and is not modelled").

| File | What it is |
|---|---|
| `coil_servo.aedt` | The Ansys project. Two circuit designs: **bench** (as built) and **mot** (the two MOT coils on the SE-600-5 — planned, not built) |
| `coil_servo_fast_turnoff.aedt` | A separate project: the same two circuits **plus the planned fast turn-off parts** (5KP30A surge protector and DSS2X101-015A blocking diode — not fitted yet). Designs **bench_ft** and **mot_ft**. See "The fast turn-off version" below |
| `models/coil_servo_parts.lib` | One model per purchased part (text file, readable) |
| `build_project.py` | Rebuilds the project files from the parts table inside it |
| `layout.py` | Where each part sits on the sheet and how the wires run |
| `preview_layout.py` | Draws `results/schematic_*.png`, a picture of the schematic, without Ansys |
| `run_sims.py` | Runs the test scenarios and saves the waveforms |
| `compare.py` | Plots model against bench and writes `results/comparison.html` |
| `results/comparison.html` | **Start here**: table and plots, model vs bench |
| `compare_fast_turnoff.py`, `results/fast_turnoff.html` | The fast turn-off version against the circuit as built (predictions only) |

## Opening the project

1. Start **Ansys Electronics Desktop Student 2025 R2** (installed on this
   PC under `C:\Program Files\ANSYS Inc\ANSYS Student\v252`).
2. File → Open → `C:\code\coil-servo\ansys\coil_servo.aedt`.
3. In the Project Manager (left), double-click the design **bench**.

**The project looks for the part models at
`C:\code\coil-servo\ansys\models\coil_servo_parts.lib`.** On a computer
where the repository sits somewhere else, run `build_project.py` there
once (below); it writes a fresh project with the right path.

### Reading the schematic

The schematic is drawn as a circuit diagram (a picture of it, made without
Ansys, is in `results/schematic_bench.png`):

- **Top:** the supply and its leads feed the **rail bar**.
- **Middle:** the H-bridge. Q1 and Q2 hang from the rail bar, Q3 and Q4
  stand on the return bar; between the two legs (node A on the left, node
  B on the right) sit the varistor, the damper and the coil with the LEM.
- **Below:** the return bar runs down to the pass bank's drain bar, with
  the six legs M1…M6 and the 5KP12CA hanging from it. The ground symbols
  under the legs are the source bar (= supply minus).
- **Bottom left to right:** sensing chain → IN1 of the Red Pitaya → OUT1 →
  50 Ω terminator → OPA994 with its two gain resistors → the gate bus to
  the six legs.
- **Left of the bridge:** the safety logic board. **Bottom row:** the test
  sources.

Blocks are drawn as boxes with the current path top to bottom (drain on
top, source at the bottom) and control pins on the sides.

A few connections use **net labels instead of wires** — a small flag with
a name; pins with the same name are connected. This is on purpose, where
wires would cross the power path: the three logic lines to the four bridge
drivers (`pol`, `npol`, `dis`), the 5 V logic supply (`v5`), the LEM
output (`lem_m`, which goes to the sensing chain at the bottom left), the
Red Pitaya's polarity input (`dio0`) and the test sources. The labels
`g1…4`, `pg1…6`, `ps1…6`, `u_kcounts`, `i_meas`, `hb_ok`, `latch_trip` are
measuring points only.

Main net names: `sup_p` supply + terminal · `rail` rail bar · `A`, `B`
bridge outputs · `ret` return bar = pass-bank drain bar · `0` source bar =
supply − · `gate_bus` op-amp output · `pg1…6` / `ps1…6` each pass FET's
gate / source · `g1…4` bridge gates · `lem_m` LEM output · `in1`, `in2`,
`out1` the Red Pitaya's connectors · `dio0`, `dio1`, `dio6` polarity,
bridge enable, heartbeat · `dis` the drivers' DISABLE line.

To move parts or re-route wires, edit `layout.py` (positions and wire
paths, in grid units) and run `build_project.py`; it refuses to save a
drawing in which any pin ends up on a different net than the parts table
in `build_project.py` says. Edits made by hand in the Ansys window are
lost at the next rebuild.

### Why some parts are grouped into blocks

**The Student licence allows at most 50 components per circuit design.**
The power stage has 82. So each bridge switch position (MOSFET + UCC5350
driver + RO-0515S supply + three gate resistors) is one block
`BRIDGE_SWITCH`, and each pass-bank leg (MOSFET + 24.9 Ω + 47 kΩ +
0.03 Ω ballast) is one block `PASS_LEG`. Every part is still its own line
inside the block — open `models/coil_servo_parts.lib`, section "CELLS".
With a full licence (the university may have one) the blocks could be
drawn flat.

## Running the simulations

From the repository root, in a terminal:

```bash
ansys\.venv\Scripts\python.exe ansys\run_sims.py --list
```

```bash
ansys\.venv\Scripts\python.exe ansys\run_sims.py
```

```bash
ansys\.venv\Scripts\python.exe ansys\compare.py
```

The second command starts Ansys in the background (no window), runs all
scenarios (about 4 s each) and saves the waveforms to `results\*.csv`.
The third makes the plots and `results\comparison.html`. Close the
project in the Ansys window first if you have it open — two programs
cannot hold the same project.

First-time setup on another computer (needs Python 3.10+ and Ansys
Electronics Desktop Student 2025 R2):

```bash
python -m venv ansys\.venv
```

```bash
ansys\.venv\Scripts\python.exe -m pip install pyaedt numpy matplotlib
```

```bash
ansys\.venv\Scripts\python.exe ansys\build_project.py
```

(`pyaedt` is Ansys's own Python library for driving Electronics Desktop.)

### Running by hand in the Ansys window

Each scenario is nothing more than a set of **design variables** (menu
Circuit → Design Properties). Set them, then right-click Analysis →
Analyze. To see curves: right-click Results → Create Standard Report →
Rectangular Plot, pick e.g. `V(i_meas)`.

| Variable | Meaning | Default |
|---|---|---|
| `ol_kcounts` | Fixed OUT1 command in thousands of counts (controller off). 0 = controller on | 0 |
| `sp_amps` | Setpoint, amps (controller on) | 2 |
| `t_sp_on`, `t_sp_len` | When the setpoint is applied, and for how long | 100 µs, 1 s |
| `t_run_off` | When OUT1 is forced to zero (stop, switch-off) | 1 s = never |
| `t_en_off` | When the bridge is opened (enable line drops) | never |
| `t_hb_off` | When the heartbeat wire is pulled | never |
| `t_in1_off` | When the IN1 cable loses contact | never |
| `kp`, `ki` | Controller gains: counts per amp, counts per (amp·second) | 31.2, 942e3 |
| `u_clamp`, `u_floor`, `trip_amps` | OUT1 limits (counts) and the over-current trip (amps) | 4100, 3450, 2.8 |
| `vt_pass` | Pass-transistor threshold: 3.892 cold, 3.868 warm | 3.892 |
| `V_opamp` | Gate amplifier supply (12.01 V now, 5.18 V before 11-13) | 12.01 V |
| `V_sup`, `I_lim`, `C_sup`, `R_lead`, `L_lead` | Supply and its leads | bench values |
| `R_reg` | How much the supply voltage sags per amp | 20 mΩ bench, 0.5 mΩ SE-600-5 (both ASSUMED) |
| `sp2_amps`, `t_sp2_on`, `slew2` | A second setpoint step added to the first (step down, or sign change for a reversal) | 0 = not used |
| `snap_amps` | Zero-setpoint snap threshold; −1 = snap off (firmware before rev 5) | 0.1 |
| `ol_step`, `t_ol_step`, `t_ol_ramp` | A step (or slow sweep) of the fixed OUT1 command | 0 = not used |
| `t_pol`, `en_gap`, `t_run_len` | Flip: when the polarity line changes, how long the bridge is off, how long OUT1 is held at zero | not used |
| `t_hb_len`, `t_rst_per` | Heartbeat wire re-plugged after this long; reset pressed again after this long | not used |
| `R_term` | Terminator at the op-amp input (1e9 Ω = not fitted) | 50 Ω |
| `cgd_pass` | Pass-transistor drain-to-gate capacitance | 1 nF |
| `L_coil`, `R_coil`, `R_damp1`, `R_damp2`, `C_damp` | Coil and damper (`R_damp1` = 1e6 Ω removes the damper) | bench values |

Useful signals: `V(i_meas)` = the current the firmware sees, in amps
(1 V = 1 A); `V(u_kcounts)` = OUT1 command in thousands of counts. The
true coil current is `(V(coil_m) − V(B)) / R_coil`.

To add a scenario, add a line to `SCENARIOS` in `run_sims.py`.

## Result so far (details in `results/comparison.html`)

All bench tests that a circuit model can do are simulated (45 runs); the
report has a table "which bench tests are simulated" that also lists the
ones that cannot be (noise, drift, heating, firmware and software
behaviour).

Matches the bench:

- safety-board light sequence, all six steps, and the 7.6 ms time-out;
- gate stage, with and without the terminator (within 0.02 V);
- current against OUT1, cold and warm (within ~8 %);
- controller steps at three gain settings and eight steps up and down over
  the range (traces overlay closely);
- switch-off with the bridge closed (159 µs / ~170 µs) and with the bridge
  opened (21 µs with damper / ~25 µs; 12 µs without / 10–13 µs);
- full reversal +2 → −2 A (0.96 ms / 0.95 ms; trimmed 0.81 ms / 0.81 ms);
- the trip, and OUT1 winding up to its clamp after a safety-board cut-off;
- a lost IN1 signal gives a 4.4 A surge with the rail collapsing —
  consistent with the 5 A surge of 11-12.

Does **not** match, or only partly:

- **The 10–20 kHz ringing.** With datasheet capacitances the model does
  not ring. With the pass transistors' drain-to-gate capacitance raised
  from 1 nF to 17 nF (design variable `cgd_pass`) it shows a resonance at
  16 / 13 / 11 kHz for 0.77 / 1.26 / 1.87 A — the bench rang at 20 / 15 /
  11.5 kHz — and the damper removes it. But in the model the ringing dies
  away within ~1 ms; on the bench it continued. A candidate explanation,
  not a confirmed one.
- **Closed loop without the damper:** the model's hold carries a
  40–50 kHz ripple of ±30–50 mA that the bench does not show.
- **Rail bar when the bridge opens:** model +25 V for under 1 µs, scope
  +2.5–3.2 V for ~10 µs. Do not trust the model's rail voltage for a
  bridge opening at 100 A.
- The measured small-step resonance moves with current (16 → 8 kHz); the
  model's does not.
- Smaller points: swing below zero after switch-off (−0.09 A against
  −0.2 A), drain bar with the bridge open (0 V against −0.5 V).

## The fast turn-off version (`coil_servo_fast_turnoff.aedt`)

A second project file, kept apart from the as-built one so the two can be
compared. It is the same circuit with the two parts planned in
[docs/future_upgrades.md](../docs/future_upgrades.md) section 6 added.
**Neither part is fitted yet: everything this file gives is a prediction.**

| Part | Name on the sheet | Where it is wired |
|---|---|---|
| IXYS DSS2X101-015A, a double Schottky diode (a diode with a low forward voltage), both halves in parallel | `D_block` | In the supply's + lead, just before the rail bar: lead → diode → rail bar. Supply current passes; coil current cannot flow back into the supply |
| Littelfuse 5KP30A, a surge protector (TVS diode: conducts only above about 35 V) | `D_clamp` | Between the rail bar (cathode, the marked end) and the return bar (anode) |

What they are for: when the bridge is opened while current flows, the coil
current goes through two bridge diodes onto the rail bar. Today it then
flows back into the supply at about 7 V, which is slow. With the diode
blocking that way, the rail bar rises until the 5KP30A conducts, so the
coil works against 35–48 V and the current falls several times faster.

Designs: **bench_ft** (bench supply, test coil) and **mot_ft** (SE-600-5,
both MOT coils). In mot_ft the damper is drawn with one resistor instead
of two, because the Student licence allows only 50 parts per design (the
proposed MOT damper has one resistor anyway).

Build and run:

    ansys\.venv\Scripts\python.exe ansys\build_project.py bench_ft mot_ft
    ansys\.venv\Scripts\python.exe ansys\run_sims.py ft_step_2A ft_turnoff ft_bridge_open ft_bridge_open_nodamper mot_ft_turnoff_100A mot_ft_bridge_open_100A mot_ft_bridge_open_100A_nodamper

(`build_project.py` rebuilds only the project files of the designs named;
`run_sims.py` with no names runs only the as-built scenarios.)

Then `ansys\.venv\Scripts\python.exe ansys\compare_fast_turnoff.py` writes
`results/fast_turnoff.html` (table and three plots). What the model
predicts (2026-11-17):

- **MOT pair, bridge opened from ~92 A:** current below 10 % after about
  30 µs, against 1.4 ms with today's switch-off (bridge kept closed). The
  bridge sees about 50 V.
- **The damper works against it.** With the proposed damper (1.6 Ω +
  22 µF) the damper capacitor charges to about 50 V and pushes about 12 A
  back through the coils in the wrong direction; the current is within 2 %
  only after 108 µs. With no damper the fall is clean (30 µs) but a fast
  ringing of about 1 A remains. The damper needs to be chosen again for
  this version. (The model has no losses at those frequencies, so real
  ringing will die sooner.)
- **Cost of the blocking diode:** 0.64 V at ~90 A (about 60 W of heat).
  100 A is still reached at the same OUT1 command; the voltage left across
  the pass bank at 100 A falls from 3.1 V to 2.5 V, and the rise to 90 A
  at a fixed command takes 0.56 ms instead of 0.46 ms.
- **The bench coil with its damper cannot show the effect:** at 2 A the
  damper takes the whole current at about 6 V and the 5KP30A never
  conducts. Without the damper the 2 A is gone in 2 µs, followed by a
  ±0.9 A ringing at about 170 kHz that the model does not damp.
- **The rail bar stays charged** (about 30 V, falling slowly) after the
  bridge has been opened, because the diode stops it emptying into the
  supply.
- With the bridge kept closed, nothing changes.

Numbers used: 5KP30A 35 V at 5 mA, 48.4 V at 103 A; DSS2X101-015A 0.67 V
at 100 A through both halves — both DATASHEET, typed from memory, to be
checked against the PDFs. Capacitances and leakage ASSUMED.

## Where each part is, and where its numbers come from

MEASURED = on our hardware. DATASHEET = typical datasheet value, **typed
from memory — check against the PDF before relying on it**. ASSUMED = a
guess. FITTED = fitted to our bench data.

| Purchased part | In the model | Numbers |
|---|---|---|
| Bench supply 5 V / 3 A | `PSU` (`DC_SUPPLY`) | 5.06 V MEASURED; 3 A limit; 100 µF output capacitance INFERRED from the surge trace |
| Supply leads | `R_lead`, `L_lead`, `R_lead_ac` | 0.2 Ω MEASURED; 3.6 µH FITTED; 20 Ω high-frequency loss ASSUMED |
| 4 × IXFN420N10T | inside `Q1…Q4` (`BRIDGE_SWITCH`) | 2.3 mΩ DATASHEET; threshold 3.52 V MEASURED; diode 0.5 V at 2 A (bench: 0.52 V); capacitances ASSUMED |
| 4 × UCC5350SB | inside `Q1…Q4` | logic as built; 65 ns delay, output resistance DATASHEET |
| 4 × RO-0515S (+ 1 µF ‖ 10 µF) | inside `Q1…Q4` | 16 V MEASURED; output resistance ASSUMED |
| Gate resistors 10 Ω / 4.7 Ω / 10 kΩ | inside `Q1…Q4` | as built |
| Test coil, 12 turns | `L_coil`, `R_coil` | **37 µH from the 11-12 switch-off trace** (the repo's calculated value is 45 µH); 25 mΩ calculated |
| Damper 1 Ω + 2.2 Ω + 10 µF | `R_damp1`, `R_damp2`, `C_damp` | as fitted 11-12 |
| Varistor S20K20 | `RV1` | 33 V at 1 mA, 80 V at 100 A DATASHEET; capacitance ASSUMED |
| LEM LF 310-S | `LEM` | 2000:1; 0.8 µs lag ASSUMED |
| Burden 10 Ω | `R_burden` | as built |
| AD8421, G = 1, inputs swapped as built | `U_ina` | 10 MHz DATASHEET |
| 49.9 Ω + cable to IN1 | `R_ser`, `C_cable` | 150 pF ASSUMED |
| 6 × IXTN200N10L2 | inside `M1…M6` (`PASS_LEG`) | current law FITTED to the 11-11/12 scans (valid 0.05–2.5 A); per-FET thresholds MEASURED; capacitances DATASHEET/ASSUMED, constant |
| 6 × 24.9 Ω, 47 kΩ, MP930 0.03 Ω | inside `M1…M6` | as built |
| 5KP12CA | `D_tvs` | 13.6 V at 1 mA, 20 V at 250 A DATASHEET |
| OPA994, 6.98 kΩ / 1 kΩ, 50 Ω terminator | `U_opa`, `R_f`, `R_g`, `R_term` | gain 7.98 (measured 7.93); 24 MHz, 125 mA limit DATASHEET; rail 12.01 V MEASURED |
| Safety logic board | `U_logic` (`LOGIC_BOARD`) | logic as built; 7.6 ms MEASURED |
| Red Pitaya | `U_rp` (`RP_SERVO`) | scales MEASURED (224 A at 1.12 V; 142.6 µV per count; −41.3 mV at zero) |

Not in the model: the 4/0 cable and bus bars inside the bridge (taken as
ideal), the bridge jumpers, the LEM's ±15 V supply, heat, noise, offsets,
and anything to do with grounding between the Red Pitaya and the supply.

## What is and is not modelled

- **The firmware is a stand-in.** `RP_SERVO` is a smooth-in-time
  controller with the same gains, limits, zero-setpoint snap, trip and
  3.6 µs delay. It has **no flip sequence, no ramp limiter, no dither, no
  1 µs sampling**. The real firmware is tested in `sim/` (cocotb); do not
  use this model to check firmware behaviour. Flips are not simulated yet.
- **The pass-transistor law is only checked up to 2.5 A.** The `mot`
  design uses it at 100 A — a 40× extrapolation. Treat every `mot` number
  as a rough prediction.
- The schematic was built without opening the Ansys window. The connections were
  checked by the script, pin by pin, and by the simulations; how it looks was
  checked only on the preview picture, not in Ansys itself.
