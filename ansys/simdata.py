"""Read a waveform CSV exported from Ansys (run_sims.py) into SI units."""
from __future__ import annotations

import csv
import os

import numpy as np

_PREFIX = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "meg": 1e6, "M": 1e6, "G": 1e9}


def _scale(unit: str, base: str) -> float:
    unit = unit.strip()
    if not unit:
        return 1.0
    pre = unit[: -len(base)] if unit.endswith(base) else unit
    return _PREFIX.get(pre, 1.0)


class Sim:
    """t in seconds; sim["net"] gives volts on that net; sim.var["name"] the design variables."""

    def __init__(self, path: str):
        with open(path, newline="") as f:
            header = next(csv.reader(f))
        data = np.genfromtxt(path, delimiter=",", skip_header=1)
        if data.ndim == 1:
            data = data[None, :]
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.var, self._v = {}, {}
        self.t = None
        for i, h in enumerate(header):
            label, _, unit = h.partition(" [")
            unit = unit.rstrip("]")
            if label == "Time":
                self.t = data[:, i] * _scale(unit, "s")
            elif label.startswith("V(") and label.endswith(")"):
                self._v[label[2:-1]] = data[:, i] * _scale(unit, "V")
            else:
                self.var[label] = (float(data[0, i]), unit)
        if self.t is None:
            raise ValueError(f"{path}: no Time column")

    def __getitem__(self, net: str) -> np.ndarray:
        return self._v[net]

    def nets(self):
        return sorted(self._v)

    def at(self, net: str, t: float) -> float:
        return float(np.interp(t, self.t, self._v[net]))

    def var_si(self, name: str) -> float:
        val, unit = self.var[name]
        for base in ("ohm", "Ohm", "H", "F", "V", "s", "A"):
            if unit.endswith(base):
                return val * _scale(unit, base)
        return val

    @property
    def i_coil(self) -> np.ndarray:
        """True coil current (amps), from the voltage across the coil's resistance."""
        return (self["coil_m"] - self["B"]) / self.var_si("R_coil")
