#!/bin/bash
# One-command clean bitstream build on the lab laptop (WSL2 Ubuntu 24.04,
# Vivado 2025.1), from the repo root or anywhere:
#
#   bash scripts/lab-build.sh            # build only
#   bash scripts/lab-build.sh DEST_DIR   # build, then copy coil_servo.bit.bin there
#
# A clean build every time: the build ID (register map STS word 6) is
# stamped into the project when tmp/coil_servo.xpr is created, so an old
# project directory would carry an old ID. The name has no shifted
# characters on purpose -- it can be typed through a remote-desktop session
# whose Shift key does not arrive.
# Prints the commit, the timing summary and the SHA-256 of the result; the
# full Vivado output goes to build.log.
set -e
cd "$(dirname "$0")/.."
source /opt/Xilinx/2025.1/Vivado/settings64.sh
echo "building from: $(git log --oneline -1)"
git status --short --untracked-files=no
make clean
make bit > build.log 2>&1 || { tail -40 build.log; echo "BUILD FAILED (see build.log)"; exit 1; }
make coil_servo.bit.bin >> build.log 2>&1
grep -A 8 "Design Timing Summary" tmp/coil_servo.runs/impl_1/*timing_summary_routed.rpt
sha256sum coil_servo.bit.bin
ls -l coil_servo.bit.bin
if [ -n "$1" ]; then
    cp coil_servo.bit.bin "$1"/ && echo "copied to $1"
fi
echo "BUILD DONE"
