"""pytest entry points that build each core with Icarus Verilog and run its
cocotb bench. Run with plain `pytest` from the repo root (see README)."""

import os
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM_DIR = Path(__file__).resolve().parent
ROOT = SIM_DIR.parent


def run_bench(toplevel: str, test_module: str, parameters=None, sources=None):
    runner = get_runner("icarus")
    build_dir = SIM_DIR / "sim_build" / toplevel
    runner.build(
        sources=sources or [ROOT / "modules" / f"{toplevel}.v"],
        hdl_toplevel=toplevel,
        build_dir=build_dir,
        build_args=["-g2012"],
        parameters=parameters or {},
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


def test_servo_output_mux():
    run_bench("servo_output_mux", "tb_servo_output_mux")


def test_servo_error():
    run_bench("servo_error", "tb_servo_error")


def test_servo_heartbeat():
    # DIV_LOG2=6 (toggle every 64 clocks) so the bench sees many periods fast
    run_bench("servo_heartbeat", "tb_servo_heartbeat", parameters={"DIV_LOG2": 6})


def test_servo_flip_fsm():
    run_bench("servo_flip_fsm", "tb_servo_flip_fsm")


def test_coil_servo_top():
    # the integration top: cores/coil_servo_top.v + every submodule
    sources = [ROOT / "cores" / "coil_servo_top.v"]
    sources += sorted((ROOT / "modules").glob("servo_*.v"))
    run_bench("coil_servo_top", "tb_coil_servo_top",
              parameters={"HB_DIV_LOG2": 6}, sources=sources)
