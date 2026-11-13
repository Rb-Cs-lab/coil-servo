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
echo "=== failing setup paths (VIOLATED) ==="
awk '/^Slack \(VIOLATED\)/ {on=1; print ""; print; next}
     /^Slack/ {on=0}
     on && /^  (Source|Destination|Data Path Delay|Logic Levels):/ {print}' "$RPT" | head -40
