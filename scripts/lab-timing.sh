#!/bin/bash
# After scripts/lab-build.sh: print the worst setup paths from the routed
# timing report (source, destination, slack, logic levels), so a timing
# failure can be located without opening Vivado. Shift-free name on
# purpose (see lab-build.sh).
cd "$(dirname "$0")/.."
RPT=$(ls tmp/coil_servo.runs/impl_1/*timing_summary_routed.rpt | head -1)
echo "report: $RPT"
grep -A 6 "Design Timing Summary" "$RPT" | tail -3
echo
echo "=== worst setup paths ==="
awk '/^Max Delay Paths/{on=1} on && /^Min Delay Paths/{exit}
     on && /^Slack/ {print ""; print}
     on && /^  Source:/ {print}
     on && /^  Destination:/ {print}
     on && /^  Data Path Delay:/ {print}
     on && /^  Logic Levels:/ {print}' "$RPT" | head -60
