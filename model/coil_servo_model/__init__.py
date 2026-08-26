"""Float reference model and fixed-point mirror for the coil current servo.

See docs/design.md for the signal-path contract this package implements.
"""

from .channels import CHANNELS, Channel
from .plant import Plant
from .pi import FloatPI, output_mux
from .fixed_point import (FixedPI, Decimator, adc_quantize, encode_gain,
                          decode_gain, output_mux_fixed, sat)
from .loop import run_loop, SimResult, T_S
from . import analysis

__all__ = [
    "CHANNELS", "Channel", "Plant", "FloatPI", "output_mux",
    "FixedPI", "Decimator", "adc_quantize", "encode_gain", "decode_gain",
    "output_mux_fixed", "sat", "run_loop", "SimResult", "T_S", "analysis",
]
