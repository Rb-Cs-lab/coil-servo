# Prebuilt bitstreams

`coil_servo.bit.bin` here is a ready-to-load FPGA image, so you can deploy
to a board without installing Vivado:

```bash
python -m coil_servo_host.deploy mot --bitstream bitstreams/coil_servo.bit.bin
```

The bitstream is the compiled form of the Verilog in `modules/` and
`cores/` (see the README for what that means). It is only valid for the
source it was built from — **if you change any HDL, rebuild it on the
Vivado machine (`make coil_servo.bit.bin`) and replace the file here in the
same commit as the HDL change**, so the two never drift apart. A build is
only shipped when Vivado reports timing met: WNS ≥ 0 and 0 failing
endpoints (`grep -A 6 "Design Timing Summary"
tmp/coil_servo.runs/impl_1/*timing_summary_routed.rpt`).

The host tools check that the board runs the register-map revision they
expect (STS word 0 bits 31:24, `registers.MAP_REV`) and refuse to configure
any other — so the August image can only be read, not configured, by the
current host tools.

| File | Built from | Register map | Build host | Timing | Hardware-verified |
|---|---|---|---|---|---|
| `coil_servo.bit.bin` | commit `aa2a92f1` (MVP rewrite: floor, trip, slew limiter with snap, dither, arm edge; timing pipelining) | rev 3 | lab laptop, WSL2 Ubuntu 24.04, Vivado 2025.1, 2026-11-11 | met — WNS +0.072 ns, WHS +0.053 ns, 0 failing endpoints | not yet — first deploy pending ([bringup.md](../docs/bringup.md)) |
| `coil_servo_fullarch_20260827.bit.bin` | HDL as of the 2026-08-27 bring-up (full architecture: OUT2 clamp, boost; registered `u_drive` timing fix, `dio_invert` slice fix) | rev 0 (no revision field) | lab NUC, WSL2, Vivado 2025.1 | met | yes — MOT board, 2026-08-27 ([lab notes](../docs/lab_notes_2026-08-27.md)). Superseded; kept for reference |

SHA-256 of the current `coil_servo.bit.bin`:
`3e9f15a38d27ac19b1ba4eed19bc7b5f8e800add9b8115be7f281f72094cf416`
