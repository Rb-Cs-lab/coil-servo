"""Closed-loop simulator: plant + controller + output stage, per channel.

Two controller flavours share the same plant and timing:
  * "float" -- FloatPI, the trusted reference (BOOTSTRAP deliverable 3).
  * "fixed" -- FixedPI, the bit-exact mirror the HDL is checked against.

Timing: PI tick T_S = 128 / 125 MHz = 1.024 us (decimation ratio 128). The
plant integrates with Euler substeps inside each tick. In this closed-loop
model the fixed-point path quantizes the error once per tick and scales by
the decimation ratio (e22 = e15 << 7), i.e. it assumes the error is constant
across the 128 fast samples of one tick -- exact for the test waveforms used
here; the cocotb bench drives the real decimator with fast samples instead.
"""

from dataclasses import dataclass, field

import numpy as np

from .channels import Channel
from .pi import FloatPI, output_mux
from .fixed_point import FixedPI, adc_quantize, output_mux_fixed

T_S = 128 / 125e6   # PI tick [s]


@dataclass
class SimResult:
    t: np.ndarray        # time [s], one point per PI tick
    I: np.ndarray        # coil current [A]
    u: np.ndarray        # controller output, normalized (post-clamp)
    v1: np.ndarray       # OUT1 [V]
    v2: np.ndarray       # OUT2 [V]
    integrator: np.ndarray  # integrator state, normalized

    def final_current(self) -> float:
        return float(self.I[-1])


def run_loop(channel: Channel, controller, deadband_counts: int,
             t_end: float, setpoint_fn, boost_fn=None,
             plant=None, substeps: int = 8) -> SimResult:
    """Run the closed loop for t_end seconds.

    controller: a FloatPI or FixedPI instance (deduced by type).
    setpoint_fn(t) -> setpoint in AMPS (drive frame).
    boost_fn(t) -> bool, boost cap engaged (default: never).
    """
    from .plant import Plant
    if plant is None:
        plant = Plant(channel)
    if boost_fn is None:
        boost_fn = lambda t: False
    fixed = isinstance(controller, FixedPI)
    deadband_norm = deadband_counts / 8192.0

    n = int(round(t_end / T_S))
    out = SimResult(*(np.zeros(n) for _ in range(6)))

    for k in range(n):
        t = k * T_S
        sp_amps = setpoint_fn(t)

        if fixed:
            sp_code = adc_quantize(sp_amps / channel.I_FS)
            meas_code = adc_quantize(plant.I / channel.I_FS)
            e15 = sp_code - meas_code            # s14 - s14 -> s15
            e22 = e15 << 7                        # constant over the tick
            u14 = controller.step(e22)
            o1, o2 = output_mux_fixed(u14, deadband_counts)
            u_norm, v1, v2 = u14 / 8192.0, o1 / 8192.0, o2 / 8192.0
            integ = (controller.acc >> controller.ki_shift) / (1 << 20)
        else:
            e = (sp_amps - plant.I) / channel.I_FS
            u_norm = controller.step(e)
            v1, v2 = output_mux(u_norm, deadband_norm)
            integ = controller.integrator

        plant.step(v1, v2, boost_fn(t), T_S, substeps)

        out.t[k] = t
        out.I[k] = plant.I
        out.u[k] = u_norm
        out.v1[k] = v1
        out.v2[k] = v2
        out.integrator[k] = integ

    return out
