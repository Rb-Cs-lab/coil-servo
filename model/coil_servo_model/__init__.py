"""Float reference model and fixed-point mirror for the coil current servo.

See docs/design.md for the signal-path contract this package implements.
"""

from .channels import BENCH_CHANNELS, CHANNELS, Channel
from .plant import Plant
from .pi import FloatPI, output_mux
from .fixed_point import (FixedPI, Decimator, SigmaDelta, SlewLimiter, adc_quantize,
                          drive_frame_error, encode_gain, decode_gain,
                          offset_correct, output_mux_fixed, sat, slew_rate_code)
from .loop import run_loop, SimResult, T_S
from . import analysis
from . import registers

__all__ = [
    "CHANNELS", "BENCH_CHANNELS", "Channel", "Plant", "FloatPI", "output_mux",
    "FixedPI", "Decimator", "SigmaDelta", "SlewLimiter", "slew_rate_code",
    "adc_quantize", "drive_frame_error",
    "encode_gain", "decode_gain",
    "output_mux_fixed", "sat", "run_loop", "SimResult", "T_S", "analysis",
]
