# Fast turn-off: blocking diode + TVS, and opening the bridge at current

**Status (2026-11-17):** parts arrived. Installation and tests below.
Firmware and host support are written and simulated on branch `fw-rev6`
(register map rev 6). They are **not deployed yet**, and every new option
is **off** until the tests in section 3 pass.

Background and design reasoning: [future_upgrades.md](future_upgrades.md) §6.

## 1. Why

Today, turning the current down always keeps the H-bridge closed. The coil
current then circulates through one bridge switch and a body diode (the
diode built into every MOSFET). It decays slowly, pushed only by about
1 V: **~1.3 ms from 100 A** on the MOT. Opening the bridge instead would be
faster, but the coil current would then flow back into the supply's output
capacitors. That pumps the supply up and may trip its overvoltage
protection.

The two new parts give that current somewhere safe to go:

- A **blocking diode** in the supply's + lead lets current flow *from* the
  supply but not back *into* it.
- A **TVS** (a fast surge-absorbing diode that conducts sharply above its
  breakdown voltage, ~33 V here) across the bridge input absorbs the coil
  energy instead.

With the bridge opened, the coil sees about −35 to −50 V instead of −1 V:
**100 A → 0 in ~40 µs** on the MOT (model), and about 3 µs from 2 A on the
bench coil.

## 2. Installation

| Part | What it is |
|---|---|
| IXYS **DSS2x101-015A** | two 100 A / 150 V Schottky diodes in one SOT-227 package (isolated base) |
| Littelfuse **5KP30A** | 5 kW TVS, one-directional, V<sub>BR</sub> 33.3–36.8 V, clamps ≤ 48.4 V at 103 A; banded end = cathode |

**Fit both together. Never run with the diode and without the TVS.** With
the diode alone, the coil energy from a bridge opening (a heartbeat-latch
trip, for example) has nowhere to go. The rail bar would fly up toward the
transistors' 100 V rating.

### 2.1 Blocking diode — in the supply's + lead, at the rail bar

```
 supply + ══4/0══► [both ANODES strapped] DSS2x101 [both CATHODES strapped] ══short strap══► RAIL BAR
```

- Mount it next to the rail bar, on the bridge's water-cooled plate (thermal
  paste, #8-32 screws like the transistors). Its base is isolated.
- Use **both halves in parallel**: strap the two anode terminals together
  and the two cathode terminals together. On the bench one half would do,
  but at 100 A a single half would sit at its rating. Wire both now so
  nothing changes for Test 4.
- Take the terminal positions from the marking on the package or the
  datasheet drawing.
- **Remote-sense leads** (if the supply's are connected) and any future
  rail **hold capacitor** belong on the **supply side** of the diode. The
  clamp loop must contain only the TVS.
- **Cost:** about 0.5–0.6 V forward drop and ~60 W at 100 A (needs the
  water). The rail at the bridge becomes ~4.4 V. Compliance at 100 A
  stays sufficient (model: 100 A still reachable). The supply can be
  trimmed up by ~0.5 V if needed. At 3 A on the bench the drop is ~0.3 V
  and negligible.

### 2.2 TVS — straight across the bridge, rail bar to return bar

```
 RAIL BAR ──► |◄── RETURN BAR       (banded end = cathode on the RAIL bar)
            5KP30A
```

- Mount it between the **rail bar** and the **return bar** (the bridge's
  bottom bar, the one that goes to the pass bank's drain bar). Use leads as
  short as possible with ring lugs on the bar studs.
- **Not** to the bank's source bar or supply −. The shortest loop is: coil
  → body diode → rail bar → TVS → return bar → body diode → coil.
- The existing 5KP12CA across the pass bank, the coil varistors and the
  bench RC damper all stay where they are.

### 2.3 Checks before power (meter in diode-test mode, supply disconnected)

| Probes (red → black) | Expect | If not |
|---|---|---|
| supply-lead end of the diode → rail bar | 0.2–0.35 V | open: a strap or terminal is wrong |
| rail bar → supply-lead end | OL (or a slowly changing high reading: Schottky leakage) | ~0.3 V: diode is backwards |
| **rail bar → return bar** | **OL** | **~0.6 V: TVS is backwards — would short the bridge input. Do not power.** |
| return bar → rail bar | ~0.5–0.7 V (the TVS conducting forward, in parallel with two bridge body diodes) | |

## 3. Tests (bench, `mot_bench`, ≤ 2.2 A)

The scripts are prepared, not run yet. F0–F4 work with the **present rev-5
firmware**. F5 needs the rev-6 bitstream.

| | Test | Why | Expect |
|---|---|---|---|
| F0 | Meter checks (2.3) | wrong orientation of either part is destructive | as in the table |
| F1 | Power on, servo stopped: rail bar vs supply + with a DMM; re-zero IN1 (`python -m coil_servo_host.zero mot_bench --write --apply`) | nothing else changed; the zero must be right for the zero window | rail bar ≈ supply − ~0.2 V |
| F2 | Normal running, rev-5 behaviour: 2 A hold, setpoint → 0 (zero-setpoint snap, bridge closed), a ±2 A reversal. DMM across the diode during the hold | the diode must not change normal operation; its forward drop | hold as before; freewheel turn-off ≈ 170 µs as before; ~0.3 V drop |
| F3 | Bridge opened at 1 A and 2 A, **damper connected** (`f_open_trip.py damper_on`: over-current trip with `trip_timeout` 0) | first opening at current with the new parts; compare with 2026-11-12 (no diode/TVS) | current gone in ~25 µs (damper takes it, as before); scope: no spike on the supply's + terminal any more |
| F4 | Same, **damper temporarily disconnected** (`f_open_trip.py damper_off`) | at bench currents the damper takes the whole turn-off (it would need > ~10 A to drive its 3.2 Ω above the TVS), so the TVS is only seen without it | current gone in a few µs; scope (probe ground on supply 0 V, CH1 rail bar, CH2 return bar, math CH1−CH2): ~33–36 V for ~3 µs |
| F5 | Rev-6 options with the damper back: fast flip ±2 A (both orderings), fast stop, zero setpoint opens the bridge and re-closes, zero-open then flip (`f_rev6.py`) | the new firmware paths on hardware | flips limited by the zero-window hold-off and settle, not the 1 ms freewheel; bridge re-closes cleanly with no current surge |
| F6 | Heartbeat-latch trip at 2 A (pull the heartbeat) | the hardware trip now dumps into the TVS, not the supply | no supply spike; latch reset per the usual rule |
| Test 4 | At 100 A on the MOT: scope rail − return during snap-offs (≤ 48 V), TVS temperature after repeated snap-offs, diode drop and temperature, compliance; damper on the MOT pair | the real energy (~0.08 J coil + TVS share per event) | see future_upgrades.md §6 |

The scripts live with the session's other bench scripts for now:

- `f_open_trip.py` handles F3/F4.
- `f_rev6.py` handles F5.
- The F2 checks reuse the earlier reversal and snap scripts.

Plots go to `logs/testlog/img/`, as for Test 3.

**Damper note.** On the MOT pair (16 µH, proposed 22 µF + 1.6 Ω) the
damper's resistor limits how much current it can take at once: ~30 A at
the TVS voltage. So at 100 A most of the energy goes into the TVS, and the
damper soaks up the rest.

## 4. Firmware and host (register map rev 6, branch `fw-rev6`)

Three new settings in CFG word 0. All are reset to 0, and 0 gives exactly
today's behaviour. Full definitions are in
[register_map.md](register_map.md).

- **`fastoff_flip`**: a flip request opens the bridge at once (new state
  OPEN_RAMP) instead of servoing to zero with it closed. The polarity still
  changes only after the current is inside the zero window and the dead
  time has passed.
- **`fastoff_stop`**: the same for a stop.
- **`fastoff_zero`**: a zero setpoint (the same detection as the rev-5
  zero-setpoint snap) opens the bridge. The board waits in new state
  ZERO_OPEN, polarity unchanged. When the setpoint leaves zero, it
  re-closes the bridge only after the zero window and the dead time.

The safety properties are unchanged and checked in simulation:

- Both DACs are zero on the same clock the bridge opens.
- The polarity never changes with the bridge closed or within the dead
  time.
- A re-close never happens at current.
- If the current does not reach zero with the bridge open within
  `flip_timeout`, the board latches TRIPPED.

**Host.** `channels.toml` gets `fastoff_fitted` (a statement that the
hardware is installed) and the three options. The host **refuses** any
option unless `fastoff_fitted = true`. `python -m coil_servo_host.config
mot` shows the setting.

**With the hardware fitted**, the over-current trip can also use
`trip_timeout_us = 0` (open at once, energy into the TVS) — decide after
F4/F6.

**Deploying rev 6:**

1. Merge `fw-rev6`.
2. Build on the lab laptop: `bash scripts/lab-build.sh`, then
   `lab-copy-to-drive.sh`.
3. Deploy, then reset the logic-board latch (heartbeat present).

The host refuses to write to a rev-5 board once rev 6 is merged, so merge
and deploy together. The dashboard needs the two new state names and the
three switches (dashboard session).
