# First-hardware bring-up checklist

For the first runs of a board with the coil servo bitstream. **Nothing in
this list connects a coil: first runs go into a dummy resistive load with no
coil attached** (BOOTSTRAP deliverable 7). Work through it in order; every
step is safe to repeat.

## 0. Network — wired Ethernet only

The boards talk to the lab PC over **wired Ethernet, never WiFi** (register
access and FIFO drains need the latency and reliability of a wire; a WiFi
hop also makes the deploy scripts flaky). Either put the board on the lab's
wired switch and use the mDNS name from the sticker on the Ethernet jack
(`rp-xxxxxx.local`), or connect it directly to a second Ethernet port on
the PC (give that port a static IP like `192.168.42.1/24` and set a static
IP on the board). Record the working address as `board_host` in
[host/config/channels.toml](../host/config/channels.toml).

Set up passwordless ssh once: `ssh-copy-id root@<board_host>`.

## 1. Board checks before power

- SD card carries the **official Red Pitaya OS 3.x** image.
- IN1/IN2 input jumpers on **LV (±1 V)**.
- E1 wiring per the port table in [CLAUDE.md](../CLAUDE.md). E1 is 3.3 V
  LVCMOS and **not 5 V tolerant** — the flip/arm/fault inputs must come
  through the level shifters.
- Nothing connected to OUT1/OUT2 yet.

## 2. Deploy and sanity-check (no analog connections)

On the Vivado machine: `make bit && make coil_servo.bit.bin`, copy
`coil_servo.bit.bin` here. Then:

```bash
python -m coil_servo_host.deploy mot --bitstream coil_servo.bit.bin
```

```bash
python -m coil_servo_host.check mot
```

Expect: heartbeat ADVANCING, the LED walk on the board, state IDLE,
measured current ~0. Put a scope on DIO6 (E1 pin 7 = K16 side): a ~954 Hz
square wave that never stops is the watchdog heartbeat — this is also the
moment to wire and test the external monostable that drops bridge enable
when the heartbeat disappears.

## 3. Analog sanity into a scope (still no load)

Scope on OUT1. Arm the board (DIO4 high), then from Python:

```bash
python -c "from coil_servo_host import Board, load_channel; ch = load_channel('mot'); b = Board(ch['host']); b.apply_config(ch['cfg']); b.write_cfg(sp_source=1, open_loop=1, setpoint=1000, servo_enable=1); input('OUT1 should read ~122 mV; Enter to zero'); b.write_cfg(setpoint=0, servo_enable=0)"
```

1000 counts = 122 mV at OUT1. Check OUT2 stays at 0. Repeat with
`setpoint=-1000`: OUT1 returns to 0, OUT2 shows 122 mV. Check a setpoint of
8000 gives only 800 mV (= 6554 counts: the 100 %-of-rated clamp).

## 4. Dummy resistive load

Connect the pass bank to the dummy load per the power-stage procedure.
Then, in order:

1. **Open-loop step** (this is the measurement the PI gains come from):

   ```bash
   python -m coil_servo_host.step mot --amps 2 --open-loop -o step_openloop.csv
   ```

2. **Swept-sine transfer function** — function generator into IN2 (small
   amplitude, positive offset so the drive stays on OUT1), then:

   ```bash
   python -m coil_servo_host.sweep mot -o tf_dummy.csv
   ```

   Step the generator from ~10 Hz to ~100 kHz; each detected frequency
   appends a CSV row (f, |H|, phase, stimulus amps).

3. Fit the pole from either measurement, set `kp`/`ki` in channels.toml
   accordingly (see `coil_servo_model.analysis.suggest_gains` for the
   formula), then a **closed-loop step**:

   ```bash
   python -m coil_servo_host.step mot --amps 2 -o step_closedloop.csv
   ```

   Look for settling without ringing; iterate gains.

## 5. Flip machinery (still on the dummy load)

With a modest closed-loop current flowing, pulse DIO3 and watch DIO0/DIO1
on a scope: bridge enable must drop only after the current has decayed,
polarity toggles between the two enable edges with the dead time visible,
and the servo resumes. Only after this looks right on the dummy load does a
coil enter the picture.
