# Bring-up checklist (MVP power stage)

For the first runs of the MOT channel: Red Pitaya + logic board + pass bank
+ H-bridge + SE-600-5 (docs/mvp_architecture.md). Work through it **in
order**; every step is safe to repeat. Each power step starts at low
current, and **no coil is connected until section 6**. Values you measure
go into [host/config/channels.toml](../host/config/channels.toml) — the
host derives the register settings from them (`python -m
coil_servo_host.config mot` shows what it derives).

*Rewritten 2026-11-10 (MVP rewrite step 5). Needs the MVP bitstream
(register-map revision 3, rewrite step 6); the host refuses to configure
the 2026-08-27 bitstream.*

A note on jargon: the **bitstream** is the compiled FPGA design
(`coil_servo.bit.bin`); the **register server** is a small Python program
on the board that lets the lab PC read and write the FPGA's settings
("registers") over the network; **open loop** means OUT1 is set directly,
without feedback; **closed loop** means the servo is regulating.

## 0. Lab PC and network

**Python environment on the lab PC** (once): follow README "Setup A" —
Python ≥ 3.11 and `pip install -e .[dev]` from the repo root. Every command
below runs on the lab PC from the repo root.

**Wired Ethernet only, never WiFi.** Use the board's fixed IP
(192.168.0.65 for the MOT board) or the name from the sticker on the
Ethernet jack (`rp-xxxxxx.local`). Record it as `board_host` in
channels.toml.

Set up passwordless ssh once — the deploy tool runs several ssh/scp
commands per deploy:

```bash
ssh-copy-id root@192.168.0.65
```

(`ssh-copy-id` doesn't exist on stock Windows; there, generate a key with
`ssh-keygen` and append `~/.ssh/id_ed25519.pub` to
`/root/.ssh/authorized_keys` on the board.)

## 1. Board and logic-board checks before power

- SD card carries the **official Red Pitaya OS 3.x** image.
- IN1/IN2 input jumpers on **LV (±1 V)**.
- E1 wired to the logic board as below. E1 is 3.3 V logic and **not 5 V
  tolerant**. Every line has a 10 kΩ pull-down on the logic board.
- Nothing connected to OUT1/OUT2 yet.

| Signal | Direction | E1 pin | MVP |
|---|---|---|---|
| DIO0_P bridge polarity | out | 3 | → logic board (74HC04 → drivers) |
| DIO1_P bridge enable | out | 5 | → logic board NAND |
| DIO2_P boost enable | out | 7 | **not connected** (held low by firmware) |
| DIO3_P flip request | in | 9 | control computer (level-shifted) |
| DIO4_P arm | in | 11 | control computer (level-shifted) |
| DIO5_P fault | in | 13 | **not connected** |
| DIO6_P heartbeat | out | 15 | → logic board monostable |
| GND | — | 25/26 | common |

## 2. Deploy and sanity-check (no analog connections)

Build the bitstream on the Ubuntu/Vivado machine (`make bit && make
coil_servo.bit.bin`, or take it from `bitstreams/`), put
`coil_servo.bit.bin` in the repo root on the lab PC, then:

```bash
python -m coil_servo_host.deploy mot --bitstream coil_servo.bit.bin
```

Deploy copies the bitstream and the register server, loads the FPGA,
starts the server, and then **checks the board came up safe**: register
map revision 3, state IDLE, OUT1 command 0, bridge enable low, arm low. It
fails loudly otherwise (e.g. arm wired high: drop it).

```bash
python -m coil_servo_host.check mot
```

Expect: the derived values printed, heartbeat ADVANCING, the LED walk,
"configuration written and read back OK", OUT2/boost 0, state IDLE,
measured current ≈ 0. The reading at zero current is the **IN1 offset**
(≈ +0.29 A at I_FS = 200 A in Nov 2026) — note it for section 5.

**After every board reboot** the bitstream and the server are gone (no
heartbeat → the latch stays tripped, which is safe). Tools then say "no
register server answering … run deploy". Re-run deploy. (The 2026-11-03
workaround — ssh in, `fpgautil -b`, start the server by hand — is what
deploy automates. Auto-loading at boot via systemd is possible but
changes what state the board boots into; not done.)

To watch current, state and flags live (read-only, safe to leave running):

```bash
python -m coil_servo_host.watch mot
```

## 3. Board and logic board on their own (no power stage)

Take all 3.3 V for jumper tests **from E1 pins 1/2**, never from 5 V.

### 3a. Logic-board acceptance test (LEDs)

Green = ALIVE (heartbeat OK), amber = BRIDGE EN (drivers enabled),
red = FAULT (latch tripped). Before step 3, set `servo_enable` from
Python **first** (the FSM needs an arm *edge* after it):

```bash
python -c "from coil_servo_host import Board, load_channel; ch = load_channel('mot'); b = Board(ch['host']); b.apply_config(ch['cfg']); b.write_cfg(servo_enable=1); print(b.sts()['fsm_state_name'])"
```

| Step | Action | Green | Amber | Red |
|---|---|---|---|---|
| 1 | Power the logic board, heartbeat running | on | off | **on** (powers up tripped) |
| 2 | Press reset | on | off | off |
| 3 | Arm: jumper pin 11 to 3V3 (FSM → RUN, DIO1 high) | on | on | off |
| 4 | Pull the DIO6 (heartbeat) wire | off | off | on |
| 5 | Re-plug DIO6 | on | off | on (stays latched) |
| 6 | **Remove the arm jumper**, press reset | on | off | off |
| 7 | Re-arm (jumper pin 11 to 3V3 again; drop it first if still in) | on | on | off |

Step 6 is the operating rule: **disarm before pressing reset** — the
firmware never saw the bridge open in step 4 and its integrator may be
wound up. Finish with `b.write_cfg(servo_enable=0)` and remove the
jumper.

**Monostable timeout:** scope DIO6 and the monostable output while
pulling DIO6: the drop must come a few ms after the last heartbeat edge
(220 kΩ / 100 nF measured **≈ 7.6 ms**, 2026-11). With 68 kΩ it was
~1.1 ms, about one heartbeat period — too short.

### 3b. Digital inputs and outputs

Run `watch` in one terminal:

- With `servo_enable=1` set, arm (pin 11) → `armed` goes 1, IDLE → RUN,
  pin 5 high. If arm was already high before `servo_enable`, nothing
  happens (by design); unplug and replug it.
- Pin 9 (flip request) while in RUN: the FSM runs a full flip; with
  nothing on IN1 the current is already inside the zero window, so it
  completes at once — scope pins 3 and 5 to see polarity toggle inside the
  enable-low window with the dead time on both sides.
- `dio_invert` (CFG word 13) flips each input's sense.
- Pins 7 (boost) and the OUT2 SMA must stay at 0 throughout.

### 3c. Analog loopback (OUT1 → IN1, one SMA cable)

The board becomes its own plant: OUT1 is read back on IN1 (×2 because IN1
is high-impedance; add a 50 Ω feed-through to get ×1). **Set `u_floor` to
0 for these tests** — otherwise in RUN OUT1 rests at ~0.39 V, which loops
back as ~158 A of "measured current" and trips the 100 A over-current
trip at once (that is the trip working).

1. *Scaling (open loop):* `open_loop=1`, `sp_source=1`, `setpoint=1000`
   → OUT1 ≈ 122 mV, `i_meas` ≈ 1000 counts × the loopback factor.
   A setpoint of 8000 must stop at the clamp (`out_clamp`, 4474 counts ≈
   0.55 V). A negative setpoint gives 0 V on OUT1 and on OUT2.
2. *Closed loop:* `open_loop=0`, setpoint a few "amps": the loop
   regulates `i_meas` onto the setpoint (the MVP gains are rescaled for a
   plant gain of ~4.8, so on the loopback's ~2 it is slower than the
   August commissioning — expected).
3. *Trip:* with the loopback, raise the setpoint register above the trip
   level (in sensor-scale amps): state TRIP → TRIPPED, OUT1 0, bridge
   enable low; `watch` shows `tripped`. `b.pulse('trip_ack')`, drop and
   re-raise arm to recover.
4. *Tools:* `step` (closed loop) and `sweep` (function generator into IN2,
   open loop) run end to end.

**Slow analog (AIN0/AIN1):** feed a known DC level and read the raw XADC
words at 0x4400_0000 (`Board.read_words(0x4400_0000, 8)`) — "does it
move" only; no calibrated readout tool yet.

## 4. Gate stage, no high current

Power the op-amp stage only (pass-bank drains not powered).

1. **OUT1 termination — check before connecting the op-amp.** Open loop,
   `setpoint=4000`: OUT1 at the op-amp input reads ~0.49 V if terminated
   in 50 Ω, ~0.98 V if not. Unterminated doubles the gate swing (up to a
   16 V command). Set `out1_terminated` in channels.toml to match.
2. **Gain and rail:** the op-amp output (the gate command) should be
   ≈ 7.98 × OUT1. Record the op-amp supply rail; it must exceed the
   largest gate command you will use (clamp ≈ 0.55 V × 8 ≈ 4.4 V, ×2 if
   unterminated).
3. Gates: each gate pin follows the command; with the op-amp unpowered
   the 47 kΩ pull-downs bring the gates to 0 within a few ms.

## 5. Power stage on a resistive dummy load (no coil)

A dummy load in place of the coils. **Start every step at a few amps.**
The first characterization can use a current-limited bench supply in
place of the SE-600-5 — the low-current test profile `mot_bench` (5 V,
3 A limit, one coil) and its step-by-step procedure are in
[bench_supply_test.md](bench_supply_test.md); then repeat on the SE-600-5.
Switching between the two is the channel name only (`mot_bench` / `mot`)
plus the hardware items in its
[switching checklist](bench_supply_test.md#switching-between-bench-and-100-a);
board LED 7 lit = bench settings loaded.

1. **IN1 offset.** At zero current, read `i_meas` (or `check`). Record
   the offset; it must be well below the flip zero window (±0.5 A) before
   flips are trusted.
2. **G_pass and the dead zone (open loop).** Step the OUT1 command in
   small increments with the open-loop step tool and record the steady
   current for each:

   ```bash
   python -m coil_servo_host.step mot --amps 2 --open-loop -o step_openloop.csv
   ```

   (`--amps` is converted to an OUT1 command through the model; the
   captured current shows what really happened.) The command at which
   current first flows is the dead zone; the slope above it is G_pass.
   Put the result in channels.toml (adjust `g_fs` / `V_th`, or set
   `out_clamp_counts` and `u_floor_counts` directly), and re-check
   `python -m coil_servo_host.config mot`.
3. **Closed-loop step and gains.** Set `kp`/`ki` from the measured plant
   gain (`coil_servo_model.analysis.suggest_gains`), then:

   ```bash
   python -m coil_servo_host.step mot --amps 5 -o step_closedloop.csv
   ```

   Look for settling without ringing; iterate. Repeat at a few currents
   (the plant gain varies with temperature and current).
4. **Over-current trip.** Temporarily set `trip_amps` low (e.g. 10 A),
   command above it: the current must stop and the board latch in
   TRIPPED. Restore `trip_amps`.
5. **Heartbeat trip at current.** At a few amps, pull DIO6: the bridge
   opens at current and the energy goes back into the supply. Watch the
   SE-600-5 (does its over-voltage protection trip?) and scope the rail.
   Disarm, `servo_enable=0`, reset the latch, re-arm.
6. **Hold noise.** Hold a steady current with `dither_enable` = 1 and = 0
   and capture the current noise (`step` capture or a scope on IN1),
   against the 0.1 % spec. This decides the resolution follow-ups
   (future_upgrades.md sections 7, 8d).

## 6. With the coil

1. **Before connecting:** four-wire (Kelvin) resistance of each coil pair
   and of the 4/0 cabling + contacts, and an LCR inductance check. Update
   the model (`model/coil_servo_model/channels.py` R_coil, R_cable).
2. **Uncooled duty limit.** The first round runs **without water**. Each
   pass FET dissipates ~50 W at 100 A continuous. Before the run, write
   down a pulse length, current and duty cycle, and check the plate
   temperature rise on a short trial (thermocouple or IR) before going
   further. Start low (a few tens of amps, short pulses, low duty) and
   raise only with measured temperatures.
3. **Turn-off path — the decisive scope capture.** From a moderate current,
   step the setpoint to zero (or `servo_enable=0`) and scope the pass-bank
   drain bar and bridge node A. Expected (freewheel, the decided design):
   drain bar at ~V_rail + 0.7 V and a ~ms decay. A drain bar at ~13 V with
   a ~0.2 ms decay would mean the TVS is conducting instead — report it,
   the model has a flag for that case.
4. **Closed-loop steps** at increasing currents, then the slew-limited
   steps the sequences will use (S2, S4 in operating_scenarios.md).
5. **Flip machinery:** with a modest current flowing, pulse DIO3 and scope
   DIO0/DIO1 and the current: enable drops only after the current has
   freewheeled into the zero window, polarity toggles between the enable
   edges with the dead time visible, and the servo ramps back up.
6. **Eddy-current settling** after flips and snap-offs (atoms or a probe
   coil) → `settle_us`.

## Looking at the data

`step` and `sweep` write CSV with header rows (seconds, amps, Hz, degrees;
in open loop `mag` is the plant gain G_pass/I_FS, not 1). Quick look:

```bash
python -c "import numpy, matplotlib.pyplot as plt; d = numpy.genfromtxt('step.csv', delimiter=',', names=True); plt.plot(d['t_s'] * 1e3, d['i_amps']); plt.xlabel('ms'); plt.ylabel('A'); plt.show()"
```

## When something misbehaves

- **"no register server answering … run deploy"** — board rebooted or the
  server was never started. Re-run deploy. If that fails, `ping` the board
  first.
- **"register-map revision 0, this host expects 3"** — the board runs the
  old (August 2026) bitstream. Deploy the current `coil_servo.bit.bin`.
  The host refuses to write to it because the new settings would land on
  the wrong registers.
- **deploy "post-check FAILED"** — read the listed problem; usually arm is
  wired high (drop it) or the wrong bitstream.
- **`check` reports heartbeat STUCK** — the bitstream isn't loaded or isn't
  ours. Rebuild with `make coil_servo.bit.bin` (the byte-swapped file, not
  the plain `.bit`) and redeploy.
- **Tool waits "raise arm (DIO4) now"** — the FSM needs an arm edge after
  `servo_enable`. Drop and raise arm. After 30 s it gives up and sets
  `servo_enable` back to 0.
- **State TRIPPED / `tripped` flag** — measured current exceeded
  `trip_amps`. Find out why (setpoint too high? loop oscillating? pass
  bank misbehaving?), then `b.pulse('trip_ack')` and re-arm.
- **State TIMEOUT_HOLD** — a flip couldn't reach the zero window within
  `flip_timeout`. Nothing is broken: the bridge is closed and the loop is
  pulling toward zero. Check the IN1 offset against `zero_win_amps`;
  acknowledge per [register_map.md](register_map.md).
- **Red LED on (latch tripped)** — heartbeat lost (board rebooted, wire
  loose, redeploy). Disarm, `servo_enable=0`, fix the cause, press reset,
  then enable and arm.
- **`sp_sign_mismatch`** — the setpoint sign disagrees with the bridge
  polarity; the loop sits at zero until they agree.
- **`TimeoutError: FIFO filled only N/16384`** — the FPGA stopped feeding
  the capture FIFO; almost always the wrong bitstream. Redeploy.
- **Ethernet pulled / lab PC crashed mid-run** — the loop keeps running in
  the FPGA at the last settings, on purpose; the heartbeat latch is the
  hardware protection.
