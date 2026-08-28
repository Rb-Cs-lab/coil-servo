# Coil servo — operating scenarios in the experimental cycle

**Purpose:** a playbook for whoever programs the experimental sequences.
For each situation the machine will produce during ramping and experimental
cycles — normal and off-nominal — this describes what the control computer
does and what the servo board (FPGA firmware + power-stage commands) does in
response. Companion to [servo_interface_brief.md](servo_interface_brief.md)
(the electrical contract) and [design.md](design.md) (the loop design).

**All timing numbers here are PROVISIONAL** — they assume the illustrative
~30 V clamp, the ~15 V boost rail, and coil resistances that are still
pending Kelvin measurement. They are for budgeting shot timing, not for
gospel. The eddy-current settling time of the chamber is entirely unknown
and is called out wherever it matters.

## 1. Who does what (30-second recap)

| Actor | Role during a cycle |
|---|---|
| Control computer | Analog setpoint into IN2 (signed, same amps-per-volt as the sensor); pulses flip request (E1 pin 9); holds arm (pin 11) high during shots |
| Servo board | PI loop at 976.6 kS/s (one correction every 1.02 µs); H-bridge flip state machine; hard clamp at 100 % of rated current; boost-rail switching |
| Power stage | Pass bank sources current (commanded by OUT1); active clamp pulls it down (OUT2); H-bridge sets direction; boost cap gives ramp headroom |
| Interlock chain | Independent hardware protection; the board only *reports* its state (pin 13) |
| Heartbeat monostable | External circuit that kills bridge enable if the board's 954 Hz heartbeat (pin 15) stops |

## 2. Normal-operation scenarios

### S1 — Start of run: arm and enable

Control computer raises arm (pin 11); software (or a standing config) has
`servo_enable = 1`. Board: state IDLE → RUN, bridge enable (pin 5) goes
high, loop starts servoing the measured current onto whatever IN2 says.
If IN2 is at zero, the board holds zero actively (small corrections around
zero, outputs near code zero). Nothing conducts until the setpoint moves.

### S2 — Ramp to a setpoint (routine, e.g. MOT load field)

Control computer ramps the IN2 voltage. Board: the PI loop tracks it —
pass bank (OUT1) commanded up, integrator taking out the steady-state
error. On the hold rail (~3 V) alone the current can only approach its
natural limit exponentially (open-loop time constant L/R — ≈ 0.9 ms for the
MOT pair), so slow, small ramps need nothing special.

### S3 — Fast ramp (boost engaged)

When the requested slew exceeds what the hold rail can push
(dI/dt > (V_rail − I·R)/L), the boost cap (pin 7) provides the headroom.
In auto mode (the config default) the board closes the boost switch at the
start of each ramp-up from zero — at servo enable and at post-flip
re-enable — and opens it once the measured current first reaches ~87.5 %
of the setpoint; it never engages on ramp-*downs* (setpoint forced to
zero → boost forced off). **A large setpoint step in mid-run does not
re-engage auto boost** — such a ramp slews on the hold rail unless the
sequence switches to manual boost control around it (`boost_mode = 0`,
pin follows the `boost_manual` register bit). While boosted, the coil
charges at roughly dI/dt ≈ V_boost/L.
The hard clamp guarantees the *command* never exceeds 100 % of rated
current no matter how aggressive the setpoint ramp is; the loop simply
rides the clamp until the setpoint comes back within range (anti-windup
keeps the integrator honest during this, so there is no overshoot when it
un-clamps).

**Sequence tip:** you can command an instantaneous setpoint step and let
the servo slew at its physical maximum, or shape the analog ramp yourself
if you want a defined dI/dt (e.g. for adiabaticity). Both are fine; the
board follows whichever is slower — your ramp or physics.

### S4 — Steady hold

Pass bank only, boost off, integrator holding the operating point against
thermal drift of the coil resistance. Bench-measured stability class:
tail noise 17 mA rms on the loopback plant, on-target to 0.04 % — the real
coil will low-pass the DAC steps and do better. This is the "do nothing"
state; the loop needs no attention from the sequence.

### S5 — Fast turn-off (snap-off for time-of-flight / molasses)

Control computer steps IN2 to zero. Board: PI output swings negative, the
output multiplexer hands off from OUT1 to OUT2 (mutually exclusive, small
dead-band), and the active clamp absorbs the coil's stored energy
(½LI² ≈ 0.08 J for the MOT pair at 100 A) at roughly dI/dt ≈ V_clamp/L.
When the current reaches zero the loop settles back to holding zero. The
bridge stays closed and enabled throughout — a snap-off is *not* a flip.

### S6 — Field reversal (H-bridge flip)

The two-step choreography (full contract in the
[interface brief](servo_interface_brief.md)):

1. Control computer pulses flip request (pin 9, ≥ 1 µs).
2. Control computer swaps the IN2 sign any time while the flip runs.

Board sequence, autonomous once triggered: internally force the setpoint
to zero and ramp down (clamp does the work, as in S5) → wait for the
measured current to sit inside the zero window (default ±0.5 A) for
~66 µs → drop bridge enable → 1 µs dead time → toggle polarity (pin 3)
→ 1 µs dead time → re-enable bridge → resume servoing the (now
negative) setpoint. The polarity edge is guaranteed strictly inside the
enable-low window — verified on the scope during commissioning.

**Budget for the sequence:** electrical time ≈ ramp-down + 66 µs dwell +
2 µs bridge window + ramp-up (see the table in section 4 — order 0.3–0.5 ms
at full current). On top of that comes **chamber eddy-current settling,
which is unmeasured and may dominate** — leave a generous placeholder and
measure it with atoms or a probe coil before trusting fast reversals.

### S7 — Ramping *through* zero without a flip (deliberate behavior)

If the sequence ramps IN2 through zero to the other sign without pulsing
the flip line, the current follows it down to zero and **stays at zero**,
with the `sp_sign_mismatch` status flag raised — the bridge polarity can
only produce one direction of current, and the board will not flip on its
own. This is by design: a reversal is a disruptive, milliseconds-long
event the experiment must schedule explicitly, never a side effect of a
setpoint ramp crossing zero. If you meant it as a reversal, add the flip
pulse (S6); if the negative excursion was unintentional, nothing bad
happened — the field just clipped at zero.

### S8 — Between shots: dropping arm

Arm (pin 11) low between shots is fine and costs nothing: losing arm
mid-run does **not** drop the bridge or disturb the current — it only
blocks *new* bridge enables and flip requests. So a sequence can hold
current through an arm gap, but should re-assert arm before the next flip.

### S9 — End of day / controlled stop

Software sets `servo_enable = 0` (or the operator runs the shutdown
recipe). Board: graceful ramp-to-zero via the clamp, then bridge enable
drops. Never an instantaneous bridge-open at current. Power stages can
then be de-energized.

### S10 — Diagnostics (not during atom shots)

`check` / `watch` read status without disturbing the loop and are safe any
time. `step` and `sweep` (and open-loop mode) drive the coil for
characterization — bench and dummy-load use only; never in a sequence.
The capture FIFO can record any shot's current waveform at full rate for
post-shot inspection without affecting the loop.

## 3. Off-nominal scenarios

### F1 — Setpoint asks for more than rated current

The command clamps at 100 % of the channel rating and the `out_sat` flag
shows the loop is riding the limit. The current sits at rated until the
setpoint returns; anti-windup prevents any overshoot on the way back.
(The *measurement* range extends to 125 % of rated, so a real overshoot
from plant dynamics would still be seen and recorded.)

### F2 — Flip requested but current can't reach zero

If the measured current never sits inside the zero window (broken clamp,
shorted pass bank, sensor fault), the flip times out into **TIMEOUT_HOLD**:
bridge stays closed, board keeps servoing toward zero, no polarity change
happens. The shot is lost; the hardware is not. Recovery is a deliberate
operator acknowledgment (`b.pulse("flip_fault_ack")` — recipe in
[register_map.md](register_map.md)); the board will not retry on its own.

### F3 — Interlock trips mid-ramp

The interlock chain acts on the power stage directly — the board is not in
that path and cannot veto or clear it. The board *sees* the fault line
(pin 13), raises the `fault` status flag, and every measurement tool warns
loudly. The loop itself keeps computing (anti-windup prevents wind-up
while the plant is dead), so status readouts stay meaningful for
diagnosing what happened.

### F4 — Control computer or network dies mid-sequence

Nothing changes at the coil: the loop runs entirely in the FPGA and holds
its last configuration, and IN2 is analog — the board keeps following
whatever voltage is present. If the analog out also died to 0 V, that
reads as "setpoint zero" and the current ramps gracefully to zero (S5).
Protection against a *hung board* is not the network's job — see F5.

### F5 — The board itself dies (hang, accidental redeploy, reboot)

The heartbeat (pin 15) stops — verified on the bench, including deliberate
kill and recovery. The external retriggerable monostable must then gate
bridge enable off, and must *not* re-enable on its own when the heartbeat
returns (operator action required). **This monostable is still to be
built** — until it exists, never leave high current unattended.

### F6 — Power-on / boot with the power stage connected

During the ~1 s Linux boot the E1 pins are undefined; after the FPGA
loads, everything resets to off (bridge disabled, DACs zero, all outputs
low). The gate driver's own pull-downs (required in the interface brief)
cover the boot window. Consequence for operations: booting the Pitaya
with the power stage energized is safe *only* once those pull-downs exist.

### F7 — Glitches on the flip line

The flip input is synchronized and edge-detected; pulses must be ≥ 1 µs.
A genuine noise edge while armed would start a real (safe, but
shot-ruining) flip sequence — so route the flip line cleanly and keep it
disconnected/disarmed when not in use (internal pull-down = no request).

## 4. Timing budgets (PROVISIONAL — for shot planning only)

Assumes 15 V boost, 30 V clamp (illustrative), provisional resistances.
dI/dt ≈ V/L near zero current; ramps at high current are slightly slower
(up) / faster (down) by the I·R term.

| Channel | L | Rated | Boost ramp 0 → rated | Clamp ramp rated → 0 | Flip, electrical only |
|---|---|---|---|---|---|
| MOT anti-Helmholtz | 16 µH | 100 A | ~120 µs | ~55 µs | ~0.25 ms |
| Z shim | 29 µH | 60 A | ~120 µs | ~60 µs | ~0.25 ms |
| X / Y shim | 57 µH | 60 A | ~230 µs | ~115 µs | ~0.4 ms |

Flip electrical = clamp ramp + 66 µs zero dwell + 2 µs bridge window +
boost ramp back. **Add chamber eddy settling (unknown) on top.** The
loop's own settling (bench: ±1 % in 259 µs on a step) overlaps these
numbers, it doesn't add to them.

## 5. Open unknowns that shape sequence design

- **Eddy-current settling after flips/snap-offs** — unmeasured; dominates
  the usable reversal time budget. Measure early.
- **Clamp voltage** — sets every ramp-*down* number above; ~30 V is
  illustrative. Also: is the clamp rated for repeated ½LI² dumps at the
  planned duty cycle?
- **Boost cap recharge time** — repeated fast ramps within one shot are
  limited by how fast the cap recovers; ask the electronics designer.
- **PI gains** — current values are placeholders until the open-loop step
  response of the real coil is measured; loop settling numbers will move.
- **Coil resistances** — Kelvin measurement pending; affects hold-rail
  headroom and the high-current end of ramp rates.
