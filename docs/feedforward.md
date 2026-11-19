# Setpoint feedforward (register map rev 7)

## Why

The pass transistors turn on only above a threshold (the "dead zone",
OUT1 ≈ 3450 counts on the bench), and above it the current they pass per
OUT1 count grows with the current (bench: 3.6 mA per count at 0.5 A,
13 mA at 2 A). The controller (the PI loop: a proportional part plus an
integrator that accumulates the error) has to *find* the OUT1 value for a
new current by integrating. With gains low enough to be stable at the top
of the range, that is slow: on 11-18 the bench gains had to come down to
18.7 / 374 because of the coil–damper resonance, and a 0 → 2 A step then
took 1.41 ms to reach 98 % (0.62 ms with the old gains).

Feedforward removes most of that wait. The FPGA holds a table of "OUT1
command needed for this current", measured on the hardware. When the
setpoint changes, OUT1 jumps to the table value at once and the PI only
corrects the remaining few percent. The table does not change the loop's
stability: the feedback gains are the same, the table only sets where the
feedback starts from.

## What the firmware does

- 16 table points (CFG words 24–31), one every 2<sup>`ff_shift`</sup>
  setpoint counts; straight lines in between; the last point held above
  the table (bench: a point every 0.22 A up to 3.3 A; MOT: every 7 A up to
  105 A).
- The value for the slew-limited setpoint (the same setpoint the
  controller sees) is added to the PI output before the OUT1 clamp and
  floor; the clamp, the over-current trip and the zero-setpoint snap work
  exactly as before.
- Only in RUN, closed loop. Off in open loop and while the board brings the
  current to zero itself (flips, stops, trips).
- The integrator may now go negative (when the table asks for more than
  needed); it never takes the total below the floor.
- `ff_enable` = 0 gives exactly the rev-6 behaviour.
- STS flag `ff_on` shows it is acting.

## Setting it up

1. Warm the transistors (a 2 A hold for a few minutes on the bench).
2. `python -m coil_servo_host.ffcal mot_bench --write`: the servo holds 12
   currents up to `I_rated` (up and back down, about 10 s) and stores the
   OUT1 each needs as `ff_curve_amps` / `ff_curve_counts` in
   channels.toml. The difference between the up and down passes is the
   drift during the scan.
3. Set `ff_enable = 1`. `ff_margin_counts` (bench 10, provisional) takes a
   few counts off every point, so the table aims slightly low and the PI
   adds the rest; this avoids overshoot when the transistors are warmer
   than during the scan (warm needs 25–35 counts less, Q5).
   **Also slow the setpoint up-ramp** (`slew_up_amps_per_ms`): when OUT1
   jumps, the pass transistors overshoot by themselves (bench: +75-80 % at
   200 A/ms, +7-14 % at 5 A/ms with the table alone), then sink to a lower
   value over a few ms. Bench choice (11-19): `ff_margin_counts = 10`,
   5 A/ms.
4. `python -m coil_servo_host.config mot_bench` prints the table summary.

For the MOT, the curve is measured in Test 4 at 100 A with the cooling
water on (the table is only as good as the temperature it was measured
at).

## Bench tests (rev 7)

| Test | What | Compare with |
|---|---|---|
| FF0 | Deploy; `ff_enable` = 0: one step and one reversal | Test Log 2 (must be identical) |
| FF1 | `ffcal` warm, twice, a few minutes apart | each other (repeatability) |
| FF2 | Steps 0 → 0.5 / 1 / 1.5 / 2 A and 2 → 0 A, gains 18.7 / 374 | Log 2 steps (1.41 ms to 98 % at 2 A) |
| FF3 | One-command reversal ±2 A | Log 2 (1289 µs to 90 %) |
| FF4 | Holds, noise, 50 shots with feedforward on | Log 2 (must be unchanged) |
| FF5 | Overshoot after a 10 min warm hold, margin 0 and 10 | — |

### Bench results (11-18/19, Benchtop Test Log 2, section "Rev 7")

| | Without feedforward | With (margin 10, 5 A/ms) |
|---|---|---|
| 0 -> 2 A within 5 % | 1.29 ms | 0.39 ms |
| 0 -> 1 A within 5 % | 1.96 ms | 0.33 ms |
| Reversal ±2 A, 90 % / within 2 % | 1.30 / 1.8 ms | 0.55 / 0.96 ms |
| Holds 0.5-2 A, noise | — | unchanged |
| 50 shot sequences | 50/50 | 50/50, holds settled, scatter halved |

Aiming 40 counts low left 0.5-1 A steps 10-20 % short for milliseconds.
The cause of the transistor overshoot is not understood; check it at 100 A
(Test 4) before choosing the MOT up-ramp.

Simulation (sim/tb_coil_servo_top.py `feedforward_*`): on the model bench
plant, with a quarter of the model's gains and a table 30 counts low, a
0 → 2 A step reaches 98 % several times sooner with feedforward and ends on
the setpoint.
