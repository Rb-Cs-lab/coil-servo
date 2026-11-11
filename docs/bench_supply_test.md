# Bench-supply test: the MOT channel at ≤ 3 A

A first hardware campaign for the MOT channel's MVP power stage
([mvp_architecture.md](mvp_architecture.md)) with a **current-limited
bench supply (5 V, 3 A limit) in place of the Mean Well SE-600-5**,
driving **one coil** of the MOT pair. The point is to verify the whole
circuit and measure what the model only guesses — pass-bank dead zone and
gain, IN1 offset, turn-off path, flips, both trips, loop gains — at a
current where no mistake can hurt anything. Everything learned here is
then re-checked on the SE-600-5.

*Written 2026-11-10, revised 2026-11-11 for the setup as built (one LEM
pass, one coil). Needs the MVP bitstream (register-map revision 3, rewrite
step 6): the host tools refuse to configure the board's current
2026-08-27 bitstream. Do this campaign **after step 6**, and after
[bringup.md](bringup.md) sections 0–4 (network, deploy, logic-board LED
test, gate stage incl. the OUT1 termination check).*

## Switching between bench and 100 A

In software the **only** switch is the channel name: **`mot_bench`** for
the bench, **`mot`** for the 100 A build, given to every host tool
(`deploy`, `check`, `watch`, `step`, `sweep`, `config`). Nothing in
channels.toml has to be edited to switch. With `mot_bench` every tool
prints a `BENCH PROFILE … NEVER use with the SE-600-5` banner, and once
the settings are loaded **board LED 7 is lit** (all LEDs off = production
settings). `check` and `watch` also say which settings the board actually
holds and warn if they don't match the name you typed.

> | Bench → 100 A | 100 A → bench |
> |---|---|
> | ☐ Bench supply output off, **disconnected** | ☐ SE-600-5 off, **disconnected** (not just switched off) |
> | ☐ SE-600-5 reconnected (+ rail bar, − bank side) | ☐ Bench supply connected: CV 5.0 V, CC 3.0 A (section 4) |
> | ☐ Coil **pair** reconnected (both coils, series-opposed) | ☐ **One** coil between bridge nodes A and B, varistor across it |
> | ☐ In-amp R_G jumper **removed** (if it was fitted) | ☐ R_G jumper fitted *only* if `G_inamp = 10` in `[mot_bench]` |
> | ☐ Water cooling on (or the uncooled duty limit, bringup.md 6.2) | ☐ No water needed (≤ 15 W total) |
> | ☐ Software: use **`mot`** — `deploy mot`, `check mot` | ☐ Software: use **`mot_bench`** — `deploy mot_bench`, `check mot_bench` |
> | ☐ `check mot`: no banner, I_FS = 200 A, LED 7 **off**, "board holds the 'mot' settings" | ☐ `check mot_bench`: banner, LED 7 **on**, "board holds the 'mot_bench' settings" |

## Words used here

- **CV / CC.** A bench supply is a *constant-voltage* (CV) source until
  the load asks for more than its current limit; then it switches to
  *constant current* (CC): the current stays at the limit and the voltage
  drops to whatever the load allows. Most supplies show a "CC" lamp then.
- **Channel / profile name.** Every host tool takes a name that selects a
  section of [host/config/channels.toml](../host/config/channels.toml).
  `mot_bench` is the MOT board with bench settings; `bench = true` in that
  section is what makes the tools print the banner and light LED 7.
- **I_FS (sensor full scale).** The coil current that gives +1 V on IN1.
  One **ADC count** (the smallest step the board can measure) is
  I_FS/8192. With the LEM as built, I_FS = 200 A and one count is
  **24.4 mA**, so 3 A is only about 123 counts.
- **Open loop / closed loop.** Open loop: OUT1 is set to a fixed command
  (in DAC counts) and we watch what current flows. Closed loop: the servo
  adjusts OUT1 to hold the current at a setpoint.
- **Dead zone, G_pass.** The pass bank conducts nothing until OUT1 is
  above ≈ 0.44 V (the FET gate threshold divided by the op-amp gain of
  ≈ 8): that is the dead zone. Above it, the current rises by **G_pass**
  amps per volt of OUT1.
- **Clamp, floor.** The firmware never commands OUT1 above the *clamp*.
  While running, it never commands OUT1 below the *floor*, which sits just
  under the dead zone so the servo does not waste time crossing it.
- **Firmware trip.** If the measured current exceeds `trip_amps`, the FPGA
  sets OUT1 to zero, lets the coil current die away, opens the bridge, and
  latches (state TRIPPED) until acknowledged.
- **Heartbeat latch.** The logic board opens the bridge in hardware if the
  Red Pitaya's heartbeat (DIO6) stops; reset is a button.
- **Freewheel.** When the bank throttles back, the coil current keeps
  circulating through the bridge and a FET body diode and dies away in
  tens of µs (at 3 A) — the decided turn-off path.
- **Dither.** The firmware toggles OUT1 by ±1 count very fast so that its
  average has finer steps than one DAC count.

## 1. What is different from the production settings

Print the derived values any time with
`python -m coil_servo_host.config mot_bench`. The `[mot_bench]` section,
with reasons (values marked PROVISIONAL in the file are guesses until
measured):

| Setting | `mot` (SE-600-5) | `mot_bench` | Why |
|---|---|---|---|
| Supply | SE-600-5, 5 V / 100 A | bench, **CV 5.0 V, CC 3.0 A** (`I_supply_max`) | the CC limit is the hard current limit of the campaign |
| Coil | the pair, 16 µH | **one coil, ~8–10 µH** (PROVISIONAL, model 9 µH, 3.2 mΩ) | not calculated: half the pair plus the mutual term. At ≤ 3 A it only sets ramp and freewheel times of a few tens of µs — no effect on the test plan |
| Sensing | LEM, one pass, I_FS 200 A | **same** (24.4 mA per count) | optional: in-amp gain 10 (below) |
| Rated (`I_rated`) | 100 A | **2.5 A** | highest closed-loop setpoint; the `step` tool refuses more |
| Firmware trip | 100 A | **2.8 A** (115 counts) | above every allowed setpoint, 0.2 A (~8 counts) below the 3.0 A supply limit, so it fires before the supply goes into CC and can be tested (4.4) — **provided the IN1 offset is not more negative than about −0.15 A** (4.1) |
| OUT1 clamp | derived: 4474 counts | **set directly: 4000 counts** (decided 2026-11-11) | at 2.5 A the derived clamp sits only ~44 counts above the dead zone — inside its uncertainty — and could block the first measurement. 4000 counts (0.49 V at OUT1, ~3.9 V gate) leaves ~0.4 V of gate headroom; the supply limit, not the clamp, bounds the current |
| Gains | kp 0.1044, ki 3149 s⁻¹ | **same**, written as 4.28 counts/A and 129 counts/(A·ms) | the per-amp form does not depend on I_FS, so the in-amp switch below needs no gain edit |
| Ramp rates | 200 / 50 A/ms | **200 / 50 A/ms** | unchanged on purpose: the physical limits are rail/L and V_f/L, not the current |
| Flip zero window | 0.5 A | **0.5 A** (PROVISIONAL) | the IN1 offset is ~0.29 A at this scale and must be calibrated first; 0.5 A is 20 % of rated on the bench |
| Trip timeout | 3 ms | 3 ms | the freewheel from 2.5 A takes only ~30–40 µs; the timeout is a safety net |
| Dither | on | on | one OUT1 count is ~0.12 A (5 % of 2.5 A) |
| Board LED 7 | off | **on** | physical sign that bench settings are loaded |

**Never run `mot_bench` on the SE-600-5.** Its clamp asks the bank for far
more than 3 A; the bench supply's limit is what makes that harmless (the
firmware trip at 2.8 A would still act, but that is not the plan).

### Optional: in-amp gain 10 for finer resolution

At 24.4 mA per count the board's view of a 2.5 A current is coarse (one
count ≈ 1 %). If that limits a measurement, the AD8421 in-amp gain can be
raised to 10: **R_G ≈ 1.1 kΩ** (G = 1 + 9.9 kΩ / R_G), giving I_FS = 20 A
and **2.44 mA per count**. Fit it on a **2-pin jumper/header** so it is
visible and removable, then change the **one line** `G_inamp = 10.0` in
`[mot_bench]`. Everything else in the section is in amps (trip, zero
window, rated, ramps) or OUT1 counts (clamp, floor, gains per amp), and
the register values are derived, so nothing else needs editing. (Once the
offset is measured small, the zero window may optionally be tightened,
e.g. `zero_win_amps = 0.1`.) The bench banner shows the G_inamp in use.

**Remove the jumper before any 100 A run.** Forgetting it fails safe:
`mot` assumes G = 1, so it reads the current 10× too high — the servo
delivers 1/10 of the setpoint and trips at 1/10 of the real current. The
other mistake (`G_inamp = 10` in the file but no jumper) reads 10× too low
on the bench; the 3 A supply limit then protects, and the ammeter check in
4.1 catches it. Not planned for the first round.

**IN2 (analog setpoint) scaling** follows I_FS: through the ÷10 divider it
is 20 A per computer volt at G = 1 (as in production) and 2 A per volt at
G = 10. The host tools below use the setpoint register instead.

## 2. Equipment

- A bench supply with **≥ 5 V and ≥ 3 A**, an adjustable current limit,
  a floating (not earthed) output, and a current display. Remote sense
  off. Its ammeter is a free, independent check of the board's current
  reading (section 4.1).
- Oscilloscope, 4 channels (IN1 or a current probe, pass-bank drain bar,
  bridge node A, and DIO0/DIO1 or DIO6).
- A thermocouple or IR thermometer for the FET plates (precaution only).

## 3. Wiring

1. **Supply.** With everything off, disconnect the SE-600-5 outputs and
   connect the bench supply in their place: **+ to the rail bar**,
   **− to where the SE-600-5's − went** (the pass-bank side of the
   high-current loop). Short, twisted leads. Leave the SE-600-5
   disconnected (not just switched off).
2. **One coil.** Connect a single coil of the MOT pair between bridge
   nodes A and B with the 4/0 leads. Its **varistor stays across it**
   (it protects against an open lead, as on the pair). Its inductance is
   not calculated — ~8–10 µH is a PROVISIONAL estimate — and at ≤ 3 A it
   only sets ramp and freewheel times of a few tens of µs, so it does not
   change anything below. At ≤ 3 A the coil stores < 50 µJ and dissipates
   < 0.03 W.
3. **LEM.** The LEM LF 310-S sits on one 4/0 lead of the coil, **one
   pass** — either lead works (the same current flows in both); keep the
   direction that reads positive current as positive (check in 4.1).
   `lem_turns = 1` in the file.
4. **No water needed.** At 5 V and 3 A the whole supply delivers at most
   15 W, almost all of it in the pass bank: ≈ **2.5 W per FET** (vs ≈ 50 W
   each at 100 A). Continuous operation without cooling water is fine;
   glance at the plates now and then.

## 4. Supply settings and first power-on

1. Supply output **off**. Set the **current limit to 3.0 A** (most
   supplies: short the output leads together, turn the output on, adjust
   the limit while reading the display, turn it off again). Then set the
   **voltage to 5.0 V** with the leads open. If it has an over-voltage
   limit, set ~6 V.
2. Board: deploy (once per board power-up) and check, with the bench name:

   ```bash
   python -m coil_servo_host.deploy mot_bench --bitstream coil_servo.bit.bin
   python -m coil_servo_host.check mot_bench
   ```

   Expect the bench banner, `supply limit = 3 A`, heartbeat advancing,
   state IDLE, "after this check: board holds the 'mot_bench' settings",
   and **LED 7 lit**.
3. Logic board: press the latch reset (the red LED goes off) with arm
   (DIO4) **low**. Turn the supply output **on**. Nothing should flow: the
   supply shows ~0 A, CV mode.
4. In a second terminal, leave this running for the whole campaign (it
   also re-checks every few seconds which settings the board holds):

   ```bash
   python -m coil_servo_host.watch mot_bench
   ```

### 4.1 IN1 offset — and its sign

1. With the supply on and the servo disabled, read the measured current
   (`check mot_bench`, or `watch`). That reading at zero current is the
   **IN1 offset**. Expected: about +0.29 A (the ~1.4 mV seen in Nov 2026,
   ≈ 12 counts). **Write it down, with its sign.**
   - It must be well below the 0.5 A zero window (flips and trips decide
     "current is zero" with it). Calibrating it out (AOUT0 trim or a host
     constant) is still an open item.
   - **Sign matters for the trip test.** The trip compares the *measured*
     current with 2.8 A. A positive offset (reads high) makes it act early
     — at ~2.5 A real with +0.29 A — which is fine. A **negative** offset
     larger than about 0.15 A would push the real trip point above the
     3.0 A supply limit: the supply would hold the current and the trip
     would never fire. In that case lower `trip_amps` by the offset before
     section 4.4.
2. With the first current in 4.2, compare the board's reading (minus the
   offset) with the **supply's ammeter**. They should agree to within a
   couple of counts. A factor near 10 means `G_inamp` in the file does not
   match the board as wired (R_G jumper) — stop and fix that first.

### 4.2 Dead zone and G_pass (open loop) — the first real measurement

The model's g_fs is least reliable here: at ≤ 3 A each FET carries
≤ 0.5 A, just above threshold, where the transconductance is lower than
the 50 A/V assumed and the six FETs do not turn on together (their
thresholds differ by 60 mV ≈ 60 OUT1 counts). So scan OUT1 in DAC counts
directly instead of trusting the model's amps:

```bash
python -m coil_servo_host.step mot_bench --open-loop --counts 3500 -o ol_3500.csv
```

The tool enables the servo, asks you to raise arm (DIO4), applies the
command for ~17 ms while capturing, then ramps down and disables. Repeat
with increasing `--counts`, e.g. 3500, 3550, then in steps of 2–5 counts
once current starts to flow (nominally ~3593; the clamp stops you at
4000). For each run note the steady current (the end of the CSV's
`i_amps` column, or — better resolved — the supply's ammeter).

- The command where current first flows is the **dead zone** (in counts;
  OUT1 volts = counts / 8192, terminated).
- The slope above it is the bank's gain at this current, **s amps per
  OUT1 count** (the model says ~0.12 A/count; expect less near
  threshold).
- Going past ~2.8 A **trips the firmware** (state TRIPPED in `watch`) —
  that is the trip working. In open loop the current rises fast (~0.5 A/µs
  on 5 V into one coil), so a command asking for well over 3 A can touch
  the supply limit for a few µs before the trip (4 samples of ~1 µs) acts;
  a brief flicker of the CC lamp is harmless. To continue:
  `python -c "from coil_servo_host import Board, load_channel; b = Board(load_channel('mot_bench')['host']); b.pulse('trip_ack')"`,
  then drop and raise arm on the next run.

Then in `[mot_bench]`:

- set `kp_counts_per_amp = 0.5 / s` and `ki_counts_per_amp_ms = 15.08 / s`
  from your measured s around 1–2 A (the commissioned loop scaled to the
  measured bank — what `coil_servo_model.analysis.suggest_gains` does);
- optionally set `u_floor_counts` = (measured dead zone − 50 counts).
  With the default floor (90 % of the dead zone) a step from zero to 2 A
  waits ~1.4 ms before current flows, because the servo must first cross
  the gap; 50 counts below the onset (~50 mV of gate, ~10 K of threshold
  drift) brings 2 A steps from zero to under 1 ms. If current then flows
  with the setpoint at zero, the floor is too high — lower it.

### 4.3 Closed-loop steps and hold noise

```bash
python -m coil_servo_host.step mot_bench --amps 2 -o step_2A.csv
```

(`step` always steps from zero.) Plot it as in
[bringup.md](bringup.md#looking-at-the-data). Look for: no overshoot, no
ringing, settled within ~1–2 ms (the model: ~1.9 ms from zero with the
90 % floor, ~0.7 ms with the floor at onset − 50), final value **within
±1–2 ADC counts** (±25–50 mA) of 2 A. That band is the measurement's
resolution, not the 0.1 % spec: 0.1 % of 2.5 A is a tenth of one count,
which this board cannot see at I_FS = 200 A. Repeat at 1 A and 2.5 A.
Below ~1 A only one or two FETs conduct, so the loop is slower there:
expected.

Hold noise: repeat the 2 A step with `dither_enable = 0` and compare (a
scope on IN1 or a current probe shows more than the 24 mA ADC steps).
The model predicts up to ~50 mA peak-to-peak hunting without the dither
at some currents (one OUT1 count is ~0.12 A) and none with it. Set
`dither_enable = 1` again afterwards.

### 4.4 Firmware over-current trip

Test the real 2.8 A level in closed loop (procedure decided 2026-11-11).
The `step` tool refuses targets above `I_rated`, so temporarily set
`I_rated = 2.9` in `[mot_bench]` (the loader still requires it below the
3.0 A supply limit), then:

```bash
python -m coil_servo_host.step mot_bench --amps 2.9 -o trip.csv
```

The current must rise, stop at the trip level (≈ 2.8 A measured; the real
current differs by the IN1 offset, 4.1), freewheel to zero, the bridge
must open (amber LED off), and `watch` must show TRIPPED. The supply's CC
lamp must **not** come on (the integration simulation peaks below
2.95 A). Acknowledge as in 4.2. **Restore `I_rated = 2.5`.**

The loader refuses a `trip_amps` at or above `I_supply_max` for exactly
this reason: if the supply limited the current first, the trip would
never fire, and the servo would wind its integrator up against the clamp
(the model shows a several-ms recovery afterwards).

### 4.5 Turn-off path: the freewheel scope capture

Hold 2.5 A (closed loop, e.g. `step mot_bench --amps 2.5` — the tool
returns the setpoint to zero at the end, which is a snap-off). Scope the
pass-bank **drain bar**, bridge **node A** and IN1, triggered on the fall.

- **Freewheel (expected):** the drain bar rises to ≈ V_rail + ~0.6 V and
  the current falls almost linearly to zero in ~30–40 µs (rate V_f / L,
  roughly 60–90 A/ms for one coil). The slope gives the body-diode drop
  at low current, V_f = L × dI/dt — with L itself uncertain, record the
  slope and the drain-bar voltage.
- **TVS instead:** drain bar at ~13 V and a fall of only a few µs. Report
  it — the model has a flag for that case (mvp_architecture.md 3.3).

The *path* carries over to 100 A; the timing does not (the pair's
inductance, V_f and the I·R term at 100 A: ~1.3 ms).

### 4.6 Flips

Hold ~2 A in closed loop (setpoint register or IN2), then pulse DIO3
(flip request). Scope DIO0 (polarity), DIO1 (enable) and the current:
enable drops only after the current is inside the 0.5 A window, polarity
toggles inside the enable-low window with the dead time visible on both
sides. After the flip the *setpoint sign* must follow the polarity —
otherwise `watch` shows `sp_sign_mismatch` and the servo sits at zero,
which is safe. If flips end in TIMEOUT_HOLD, the IN1 offset is too close
to the zero window (4.1). Also scope V_GS of the bridge FETs during a flip
to confirm `deadtime_us`: the FSM's enable-low window is the only
shoot-through protection (mvp_architecture.md section 4).

### 4.7 Heartbeat-latch trip at current

Hold 2.5 A, then pull the DIO6 wire. The logic board opens the bridge at
current; the coil's ½·L·I² ≈ **30 µJ** (40 µJ at 3 A, with L ≈ 9 µH) goes
back into the bench supply. A bench supply cannot sink current, so this
charges its output capacitor — a bump of millivolts for typical
capacitors; some supplies show a brief error or trip their over-voltage
protection. Scope the rail. At this energy nothing can be harmed; on the
SE-600-5 the same event returns ~0.08 J (100 A, the pair), which is the
open question this test previews (mvp_architecture.md 3.3).

Recovery — the operating rule: **disarm (DIO4 low) first**, then
`servo_enable = 0`, re-plug DIO6, press the latch reset, and only then
enable and arm again.

### 4.8 Optional: transfer function

`python -m coil_servo_host.sweep mot_bench` works as in
[bringup.md](bringup.md) section 3c, but the useful OUT1 range on the
bench is tiny: the generator offset must sit just above the dead zone
(~4.4 V before the ÷10 divider) and the amplitude must stay within a few
tens of mV at the generator (the whole 0–3 A range is ~25 OUT1 counts).

## 5. What to write back

| Measured | Where it goes | Notes |
|---|---|---|
| IN1 offset (mV and A, with sign) | lab notes; `trip_amps` / `zero_win_amps` if needed | carries over to `[mot]` unchanged (same scale) |
| Dead zone onset (counts) | `[mot_bench] u_floor_counts` (optional); lab notes | carries over to `[mot]`: its floor is 90 % of the dead zone; compare with the model's 3593 counts and correct `V_th`/termination/gain if they disagree |
| Gain near threshold, s (A per OUT1 count) | `[mot_bench] kp_counts_per_amp`, `ki_counts_per_amp_ms` | **not** the 100 A value |
| Freewheel slope and drain-bar voltage | lab notes → `V_f` in `model/coil_servo_model/channels.py` | low-current value only |
| Freewheel vs TVS | lab notes; `turnoff_model` | carries over |
| Bridge dead time (V_GS scope) | `deadtime_us` in both sections | carries over |

## 6. What carries over to the 100 A build, and what does not

**Carries over** (no need to repeat in detail): wiring and polarity of
every signal; the FSM — arming, flips, zero-window qualification, trips,
TRIPPED/acknowledge, heartbeat latch and the stop-before-reset rule;
OUT1 termination and op-amp gain; the dead-zone onset (at the same
temperature); the IN1 offset (same sensor scale, if no R_G jumper); the
turn-off path (freewheel or TVS); the bridge dead time; that the register
values the host derives reach the board correctly.

**Must be re-measured on the SE-600-5 with `mot`:**

- G_pass and the PI gains — at 100 A all six FETs conduct with ~0.5 V of
  overdrive and the ballast resistors dominate; the bank gain is very
  different from the near-threshold value measured here;
- freewheel time (~1.3 ms from 100 A with the pair) and V_f at high
  current;
- the compliance ramp near 100 A (rail sag, cable and contact resistance);
- heat: ~50 W per FET at 100 A — the uncooled duty limit
  (bringup.md section 6.2);
- the heartbeat trip into the SE-600-5 (0.08 J; over-voltage shutdown?);
- hold noise against the 0.1 % spec, including ground noise from 100 A
  return currents and the switcher (absent on the bench);
- FET current sharing (at 3 A it is poor by design; it matters at 100 A).

To go back, work through the left column of the checklist at the top.

## 7. When something looks wrong (bench-specific)

- **A tool prints "board holds the 'mot' settings" (or the reverse)** —
  the board was last configured with the other profile. Check the
  hardware matches the name you meant, then run `check` with that name.
  "UNKNOWN / mixed" right after `deploy` is normal (nothing configured
  yet).
- **The supply's CC lamp comes on in closed loop** — something asked for
  more than 3 A and the trip did not catch it: check the IN1 offset's sign
  (4.1) and `G_inamp` against the board.
- **Board reading ≠ supply ammeter by ~10×** — `G_inamp` in the file does
  not match the R_G jumper.
- **No current anywhere up to the clamp (4000 counts) in open loop** — the
  dead zone is higher than expected: re-check the OUT1 termination and
  op-amp gain (bringup.md section 4), the op-amp supply, the gate wiring.
- **Current flows with the setpoint at zero in RUN** — the floor is above
  the real dead zone (warm FETs lower the threshold): lower
  `u_floor_counts` or remove it.
- **TIMEOUT_HOLD on flips** — IN1 offset close to the 0.5 A zero window.
- **The loop is slow (several ms) but does not ring** — the real gain near
  threshold is below the model's; measure s (4.2) and rescale the gains.
