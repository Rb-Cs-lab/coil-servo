# Coil servo — operating scenarios in the experimental cycle (MVP)

**Purpose:** a playbook for whoever programs the experimental sequences.
For each situation the machine will produce during ramping and experimental
cycles — normal and off-nominal — this describes what the control computer
does and what the servo board (FPGA firmware) and the MVP power stage do in
response. Companion to [servo_interface_brief.md](servo_interface_brief.md)
(the electrical contract), [mvp_architecture.md](mvp_architecture.md) (the
power stage as built) and [register_map.md](register_map.md) (every
setting).

*Rewritten 2026-11-10 (MVP rewrite step 5) for the power stage as built:
one 5 V rail, no boost, no active clamp — current goes down by
freewheeling through the bridge.*

**Timing numbers are for the MOT channel and PROVISIONAL** — they come
from the model with provisional coil/cable resistances and the datasheet
g_fs. The chamber's eddy-current settling time is entirely unknown and is
called out wherever it matters.

## 1. Who does what

| Actor | Role during a cycle |
|---|---|
| Control computer | Analog setpoint into IN2 (signed, 20 A per volt at the computer); pulses flip request (E1 pin 9); raises arm (pin 11) to start |
| Servo board (FPGA) | PI loop (one correction every 1.02 µs); setpoint ramp limits (≤ 200 A/ms up, ≤ 50 A/ms down); over-current trip (100 A); H-bridge flip sequence; heartbeat |
| Pass bank | Low-side linear MOSFET bank driven by OUT1 through the gain-8 op-amp stage: **can only source current or source less** |
| H-bridge | Sets the current direction; when the bank throttles, the coil current **freewheels** through one switch and a body diode — that is how current goes down |
| Logic board | The only automatic hardware trip: no heartbeat → bridge drivers disabled and latched until someone presses reset |
| SE-600-5 supply | 5 V / 100 A; its own over-current protection is the backup current limit |

## 2. Normal operation

### S1 — Start of run: enable, then arm

1. The latch on the logic board must be reset (red LED off, green on).
2. The host sets `servo_enable = 1` (standing configuration; `step` and
   `sweep` do it themselves).
3. **Then** the control computer raises arm (pin 11). The board needs a
   rising *edge* of arm while `servo_enable` is already set — an arm line
   that was high beforehand (or high when the bitstream loaded, or high
   through a trip) does nothing until it is dropped and raised again.

Board: IDLE → RUN, bridge enable (pin 5) goes high, and OUT1 rests at the
**floor** — about 90 % of the pass bank's dead zone, just below where any
current flows. Nothing conducts until the setpoint moves.

### S2 — Ramp to a setpoint

The control computer steps or ramps IN2. The firmware passes the setpoint
on at no more than **200 A/ms** (away from zero), and the loop tracks it:
0 → 50 A takes 0.25 ms, then settles to 0.1 % within ~1 ms. On the 5 V
rail the coil could rise slightly faster (~0.3 A/µs at zero current,
~0.17 A/µs near 100 A), so ramps are paced by the firmware limit, not
physics. **Why the limit exists:** a raw step to ~55–95 A would overshoot
by up to ~20 % (the integrator winds up while the 5 V rail limits the
rise); the ramp removes that completely.

**Sequence tip:** step the setpoint if you just want it there fast; shape
the analog ramp yourself only if you want something *slower* than
200 A/ms (e.g. adiabatic ramps). The board follows whichever is slower.

### S3 — Steady hold

The integrator holds the operating point against slow drifts (coil
warming, FET threshold drift). With the OUT1 dither on (configuration
default) the simulated hold sits within one ADC count (24 mA) of the
setpoint; the real figure is a bench measurement. Keep holds **at or
below ~98 A**: the over-current trip is at 100 A and noise at exactly
100 A would trip it.

**Uncooled first test round:** no long holds — short pulses at reduced
current within the written duty limit (each pass FET dissipates ~50 W at
100 A).

### S4 — Controlled down-ramp to a lower current

Control computer lowers IN2. The firmware passes the setpoint on at no
more than **50 A/ms** toward zero; the coil freewheels down at least that
fast at these currents, so the current follows the ramp with no
undershoot. (A raw step down, e.g. 80 → 20 A, would undershoot towards
zero before recovering — the reason for the limit.)

### S5 — Turn-off (snap to zero for time-of-flight / molasses)

Control computer steps IN2 to zero. A target inside the zero window
(±0.5 A) bypasses the 50 A/ms limit, so the setpoint snaps to zero at
once, OUT1 drops below the dead zone, and the coil **freewheels at full
speed: ~1.3 ms from 100 A, ~1.1 ms from 80 A**. The bridge stays closed
and enabled — a turn-off is not a flip. (The down-limit exists to
prevent undershoot, which cannot happen at a zero target. A ramp *to*
zero that you shape yourself is followed as usual.)

The fastest turn-off the MVP hardware can do is the freewheel; a much
faster snap-off (~50 µs) needs the blocking diode + TVS upgrade
(future_upgrades.md section 6) and a firmware change once those parts
are fitted.

### S6 — Field reversal (H-bridge flip)

The two-step choreography (full contract in the
[interface brief](servo_interface_brief.md)):

1. Control computer pulses flip request (pin 9, ≥ 1 µs; only while armed
   and in RUN).
2. Control computer swaps the IN2 sign any time while the flip runs.

Board sequence, autonomous once triggered:

1. Force the setpoint to zero. This bypasses the 50 A/ms limit; OUT1
   drops to zero and the coil freewheels down (~0.8 ms from 50 A, ~1.3 ms
   from 100 A).
2. Wait for the measured current to stay inside the zero window
   (±0.5 A) for ~66 µs.
3. Drop bridge enable, wait the 1 µs dead time, toggle polarity (pin 3),
   wait another 1 µs, re-enable the bridge. The polarity edge always
   falls strictly inside the enable-low window.
4. Wait the settle delay (100 µs placeholder for chamber eddy currents).
5. Ramp up to the new (sign-swapped) setpoint at 200 A/ms.

**Budget:** see section 4 — about 1.2 ms for a 50 A reversal, plus
**chamber eddy-current settling, which is unmeasured and may dominate**.

### S7 — Ramping *through* zero without a flip (deliberate behaviour)

If the sequence ramps IN2 through zero to the other sign without pulsing
the flip line, the current follows it down to zero and **stays at zero**,
with the `sp_sign_mismatch` status flag raised — the bridge polarity can
only produce one direction of current, and the board never flips on its
own. If you meant a reversal, add the flip pulse (S6); if not, nothing
bad happened — the field just clipped at zero.

### S8 — Between shots: dropping arm

Arm low between shots is fine: losing arm mid-run does **not** drop the
bridge or disturb the current — it only blocks flip requests (and, in
IDLE, starting). Re-raise arm before the next flip.

### S9 — End of day / controlled stop

The host sets `servo_enable = 0`. Board: setpoint forced to zero, the coil
freewheels down, and the bridge opens once the current is inside the zero
window — never an instantaneous bridge-open at current. Then the power
stage can be switched off. To run again later: `servo_enable = 1`, then a
fresh arm edge (S1).

### S10 — Diagnostics (not during atom shots)

`check` and `watch` only read status and are safe any time. `step` and
`sweep` drive the coil for characterization — bench and dummy-load use
only, never in a sequence. In **open-loop** mode OUT1 is a gate command,
not a current: `step --open-loop --amps X` converts X through the model
(PROVISIONAL), and `sweep` needs the function generator's offset above the
dead zone. The capture FIFO can record any shot's current waveform for
post-shot inspection without affecting the loop.

## 3. Off-nominal scenarios

### F1 — Setpoint asks for more than rated / over-current trip

If the measured current exceeds **100 A** for ~4 µs, the firmware trips:
both outputs drop to zero on the next clock, the bridge stays closed
while the coil freewheels down (~1.3 ms), then opens once the current is
near zero (or after 3 ms regardless) and latches in **TRIPPED**. Every
host tool warns. To resume: find out why, `Board.pulse("trip_ack")`, then
drop and re-raise arm. The OUT1 clamp (the command limit, set to ask for
~100 A) and the supply's over-current protection sit behind the trip.

### F2 — Flip requested but current can't reach zero

If the current never sits inside the zero window (e.g. a shorted pass FET
or a sensor fault), the flip times out (20 ms) into **TIMEOUT_HOLD**: the
bridge stays closed, the board keeps pulling toward zero, and the
polarity does not change. The shot is lost; the hardware is not. Recovery
is a deliberate acknowledgment (`b.pulse("flip_fault_ack")`, recipe in
[register_map.md](register_map.md)). A shorted pass FET would also push
the current up to the trip level (F1).

### F3 — Control computer or network dies mid-sequence

Nothing changes at the coil: the loop runs in the FPGA, and IN2 is
analog — the board keeps following whatever voltage is present. If the
analog output died to 0 V, that reads as "setpoint zero" and the current
ramps down (S5).

### F4 — Heartbeat trip (Red Pitaya dies, hangs, reboots or is redeployed)

The heartbeat (pin 15) stops, the monostable on the logic board drops
after ~7.6 ms, and the latch disables the bridge drivers. **This opens the
bridge at whatever current is flowing:** the coil current is pushed back
into the supply through the body diodes (~0.2 ms from 100 A), which may
trip the SE-600-5's over-voltage protection (open question). The latch
stays tripped until someone presses reset — it never re-arms on its own.

**If the heartbeat dropped but the Red Pitaya kept running** (a loose
DIO6 wire), the firmware never learns the bridge opened: it keeps
integrating against zero current and its output may wind up to the clamp.
**Operating rule: disarm (arm low) before pressing the latch reset**, and
set `servo_enable = 0` if in doubt — then re-enable and re-arm (S1).

### F5 — Power-on / boot with the power stage connected

The logic board's latch powers up **tripped**, and every E1 line has a
10 kΩ pull-down, so the bridge stays disabled through the Red Pitaya's
~1 s Linux boot. After a reboot the bitstream and register server are not
running and there is no heartbeat, so the latch stays tripped. Order:
deploy (which checks the board comes up IDLE, OUT1 = 0, bridge off,
disarmed), `check`, then reset the latch, then S1.

### F6 — Glitches on the flip line

The flip input is synchronized and edge-detected; pulses must be
≥ 1 µs. A genuine noise edge while armed would start a real (safe, but
shot-ruining) flip sequence — route the flip line cleanly and keep it
disconnected or disarmed when not in use.

### F7 — Supply protection trips

The SE-600-5's over-current or over-voltage protection may shut it down
(F1 backup, F4 energy return). The current then decays with the bridge
still closed and the servo sees the current fall away; its output rides
up to the clamp (anti-windup keeps the integrator there). **Set
`servo_enable = 0` first** — otherwise the returning supply meets OUT1 at
the clamp and the current jumps to the trip level — then power-cycle the
supply per its manual, re-enable and re-arm.

## 4. Timing budget (MOT channel, PROVISIONAL — for shot planning)

| Move | Time | Set by |
|---|---|---|
| Ramp up 0 → 50 A / 0 → 100 A | 0.25 ms / 0.5 ms, then settle ~1 ms | Firmware limit 200 A/ms (physics allows ~0.4 ms to 100 A) |
| Ramp down 100 → 50 A (IN2) | 1 ms | Firmware limit 50 A/ms |
| Snap to zero (IN2 → 0, or flip, stop, trip) from 50 A / 100 A | ~0.8 ms / ~1.3 ms | Body-diode freewheel |
| Flip at 50 A, electrical | ~1.2 ms = 0.8 ms freewheel + ~70 µs zero check + 2 µs bridge window + 0.1 ms settle (placeholder) + 0.25 ms ramp up | FSM + physics |
| Flip at 100 A, electrical | ~2 ms | same |

**Add chamber eddy settling (unknown) on top of every flip and snap-off.**
Shims (placeholders until their stages exist): the freewheel from 60 A
takes ~1.4 ms (Z) and ~2.7 ms (X/Y).

## 5. Open items that shape sequence design

- **Eddy-current settling after flips and snap-offs** — unmeasured;
  dominates the usable reversal budget. Measure early (`settle_us`).
- **PI gains** — loopback values rescaled by the model's plant gain;
  set them from the measured step response.
- **Bridge-open-at-current** (F4) — does the supply trip on the returned
  energy?
- **Hold noise** with the gain-8 stage and dither, against the 0.1 %
  spec.
- **Uncooled duty limit** for the first test round.
