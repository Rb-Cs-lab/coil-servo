# Bench-supply test: the MOT channel at ≤ 3 A

A first hardware campaign for the MOT channel's MVP power stage
([mvp_architecture.md](mvp_architecture.md)) with a **current-limited
bench supply (5 V, 3 A limit) in place of the Mean Well SE-600-5**. The
point is to verify the whole circuit and measure what the model only
guesses — pass-bank dead zone and gain, IN1 offset, turn-off path, flips,
both trips, loop gains — at a current where no mistake can hurt anything.
Everything learned here is then re-checked on the SE-600-5.

*Written 2026-11-10. Needs the MVP bitstream (register-map revision 3,
rewrite step 6): the host tools refuse to configure the board's current
2026-08-27 bitstream. Do this campaign **after step 6**, and after
[bringup.md](bringup.md) sections 0–4 (network, deploy, logic-board LED
test, gate stage incl. the OUT1 termination check).*

## Words used here

- **CV / CC.** A bench supply is a *constant-voltage* (CV) source until
  the load asks for more than its current limit; then it switches to
  *constant current* (CC): the current stays at the limit and the voltage
  drops to whatever the load allows. Most supplies show a "CC" lamp then.
- **Channel / profile name.** Every host tool takes a name that selects a
  section of [host/config/channels.toml](../host/config/channels.toml).
  This campaign uses **`mot_bench`** — the MOT board with bench settings.
- **I_FS (sensor full scale).** The coil current that gives +1 V on IN1.
  One **ADC count** (the smallest step the board can measure) is I_FS/8192.
- **LEM turns.** The LEM current sensor measures the current through the
  hole in its body. Passing the wire through the hole N times multiplies
  the signal by N, so the board sees N times finer current steps.
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
| LEM turns | 1 | **10** (`lem_turns`) | at I_FS = 200 A one ADC count is 24 mA; 3 A would be only ~120 counts |
| I_FS / ADC count | 200 A / 24.4 mA | **20 A / 2.44 mA** | follows from the turns |
| Rated (`I_rated`) | 100 A | **2.5 A** | highest closed-loop setpoint; the `step` tool refuses more |
| Firmware trip | 100 A | **2.8 A** | above every allowed setpoint, below the 3.0 A supply limit, so it fires before the supply goes into CC (0.2 A margin ≫ the ~30 mA IN1 offset) and can be tested (4.4) |
| OUT1 clamp | derived: 4474 counts | **set directly: 4000 counts** | at 2.5 A the derived clamp sits only ~44 counts above the dead zone — inside its uncertainty — and could block the first measurement. 4000 counts (0.49 V at OUT1, ~3.9 V gate) leaves ~0.4 V of gate headroom; the supply limit, not the clamp, bounds the current |
| Gains kp / ki | 0.1044 / 3149 s⁻¹ | **0.01044 / 315 s⁻¹** | the model's plant gain G_pass/I_FS is 10× larger with 10 turns; same crossover (~2.8 kHz) and phase margin |
| Ramp rates | 200 / 50 A/ms | **200 / 50 A/ms** | unchanged on purpose: the physical limits are rail/L and V_f/L, which do not depend on the current |
| Flip zero window | 0.5 A | **0.1 A** (41 counts) | ~3× the expected IN1 offset at this scale; still < 5 % of rated |
| Trip timeout | 3 ms | 3 ms | the freewheel from 2.5 A takes only ~0.05–0.1 ms; the timeout is a safety net |
| Dither | on | on | one OUT1 count is ~0.12 A (5 % of 2.5 A) — without the dither the hold hunts by several % |

**Never run `mot_bench` on the SE-600-5.** Its clamp asks the bank for far
more than 3 A; the bench supply's limit is what makes that harmless. And
use `mot_bench` only while the LEM carries the 10 turns: with one turn
every reading is 10× too small (trip at 28 A real). Conversely, `mot`
with 10 turns reads 10× too high.

**If the control computer drives IN2 (analog setpoint) on the bench,** its
scaling changes with I_FS: through the ÷10 divider it is **2 A per
computer volt** (2.5 A = 1.25 V), not the production 20 A per volt. The
host tools below use the setpoint register instead and need no change.

## 2. Equipment

- A bench supply with **≥ 5 V and ≥ 3 A**, an adjustable current limit,
  a floating (not earthed) output, and a current display. Remote sense
  off. Its ammeter is a free, independent check of the LEM (section 4.1).
- Thin flexible wire for the LEM winding, ≥ 1.5 mm² (rated well above
  3 A), long enough for 10 turns through the LEM hole plus the run
  between the two coils (~1–2 m).
- Oscilloscope, 4 channels (IN1 or a current probe, pass-bank drain bar,
  bridge node A, and DIO0/DIO1 or DIO6).
- A thermocouple or IR thermometer for the FET plates (precaution only).

## 3. Wiring

1. **Supply.** With everything off, disconnect the SE-600-5 outputs and
   connect the bench supply in their place: **+ to the rail bar**,
   **− to where the SE-600-5's − went** (the pass-bank side of the
   high-current loop). Short, twisted leads. Leave the SE-600-5
   disconnected (not just switched off).
2. **LEM winding.** The LEM sits on the lead between the two coils.
   Replace that 4/0 lead with the thin wire, passing it **10 times through
   the LEM hole** — a "turn" is one pass through the hole, all in the same
   direction (the direction the 4/0 lead had, so positive current still
   reads positive). The 4/0 lead must be removed: any second pass of the
   coil current through the hole would add to the count.
   - **PROVISIONAL:** confirm 10 turns of your wire really fit through the
     LF 310-S hole. If only N fit, set `lem_turns = N` in `[mot_bench]` —
     I_FS, the trip level, the zero window and the ramp codes follow
     automatically. Re-run `python -m coil_servo_host.config mot_bench`.
   - The thin wire adds tens of mΩ to the coil loop. At 3 A that is
     < 0.1 V and only makes the freewheel slightly faster — harmless.
   - *Alternative without rewinding:* raise the AD8421 in-amp gain
     (G = 1 + 9.9 kΩ / R_G; G = 10 gives I_FS = 20 A too) and set
     `G_inamp` in channels.toml. That is a soldering job on the sensor
     board and also amplifies the LEM's own offset; the turns are easier
     and undone by refitting the 4/0 lead.
3. **Coils.** The campaign runs **with the coils connected**: at ≤ 3 A
   they store ≤ 72 µJ and dissipate < 0.1 W, and the freewheel, flip and
   heartbeat tests need their inductance. (A resistive dummy load in
   their place works for sections 4.1–4.4 only.) If you can, do the
   four-wire coil resistance measurement of [bringup.md](bringup.md)
   section 6.1 first — it only needs the coils disconnected once.
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

   Expect the summary with `supply limit = 3 A`, `I_FS = 20 A`, heartbeat
   advancing, state IDLE.
3. Logic board: press the latch reset (the red LED goes off) with arm
   (DIO4) **low**. Turn the supply output **on**. Nothing should flow: the
   supply shows ~0 A, CV mode.
4. In a second terminal, leave this running for the whole campaign:

   ```bash
   python -m coil_servo_host.watch mot_bench
   ```

### 4.1 IN1 offset and the LEM turns

1. With the supply on and the servo disabled, read the measured current
   (`check mot_bench`, or `watch`). That reading at zero current is the
   **IN1 offset**. Expected: about ±30 mA (the ~1.4 mV seen in Nov 2026 is
   28 mA at I_FS = 20 A). **Write it down.** It must be well below the
   zero window (0.1 A); if it is above ~50 mA, widen `zero_win_amps`
   (flips and trips decide "current is zero" with it).
2. The turns check comes with the first current in 4.2: compare the
   board's reading (minus the offset) with the **supply's ammeter**. They
   should agree to a few %. A factor near 10 (or N/10) means the turns or
   `lem_turns` are wrong — stop and fix that first, since the trip level
   depends on it.

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
with increasing `--counts`, e.g. 3500, 3550, then in steps of 5–10 counts
once current starts to flow (nominally ~3593; the clamp stops you at
4000). For each run note the steady current (the end of the CSV's
`i_amps` column, or the supply's ammeter).

- The command where current first flows is the **dead zone** (in counts;
  OUT1 volts = counts / 8192, terminated).
- The slope above it, in amps per count, is the bank's gain **at this
  current**. The plant gain is K = slope × 8192 / 20 A (the model says
  ~48; expect less near threshold).
- Going past ~2.8 A **trips the firmware** (state TRIPPED in `watch`) —
  that is the trip working. In open loop the current rises fast
  (~0.3 A/µs on 5 V), so a command asking for well over 3 A can touch the
  supply limit for a few µs before the trip (4 samples of ~1 µs) acts; a
  brief flicker of the CC lamp is harmless. To continue: `python -c "from coil_servo_host import Board, load_channel; b = Board(load_channel('mot_bench')['host']); b.pulse('trip_ack')"`,
  then drop and raise arm on the next run.

Then in `[mot_bench]`:

- set `kp = 0.5 / K` and `ki = 15080 / K` (1/s) from your measured K
  around 1–2 A (the commissioned 0.5 and 15080/s divided by the plant
  gain — what `coil_servo_model.analysis.suggest_gains` does);
- optionally set `u_floor_counts` = (measured dead zone − 50 counts).
  With the default floor (90 % of the dead zone) a step from zero to 2 A
  waits ~1.4 ms before current flows, because the servo must first cross
  the gap; 50 counts below the onset (~50 mV of gate, ~10 K of threshold
  drift) brings 2 A steps from zero to ~1 ms. If current then flows with
  the setpoint at zero, the floor is too high — lower it.

### 4.3 Closed-loop steps and hold noise

```bash
python -m coil_servo_host.step mot_bench --amps 2 -o step_2A.csv
```

(`step` always steps from zero.) Plot it as in
[bringup.md](bringup.md#looking-at-the-data). Look for: no overshoot, no
ringing, settled within ~1–2 ms (the model: ~2.2 ms from zero with the
90 % floor, ~1 ms with the floor at onset − 50), final value within a
couple of mA of 2 A (one ADC count is 2.44 mA — the measurement's
resolution). Repeat at 1 A and 2.5 A. Below ~1 A only one or two FETs
conduct, so the loop is slower there: expected.

Hold noise: repeat the 2 A step with `dither_enable = 0` and compare the
tail of the two captures (or scope IN1). The model predicts ~50 mA
peak-to-peak hunting without the dither (one OUT1 count is ~0.12 A) and a
few mA with it. Set `dither_enable = 1` again afterwards.

### 4.4 Firmware over-current trip

Test the real 2.8 A level in closed loop. The `step` tool refuses targets
above `I_rated`, so temporarily set `I_rated = 2.9` in `[mot_bench]` (the
loader still requires it below the 3.0 A supply limit), then:

```bash
python -m coil_servo_host.step mot_bench --amps 2.9 -o trip.csv
```

The current must rise, stop just above 2.8 A, freewheel to zero, the
bridge must open (amber LED off), and `watch` must show TRIPPED. The
supply's CC lamp must **not** come on (the integration simulation peaks
below 2.95 A). Acknowledge as in 4.2. **Restore `I_rated = 2.5`.**

The loader refuses a `trip_amps` at or above `I_supply_max` for exactly
this reason: if the supply limited the current first, the trip would
never fire, and the servo would wind its integrator up against the clamp
(the model shows a several-ms recovery afterwards).

### 4.5 Turn-off path: the freewheel scope capture

Hold 2.5 A (closed loop, e.g. `step mot_bench --amps 2.5` — the tool
returns the setpoint to zero at the end, which is a snap-off). Scope the
pass-bank **drain bar**, bridge **node A** and IN1, triggered on the fall.

- **Freewheel (expected):** the drain bar rises to ≈ V_rail + ~0.6 V and
  the current falls almost linearly to zero in ~50–70 µs (rate V_f / L,
  ~40–50 A/ms). The slope gives the body-diode drop at low current:
  V_f = 16 µH × dI/dt.
- **TVS instead:** drain bar at ~13 V and a fall of only ~5 µs. Report it
  — the model has a flag for that case (mvp_architecture.md 3.3).

The *path* carries over to 100 A; the timing does not (V_f and the I·R
term grow with current: ~1.3 ms from 100 A).

### 4.6 Flips

Hold ~2 A in closed loop (setpoint register or IN2), then pulse DIO3
(flip request). Scope DIO0 (polarity), DIO1 (enable) and the current:
enable drops only after the current is inside the 0.1 A window, polarity
toggles inside the enable-low window with the dead time visible on both
sides. After the flip the *setpoint sign* must follow the polarity —
otherwise `watch` shows `sp_sign_mismatch` and the servo sits at zero,
which is safe. If flips end in TIMEOUT_HOLD, the IN1 offset is too close
to the zero window (4.1). Also scope V_GS of the bridge FETs during a flip
to confirm `deadtime_us`: the FSM's enable-low window is the only
shoot-through protection (mvp_architecture.md section 4).

### 4.7 Heartbeat-latch trip at current

Hold 2.5 A, then pull the DIO6 wire. The logic board opens the bridge at
current; the coil's ½·L·I² ≈ **50 µJ** (72 µJ at 3 A) goes back into the
bench supply. A bench supply cannot sink current, so this charges its
output capacitor — a bump of millivolts to ~0.1 V for typical capacitors;
some supplies show a brief error or trip their over-voltage protection.
Scope the rail. At this energy nothing can be harmed; on the SE-600-5 the
same event returns ~0.08 J (100 A), which is the open question this test
previews (mvp_architecture.md 3.3).

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
| LEM turns that fit | `[mot_bench] lem_turns` | everything scaled from it follows |
| IN1 offset (mV, and A at this scale) | lab notes; `zero_win_amps` if needed | the offset in **millivolts** carries over; in amps it is 10× larger at I_FS = 200 A |
| Dead zone onset (counts) | `[mot_bench] u_floor_counts` (optional); lab notes | carries over to `[mot]`: its floor is 90 % of the dead zone; compare with the model's 3593 counts and correct `V_th`/termination/gain if they disagree |
| Gain near threshold, K | `[mot_bench] kp`, `ki` | **not** the 100 A value |
| Body-diode V_f at ~2.5 A (freewheel slope) | lab notes → `V_f` in `model/coil_servo_model/channels.py` | low-current value only |
| Freewheel vs TVS | lab notes; `turnoff_model` | carries over |
| Bridge dead time (V_GS scope) | `deadtime_us` in both sections | carries over |

## 6. What carries over to the 100 A build, and what does not

**Carries over** (no need to repeat in detail): wiring and polarity of
every signal; the FSM — arming, flips, zero-window qualification, trips,
TRIPPED/acknowledge, heartbeat latch and the disarm-before-reset rule;
OUT1 termination and op-amp gain; the dead-zone onset (at the same
temperature); the IN1 offset in volts; the turn-off path (freewheel or
TVS); the bridge dead time; that the register values the host derives
reach the board correctly.

**Must be re-measured on the SE-600-5 with `[mot]`:**

- G_pass and the PI gains — at 100 A all six FETs conduct with ~0.5 V of
  overdrive and the ballast resistors dominate; the bank gain is very
  different from the near-threshold value measured here;
- freewheel time (~1.3 ms from 100 A) and V_f at high current;
- the compliance ramp near 100 A (rail sag, cable and contact resistance);
- heat: ~50 W per FET at 100 A — the uncooled duty limit
  (bringup.md section 6.2);
- the heartbeat trip into the SE-600-5 (0.08 J; over-voltage shutdown?);
- hold noise against the 0.1 % spec, including ground noise from 100 A
  return currents and the switcher (absent on the bench);
- FET current sharing (at 3 A it is poor by design; it matters at 100 A).

**To go back to production:** supply output off, bench supply
disconnected, SE-600-5 reconnected, the 4/0 lead refitted through the LEM
**once**, and every tool run with `mot` again (`deploy mot`, `check mot`
— check must read I_FS = 200 A).

## 7. When something looks wrong (bench-specific)

- **The supply's CC lamp comes on** — something asked for more than 3 A
  and the trip did not catch it: check the LEM turns against `lem_turns`
  (4.1) and the IN1 offset.
- **Board reading ≠ supply ammeter by ~10×** — LEM turns vs `lem_turns`,
  or the wrong channel name (`mot` instead of `mot_bench`).
- **No current anywhere up to the clamp (4000 counts) in open loop** — the
  dead zone is higher than expected: re-check the OUT1 termination and
  op-amp gain (bringup.md section 4), the op-amp supply, the gate wiring.
- **Current flows with the setpoint at zero in RUN** — the floor is above
  the real dead zone (warm FETs lower the threshold): lower
  `u_floor_counts` or remove it.
- **TIMEOUT_HOLD on flips** — IN1 offset close to the 0.1 A zero window.
- **The loop is slow (several ms) but does not ring** — the real gain near
  threshold is below the model's; measure K (4.2) and rescale the gains.
