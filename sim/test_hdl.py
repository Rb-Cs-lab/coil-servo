"""pytest entry points that build each core with Icarus Verilog and run its
cocotb bench. Run with plain `pytest` from the repo root (see README)."""

import os
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM_DIR = Path(__file__).resolve().parent
ROOT = SIM_DIR.parent


def run_bench(toplevel: str, test_module: str):
    runner = get_runner("icarus")
    build_dir = SIM_DIR / "sim_build" / toplevel
    runner.build(
        sources=[ROOT / "cores" / f"{toplevel}.v"],
        hdl_toplevel=toplevel,
        build_dir=build_dir,
        build_args=["-g2012"],
        always=True,
    )
    # the simulator-embedded Python must find the tb_* modules
    os.environ["PYTHONPATH"] = (
        str(SIM_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    runner.test(hdl_toplevel=toplevel, test_module=test_module,
                build_dir=build_dir)


def test_servo_decimator():
    run_bench("servo_decimator", "tb_servo_decimator")


def test_servo_pi():
    run_bench("servo_pi", "tb_servo_pi")
