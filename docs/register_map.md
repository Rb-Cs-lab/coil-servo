# Register map (implemented in `cores/coil_servo_top.v`)

Single source of truth for CFG/STS field layout.
`model/coil_servo_model/registers.py` mirrors this file (the integration
testbench and the host tools both use it); the HDL slices in
`coil_servo_top.v` follow this table. **Never invent offsets — change this
file first**, then registers.py, then the HDL (the integration bench catches
disagreement between the last two).

## Addressing

`axi_hub` on PS GP0 at physical **0x4000_0000** (mmap `/dev/mem`).
Address bits [27:24] select the hub port; bits [23:2] select a 32-bit word.

| Port | Base | Contents |
|---|---|---|
| 0 | 0x4000_0000 | CFG — write-only from PS, one parameter per 32-bit word |
| 1 | 0x4100_0000 | STS — read-only from PS |
| 2 | 0x4200_0000 | B02 BRAM: XADC readout (AIN0 temp, AIN1 rail) |
| stream 0 | — | S00 AXIS: raw ADC capture FIFO (HIL diagnostics) |

One word per parameter (CFG_DATA_WIDTH = 512, STS_DATA_WIDTH = 256):
wasteful of hub width, trivial to read in a debugger, immune to
straddled-field read/write tearing. All multi-bit fields LSB-aligned in
their word; unused high bits read/write as 0.

## CFG (0x4000_0000 + 4·word)

| Word | Name | Format | Reset | Meaning |
|---|---|---|---|---|
| 0 | `ctrl` | bits | 0 | b0 `servo_enable` (1→0 triggers a graceful ramp-to-zero stop, never an instant bridge drop); b1 `int_clear` (level; pulse from host); b2 `sp_source` (0 = IN2 analog, 1 = `setpoint` reg); b3 `fifo_rst`; b4 `out2_invert`; b5 `boost_mode` (0 = manual, 1 = auto); b6 `boost_manual`; b7 `flip_fault_ack`; b15:8 `led` |
| 1 | `setpoint` | s14 Q1.13 | 0 | register setpoint (counts of I_FS) |
| 2 | `kp_mant` | s18 | 0 | P gain mantissa |
| 3 | `kp_shift` | u5 | 0 | P gain right-shift |
| 4 | `ki_mant` | s18 | 0 | I gain mantissa (per-tick) |
| 5 | `ki_shift` | u6 | 0 | I accumulator right-shift |
| 6 | `out_clamp` | u14 Q1.13 | 0 | hard output clamp, counts (= 100 % of rated current; review decision 2026-08-26). Reset 0 ⇒ outputs forced 0 until configured — safe. |
| 7 | `deadband` | u14 Q1.13 | 0 | OUT1/OUT2 handoff deadband |
| 8 | `zero_win` | u14 Q1.13 | 0 | flip zero-current window (counts) — PROVISIONAL |
| 9 | `zero_holdoff` | u16 | 0 | decimated samples the window must hold |
| 10 | `deadtime` | u16 | 125 | bridge dead time, 8 ns ticks (125 = 1 µs) — PROVISIONAL |
| 11 | `settle` | u32 | 0 | post-flip settle, 8 ns ticks — eddy placeholder, PROVISIONAL |
| 12 | `flip_timeout` | u32 | 0 | RAMP_DOWN timeout, 8 ns ticks (0 = disabled) |
| 13 | `dio_invert` | bits | 0 | invert sense of E1 *inputs*: b0 flip request (DIO3), b1 arm (DIO4), b2 fault (DIO5). Reset 0 = as listed in the port table. Output polarities are fixed in HDL on purpose (reset state must be safe unconfigured). |
| 14 | — | u12 | 0 | reserved for `aout0_trim` (transducer offset trim via slow PWM output; wired up in session 7 when it can be validated against a voltmeter) |
| 15 | — | | | reserved |

## STS (0x4100_0000 + 4·word)

Signed fields are raw (LSB-aligned, upper bits zero): the host sign-extends
from the stated width (`registers.parse_sts` does this).

| Word | Name | Format | Meaning |
|---|---|---|---|
| 0 | `flags` | bits | b3:0 `fsm_state`; b4 `fault` (DIO5, read-only, no clear path); b5 `bridge_en`; b6 `polarity`; b7 `armed` (DIO4); b8 `out_sat` (clamp engaged); b9 `int_railed`; b10 `sp_sign_mismatch`; b11 `timeout_hold` |
| 1 | `i_meas` | s22 Q2.20 | decimated measured current (board frame) |
| 2 | `sp_active` | s14 | setpoint after mux (what the loop sees) |
| 3 | `u14` | s14 | PI output after the hard clamp |
| 4 | `heartbeat` | u32 | free-running counter (liveness readback) |
| 5 | `fifo_count` | u32 | ADC capture FIFO fill (added at the block-design level) |
| 6–7 | — | | reserved |

## FSM state encoding (`flags[3:0]`)

0 IDLE/RUN-disarmed · 1 RUN · 2 RAMP_DOWN · 3 DISABLE · 4 FLIP ·
5 ENABLE · 6 SETTLE · 7 TIMEOUT_HOLD (needs `flip_fault_ack`)
