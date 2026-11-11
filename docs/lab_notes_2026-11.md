# Coil servo — MVP build and bring-up log (November 2026)

**Channel:** MOT | **Board:** Red Pitaya STEMlab 125-14 at 192.168.0.65
(wired) | **Power stage:** the MVP (docs/mvp_architecture.md) |
**Sources:** OneNote "Construction" notes (Santi + Jeff, 11-02 to 11-06)
and the design decisions of 11-10.

## Build log

- **11-02 — MOSFET thresholds.** V_GS(th) of all 10 pass-bank
  (IXTN200N10L2, at 3 mA) and 10 bridge (IXFN420N10T, at 8 mA) devices
  measured: gate tied to drain, series resistor from a bench supply,
  DMM across gate–source. Pass bank 3.35–3.56 V → installed #1, 2, 4, 7,
  9, 10 (3.50–3.56 V, 0.06 V spread → ≤ 12 % current-sharing spread at
  100 A). Bridge 3.51–3.59 V → installed #2, 5, 6, 9 (0.01 V spread).
  The SE-600-5 was reordered after the wrong model arrived.
- **11-03 — logic board on the MOT Red Pitaya** (still running the
  2026-08-27 bitstream). `pip install -e .` then `python -m
  coil_servo_host.check mot` **failed at first**: after a reboot the
  board had neither the bitstream nor the register server running. Fixed
  by hand over ssh:

  ```bash
  /opt/redpitaya/bin/fpgautil -b /root/coil_servo.bit.bin
  ```

  ```bash
  nohup python3 /root/coil_servo_server.py --port 9001 >/dev/null 2>&1 &
  ```

  After that `check` reported heartbeat ADVANCING, LED walk done,
  state IDLE, armed 0, fault 0, bridge_en 0, polarity 0, measured
  **+0.179 A** (at the old I_FS = 125 A: ≈ 1.4 mV IN1 offset, ≈ 0.29 A at
  the MVP's 200 A scale). The LED acceptance sequence passed
  (bringup.md 3a). In that test `servo_enable` was set from Python
  *after* arm was already high, and the old firmware went to RUN on the
  level — the MVP firmware needs an arm edge after `servo_enable`, so the
  recipe now enables first, then arms.
- **11-03 — monostable timing.** First built with 68 kΩ: the timeout was
  ~1.1 ms, about one heartbeat period, so a pulled heartbeat could
  recover on an edge. Changed to **220 kΩ / 100 nF: drop ≈ 7.6 ms,
  consistent**. (The CD74HC123 is specified at 5 V and times differently
  on the 3.3 V rail; trust the measurement.)
- **11-04 — cold plates.** Water-cooled plates machined (bridge: 4
  SOT-227; pass bank: 6 SOT-227 + 6 MP930). FETs mounted with thermal
  paste.
- **11-04 — pass bank assembled.** 12 AWG equal-length drain legs to the
  "input → drain" bus bar; gate/source leads and ballast resistors
  soldered; source legs to the "output → to source" bus bar; 5KP12CA
  between the bars; parts labelled; OPA994 perfboard with BNC input
  wired.
- **11-06 — H-bridge.** Assembly started; gate-drive pinout fixed
  (mvp_architecture.md section 4); bridge bus bars ordered.

## Decisions (11-10)

- **Turn-off keeps the bridge closed:** current goes down by freewheeling
  through the bridge body diodes (~1.3 ms from 100 A); the firmware never
  opens the bridge at current. (The bank TVS does not conduct in this
  path.) A faster snap-off later = blocking diode + TVS across the bridge
  input (future_upgrades.md section 6; parts to be ordered).
- **Dead-zone floor:** in RUN the PI output rests at ~90 % of the
  pass-bank dead zone, so steps from zero meet the 1 ms target.
- **Over-current trip at 100 A** (the supply's rating), on measured
  current.
- **Setpoint ramps enforced in firmware:** ≤ 200 A/ms up, ≤ 50 A/ms down
  (raw steps overshoot or undershoot on this plant) — except a target
  inside the ±0.5 A zero window, which is taken at once, so a step to
  zero is a full-speed freewheel snap-off (~1.3 ms from 100 A).
- **OUT1 dither** to recover resolution lost to the gain-8 gate stage.
- **DIO5 (fault input) not wired;** the Red Pitaya outputs nothing until
  enabled and armed (edge).

## Repo (MVP rewrite, brief section 11)

Steps 1–5 done on 11-10: architecture docs, model, firmware + cocotb
benches, host tools, documentation. The host now detects "no register
server" and prints the `deploy` command (the 11-03 problem), and `deploy`
checks the board comes up in its safe state. Auto-loading the bitstream
and server at boot (systemd) was **not** done: it would change what state
the board boots into — propose separately if wanted.

## Step 6 — MVP bitstream (11-11)

The NUC is no longer available; Vivado 2025.1 was installed on a lab
laptop under WSL2 (Ubuntu 24.04) following README "Setup B" (the libtinfo5
package had been revised to `6.3-2ubuntu0.3`; the Linux "Self Extracting
Web Installer" is needed, not the Windows one).

| Build | Result | Fix |
|---|---|---|
| 1 | WNS −2.169 ns, 232 failing endpoints: `slew_0` (−2.17), `pi_0` (−1.80), `fsm_0` (−1.03), two marginal | slew limiter split into measure/move phases; PI extra pipeline stage (registered sum, bounds, floor compares); FSM window/trip compares registered; dither output registered |
| 2 | WNS −0.015 ns, 1 endpoint (`slew_0/acc`) | rate choice registered in the measure phase |
| 3 | **WNS +0.072 ns, WHS +0.053 ns, 0 failing** | — |

Build 3 (from `aa2a92f1`) is `bitstreams/coil_servo.bit.bin`, SHA-256
`3e9f15a3…f416`. **Next:** first deploy with arm low and the power stage
off, then bring-up per [bringup.md](bringup.md).

## First deploy of the MVP bitstream (11-11)

Board only: power stage off, arm low, nothing on the SMA inputs.
`deploy mot` loaded the image (69 ms) and its post-check passed: IDLE,
OUT1 = 0, bridge disabled, not armed, register map rev 3. `check mot`:
heartbeat ADVANCING, board settings UNKNOWN before (expected after a load),
`mot` settings after, **configuration written and read back OK** (the
32-word CFG block works on hardware), OUT2/boost 0, dither on, state IDLE,
not armed, not tripped. Measured current at zero with nothing on IN1:
**+0.189 A** (≈ 1 mV, the Red Pitaya's own input offset; positive = the
safe direction for the trip). Re-measure with the LEM chain connected.

## Logic-board acceptance test with the MVP firmware (11-11) — latch fault found and fixed

Board only (power stage off). After a Red Pitaya power cycle the bitstream
was gone: no heartbeat (E1 pin 15 read 0.3 V instead of ~1.6 V average),
green off, latch held tripped — the safety chain working as designed.
Redeployed; heartbeat back.

- **Arm-edge rule confirmed on hardware:** arm jumper (pin 11) in *before*
  `servo_enable` = 1 → stays IDLE, amber off; unplug/replug → RUN, amber on.
- **Fault: the latch did not latch.** Pulling the heartbeat turned green
  and amber off but red stayed off, and re-plugging it brought amber back
  on by itself. Diagnosis by meter: the latch (74HC74 half 1) trips on a
  rising edge at CLK (pin 3) with D (pin 2) held high — a probe touch on
  pin 3 tripped it — and the monostable's 1Q̄ (CD74HC123 pin 4) rises when
  the heartbeat is lost, but **74HC74 pin 3 had no wire at all** (floating,
  ~0.4–0.6 V). Likely a wire that came loose after the 11-03 test.
- **Fix:** wire CD74HC123 pin 4 → 74HC74 pin 3. Re-test: pull heartbeat →
  green off, amber off, red on; re-plug → green on, red stays on, amber
  stays off. ✓
- **Design weakness to review (not fixed):** the trip is *edge*-triggered.
  Pressing reset while the heartbeat is still absent clears the latch, and
  when the heartbeat returns there is no new edge, so the bridge enables
  without another reset. A level-sensitive trip (monostable output on the
  latch's asynchronous preset/clear) would prevent that. Until reviewed:
  **only press reset with the heartbeat present (green on), and only after
  stopping the servo.**
- Measured on the way: CD74HC123 half 1 is the watchdog (pin 13 1Q = 3.3 V
  with heartbeat, 0 V without; pin 4 1Q̄ the inverse); half 2 idle.
- Corrected operating rule (also in all docs, commit 5493db89): stop the
  servo (`servo_enable` = 0, IDLE) before pressing reset — dropping arm
  alone does not stop a running servo.
- **Acceptance test passed** after the fix (all 8 steps of bringup.md 3a
  in the corrected order): arm-before-enable stays IDLE; arm edge → RUN;
  heartbeat pulled → red latched; re-plugged → red stays, amber off; stop →
  IDLE; reset → amber stays off; re-enable + arm edge → RUN; stop → IDLE.
  The logic board passes with the MVP firmware.
