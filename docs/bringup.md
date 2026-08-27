# First-hardware bring-up checklist

For the first runs of a board with the coil servo bitstream. **Nothing in
this list connects a coil: first runs go into a dummy resistive load with no
coil attached** (BOOTSTRAP deliverable 7). Work through it in order; every
step is safe to repeat.

## 0. Lab PC and network

**Python environment on the lab PC** (once): follow README "Setup A" —
Python ≥ 3.11 and `pip install -e .[dev]` from the repo root. Every command
below runs on the lab PC from the repo root.

**Wired Ethernet only, never WiFi** (register access and FIFO drains need
the latency and reliability of a wire; a WiFi hop also makes the deploy
scripts flaky). Either put the board on the lab's wired switch and use the
name from the sticker on the Ethernet jack (`rp-xxxxxx.local` — the board
announces this name on the local network automatically, a mechanism called
mDNS), or connect it directly to a second Ethernet port on the PC (give
that port a static IP like `192.168.42.1/24` and give the board a static IP
too — see the Red Pitaya docs "Network manager" page for OS 3.x). Record
the working address as `board_host` in
[host/config/channels.toml](../host/config/channels.toml).

Set up passwordless ssh once — the deploy tool runs three ssh/scp commands
per deploy and will prompt for the password each time otherwise:

```bash
ssh-copy-id root@rp-xxxxxx.local
```

(`ssh-copy-id` doesn't exist on stock Windows; there, generate a key with
`ssh-keygen` and append the contents of `~/.ssh/id_ed25519.pub` to
`/root/.ssh/authorized_keys` on the board.)

## 1. Board checks before power

- SD card carries the **official Red Pitaya OS 3.x** image.
- IN1/IN2 input jumpers on **LV (±1 V)**.
- E1 wiring per the table below. E1 uses 3.3 V logic and is **not 5 V
  tolerant** — the flip/arm/fault inputs must come through the level
  shifters.
- Nothing connected to OUT1/OUT2 yet.

Physical pins on the E1 header (26-pin, pin 1 marked on the board;
**verify against the official Red Pitaya E1 pinout before wiring** — the
DIO*_P signals sit on the odd pins 3–17):

| Signal | Direction | E1 pin |
|---|---|---|
| DIO0_P bridge polarity | out | 3 |
| DIO1_P bridge enable (low = FETs off) | out | 5 |
| DIO2_P boost-cap enable | out | 7 |
| DIO3_P flip request | in | 9 |
| DIO4_P arm | in | 11 |
| DIO5_P interlock fault (read-only) | in | 13 |
| DIO6_P watchdog heartbeat | out | 15 |
| GND | — | 25/26 |

The three inputs have internal pull-downs: a disconnected cable reads as
disarmed / no flip request / no fault.

## 2. Deploy and sanity-check (no analog connections)

On the Vivado machine: `make bit && make coil_servo.bit.bin`, then copy
`coil_servo.bit.bin` into the directory you run the next command from
(the repo root on the lab PC). Then:

```bash
python -m coil_servo_host.deploy mot --bitstream coil_servo.bit.bin
```

```bash
python -m coil_servo_host.check mot
```

Expect: heartbeat ADVANCING, the LED walk on the board, state IDLE,
measured current ~0. Put a scope on the heartbeat (DIO6_P, E1 pin 15): a
~954 Hz square wave that never stops is the watchdog — this is also the
moment to wire and test the external monostable that drops bridge enable
when the heartbeat disappears.

To watch the measured current, state, and flags live at any point during
bring-up (read-only, safe to leave running in its own terminal):

```bash
python -m coil_servo_host.watch mot
```

## 3. Analog sanity into a scope (still no load)

Scope on OUT1. Arm the board (drive DIO4, E1 pin 11, to 3.3 V — it has a
pull-down, so leaving it disconnected means disarmed), then from Python:

```bash
python -c "from coil_servo_host import Board, load_channel; ch = load_channel('mot'); b = Board(ch['host']); b.apply_config(ch['cfg']); b.write_cfg(sp_source=1, open_loop=1, setpoint=1000, servo_enable=1); input('OUT1 should read ~122 mV; Enter to zero'); b.write_cfg(setpoint=0, servo_enable=0)"
```

1000 counts = 122 mV at OUT1. Check OUT2 stays at 0. Repeat with
`setpoint=-1000`: OUT1 returns to 0, OUT2 shows 122 mV. Check a setpoint of
8000 gives only 800 mV (= 6554 counts: the 100 %-of-rated clamp).

## 4. Dummy resistive load

Connect the pass bank to the dummy load per the power-stage documentation
(lives with the power-stage design, not in this repo — TBD: link it here
once it has a home). Then, in order:

1. **Open-loop step** (this is the measurement the PI gains come from):

   ```bash
   python -m coil_servo_host.step mot --amps 2 --open-loop -o step_openloop.csv
   ```

2. **Swept-sine transfer function** — function generator into IN2. Keep in
   mind IN2 arrives through the ÷10 divider, so 1 V at the generator = 0.1 V
   at the board = `I_FS/10` amps of commanded current (12.5 A on the MOT
   channel!). Start small and offset positive so the drive stays on OUT1:
   e.g. **200 mVpp with +300 mV offset at the generator** commands
   2.5 ± 1.25 A on the MOT channel. Then:

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

## Looking at the data

`step` and `sweep` write plain CSV with header rows (units are in the
headers: seconds, amps, Hz, degrees; `mag` is dimensionless — measured amps
per commanded amp). Quick look with matplotlib (installed by Setup A):

```bash
python -c "import numpy, matplotlib.pyplot as plt; d = numpy.genfromtxt('step.csv', delimiter=',', names=True); plt.plot(d['t_s'] * 1e3, d['i_amps']); plt.xlabel('ms'); plt.ylabel('A'); plt.show()"
```

or open the CSV in anything that reads spreadsheets.

## When something misbehaves

- **`check` reports heartbeat STUCK** — the bitstream isn't loaded or isn't
  ours. Re-run deploy and watch its output; `fpgautil` prints an error if
  the .bit.bin is malformed (rebuild `make coil_servo.bit.bin`, don't scp
  the plain `.bit`).
- **`check`/tools can't connect** — the register server isn't running
  (deploy starts it, a board reboot kills it: re-run deploy) or the address
  in channels.toml is wrong. `ping rp-xxxxxx.local` first; if mDNS is
  flaky on your network, switch to a static IP.
- **`TimeoutError: FIFO filled only N/16384`** — the FPGA stopped feeding
  the capture FIFO; almost always the wrong bitstream. Redeploy.
- **State shows `TIMEOUT_HOLD`** — a flip (or graceful stop) couldn't reach
  the zero-current window within `flip_timeout`. Expected during early
  tuning if `zero_win` is too tight or the clamp stage isn't pulling.
  Nothing is broken: the loop is still servoing toward zero with the bridge
  closed. Acknowledge and resume per the recipe at the bottom of
  [register_map.md](register_map.md).
- **`sp_sign_mismatch` flag set** — the control computer's setpoint sign
  disagrees with the current bridge polarity (see design.md Node B). The
  loop safely sits at zero current until the signs agree.
- **Ethernet cable pulled / lab PC crashed mid-run** — the loop keeps
  running in the FPGA at the last register values, on purpose. Reconnect
  and carry on; the heartbeat/monostable and the hardware interlock are the
  actual protection layers.
