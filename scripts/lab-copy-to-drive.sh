#!/bin/bash
# After scripts/lab-build.sh on the lab laptop: put the new coil_servo.bit.bin
# into the shared Google Drive folder the lab PC deploys from, keeping the
# previous image under a versioned name (the lab's convention:
# coil_servo_v0.bit.bin = rev 3, coil_servo_v1.bit.bin = rev 4, ...).
#
#   bash scripts/lab-copy-to-drive.sh [OLD_NAME]
#
# Without OLD_NAME the previous image gets the next free version number
# (coil_servo_v2.bit.bin, v3, ...).
#
# Uses Windows PowerShell from WSL, because Google Drive for desktop's G:
# drive is not mounted inside WSL. The rename happens locally first, so the
# new file can never overwrite the old one before Drive has synced.
set -e
cd "$(dirname "$0")/.."
OLD_NAME="${1:-auto}"
DEST='G:\My Drive\RbCs\High current coils\RP servo files'
SRC="$(wslpath -w coil_servo.bit.bin)"
[ -f coil_servo.bit.bin ] || { echo "no coil_servo.bit.bin -- run scripts/lab-build.sh first"; exit 1; }
powershell.exe -NoProfile -Command "
\$ErrorActionPreference = 'Stop'
\$d = '$DEST'
\$cur = Join-Path \$d 'coil_servo.bit.bin'
\$oldName = '$OLD_NAME'
if (\$oldName -eq 'auto') {
  \$n = 0
  while (Test-Path -LiteralPath (Join-Path \$d \"coil_servo_v\$n.bit.bin\")) { \$n++ }
  \$oldName = \"coil_servo_v\$n.bit.bin\"
}
\$old = Join-Path \$d \$oldName
if (Test-Path -LiteralPath \$old) { throw \"\$old already exists -- pick another OLD_NAME\" }
if (Test-Path -LiteralPath \$cur) {
  Rename-Item -LiteralPath \$cur -NewName \$oldName
  Write-Output \"renamed the previous coil_servo.bit.bin to \$oldName\"
}
Copy-Item -LiteralPath '$SRC' -Destination \$cur
Get-ChildItem -LiteralPath \$d | Format-Table Name, Length, LastWriteTime -AutoSize
"
echo "COPY DONE"
