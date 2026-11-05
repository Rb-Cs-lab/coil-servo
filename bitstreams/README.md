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
same commit as the HDL change**, so the two never drift apart.

| File | Built from | Build host | Timing | Hardware-verified |
|---|---|---|---|---|
| coil_servo.bit.bin | HDL as of the 2026-08-27 bring-up (registered `u_drive` timing fix, `dio_invert` slice fix) | lab NUC, WSL2, Vivado 2025.1 | met | yes — MOT board, 2026-08-27 ([lab notes](../docs/lab_notes_2026-08-27.md)) |
