"""pytest setup for the HDL simulations: make sure Icarus Verilog is on PATH.

On lab Windows machines the winget install lands in C:\\iverilog; on Linux
`apt install iverilog` puts it on PATH already.
"""

import os
import shutil
from pathlib import Path

_CANDIDATES = [r"C:\iverilog\bin", r"C:\Program Files\Icarus Verilog\bin"]

if shutil.which("iverilog") is None:
    for cand in _CANDIDATES:
        if (Path(cand) / "iverilog.exe").exists():
            os.environ["PATH"] = cand + os.pathsep + os.environ["PATH"]
            break
