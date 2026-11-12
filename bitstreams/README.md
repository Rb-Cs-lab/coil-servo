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
| `coil_servo.bit.bin` | commit `275081b` (rev 5: zero-setpoint snap -- OUT1 to 0 at once on a zero setpoint, CFG ctrl b21 / word 23, STS b14; HDL identical to `704fd89`) | rev 5 | lab laptop, WSL2 Ubuntu 24.04, Vivado 2025.1, 2026-11-12 (`scripts/lab-build.sh`, clean build) | met -- WNS +0.003 ns, WHS +0.042 ns, 0 failing endpoints (thin setup margin; rev 4 had +0.156) | deployed on the MOT board 2026-11-12: check reports rev 5, commit 275081b; bench pulse tests with the snap on/off ([lab notes](../docs/lab_notes_2026-11.md)) |
| `coil_servo_rev4_20261111.bit.bin` | commit `103a127` (rev 4: IN1/IN2 zero-offset registers, build-ID register, open-loop integrator clear) | rev 4 | lab laptop, WSL2 Ubuntu 24.04, Vivado 2025.1, 2026-11-11 | met — WNS +0.156 ns, WHS +0.074 ns, 0 failing endpoints | deployed on the MOT board 2026-11-11: check reports rev 4, commit 103a127, configuration read back OK; Tests 1-3. Superseded |
| `coil_servo_rev3_20261111.bit.bin` | commit `aa2a92f1` (first MVP build) | rev 3 | lab laptop, 2026-11-11 | met — WNS +0.072 ns | yes — MOT board 2026-11-11: deploy/check, logic-board acceptance, loopback ([lab notes](../docs/lab_notes_2026-11.md)). Superseded |
| `coil_servo_fullarch_20260827.bit.bin` | HDL as of the 2026-08-27 bring-up (full architecture) | rev 0 | lab NUC, WSL2, Vivado 2025.1 | met | yes — MOT board, 2026-08-27. Superseded |

Since rev 4 the bitstream reports the commit it was built from (STS word 6);
`check` and `deploy` print it, e.g. "commit 275081b". The shared Google Drive folder "RP servo files" holds the same images (`coil_servo.bit.bin` = current, `coil_servo_v0` = rev 3, `coil_servo_v1` = rev 4); `scripts/lab-copy-to-drive.sh` puts a new laptop build there.

SHA-256 of the current `coil_servo.bit.bin`:
`f3533cc86ac406169d07322e8df301574d8a961cef2320fb1f3b9aae759517aa`
