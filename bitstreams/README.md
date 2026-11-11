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
| `coil_servo.bit.bin` | commit `103a127` (rev 4: IN1/IN2 zero-offset registers, build-ID register, open-loop integrator clear) | rev 4 | lab laptop, WSL2 Ubuntu 24.04, Vivado 2025.1, 2026-11-11 | met — WNS +0.156 ns, WHS +0.074 ns, 0 failing endpoints | not yet — deploy pending |
| `coil_servo_rev3_20261111.bit.bin` | commit `aa2a92f1` (first MVP build) | rev 3 | lab laptop, 2026-11-11 | met — WNS +0.072 ns | yes — MOT board 2026-11-11: deploy/check, logic-board acceptance, loopback ([lab notes](../docs/lab_notes_2026-11.md)). Superseded |
| `coil_servo_fullarch_20260827.bit.bin` | HDL as of the 2026-08-27 bring-up (full architecture) | rev 0 | lab NUC, WSL2, Vivado 2025.1 | met | yes — MOT board, 2026-08-27. Superseded |

Since rev 4 the bitstream reports the commit it was built from (STS word 6);
`check` and `deploy` print it, e.g. "commit 103a127".

SHA-256 of the current `coil_servo.bit.bin`:
`dd90d03b5380e3547b846d4496214fe2259717efca6e4f17350c680ba5afb5fb`
