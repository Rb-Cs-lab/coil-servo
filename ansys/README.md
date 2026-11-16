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
| `models/coil_servo_parts.lib` | One model per purchased part (text file, readable) |
| `build_project.py` | Rebuilds `coil_servo.aedt` from the parts table inside it |
| `run_sims.py` | Runs the test scenarios and saves the waveforms |
| `compare.py` | Plots model against bench and writes `results/comparison.html` |
| `results/comparison.html` | **Start here**: table and plots, model vs bench |

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

The schematic is drawn by a script, so it uses **net labels instead of
drawn wires**: every pin carries a small flag with a name (`rail`, `A`,
`B`, `ret`, `gate_bus`, …), and all pins with the same name are
connected. The parts are laid out in nine titled groups:

1. Supply and its leads
2. H-bridge (4 switch blocks)
3. Coil, damper, varistor, current sensor
4. Pass bank (6 leg blocks) and the TVS
5. Gate stage (OPA994)
6. Sensing chain (burden, AD8421, cable to IN1)
7. Red Pitaya (firmware stand-in)
8. Safety logic board
9. Test sources — what the firmware, the operator and faults do

Main net names: `sup_p` supply + terminal · `rail` rail bar · `A`, `B`
bridge outputs · `ret` return bar = pass-bank drain bar · `0` source bar =
supply − · `gate_bus` op-amp output · `pg1…6` / `ps1…6` each pass FET's
gate / source · `g1…4` bridge gates · `lem_m` LEM output · `in1`, `in2`,
`out1` the Red Pitaya's connectors · `dio0`, `dio1`, `dio6` polarity,
bridge enable, heartbeat · `dis` the drivers' DISABLE line.

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
| `L_coil`, `R_coil`, `R_damp1`, `R_damp2`, `C_damp` | Coil and damper (`R_damp1` = 1e6 Ω removes the damper) | bench values |

Useful signals: `V(i_meas)` = the current the firmware sees, in amps
(1 V = 1 A); `V(u_kcounts)` = OUT1 command in thousands of counts. The
true coil current is `(V(coil_m) − V(B)) / R_coil`.

To add a scenario, add a line to `SCENARIOS` in `run_sims.py`.

## Result so far (details in `results/comparison.html`)

Matches the bench: current for a fixed OUT1 (0.77 / 1.26 / 1.87 / 2.04 A
against 0.71 / 1.23 / 1.93 / 2.0 A), rail bar at 2 A (4.61 V / 4.60 V),
switch-off with the bridge closed (159 µs / ~170 µs), bridge opened at 2 A
(21 µs with damper / ~25 µs; 12 µs without / 10–13 µs), controller steps
(2 A: 0.48 / 0.69 ms to 90 / 98 % against 0.45–0.47 / 0.62–0.65 ms), the
trip, the 7.6 ms heartbeat time-out, and OUT1 winding up to its clamp
after a safety-board cut-off. A lost IN1 signal gives a 4.4 A surge with
the rail collapsing — consistent with the 5 A surge of 11-12.

Does **not** match:

- **The 10–20 kHz ringing without the damper does not appear.** The parts
  as modelled are stable at a fixed OUT1, so the cause is something the
  model leaves out.
- **Rail bar when the bridge opens:** model +25 V for under 1 µs, scope
  +2.5–3.2 V for ~10 µs. Until that is understood, do not trust the
  model's rail voltage for a bridge opening at 100 A.
- Smaller points: swing below zero after switch-off (−0.09 A against
  −0.2 A), drain bar with the bridge open (0 V against −0.5 V), no
  overshoot on steps.

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
- The schematic drawing itself has not been looked at on screen (it was
  built without a window); the connections were checked by the script, pin
  by pin, and by the simulations.
