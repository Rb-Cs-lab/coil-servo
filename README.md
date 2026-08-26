# coil-servo

Digital current servo for the magnetic field coils of a dual-species Rb–Cs
ultracold atoms machine, running on Red Pitaya STEMlab 125-14 (one board per
coil channel). The PI loop and H-bridge flip state machine live in the FPGA
fabric; Python handles tuning, transfer-function sweeps, diagnostics, and
configuration.

- [BOOTSTRAP.md](BOOTSTRAP.md) — full context, requirements, and safety invariants
- [CLAUDE.md](CLAUDE.md) — hardware facts, toolchain, register map, make targets

Built on the Tcl/Makefile FPGA flow from
[pavel-demin/red-pitaya-notes](https://github.com/pavel-demin/red-pitaya-notes)
(tag `20251012`, MIT), stripped to the bitstream build; the board runs the
official Red Pitaya OS 3.x. Requires Vivado 2025.1 on Ubuntu 24.04/22.04 to
build; simulation (cocotb) and the Python model run anywhere.
