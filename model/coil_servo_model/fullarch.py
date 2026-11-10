"""LEGACY full-architecture model (Aug-Sep 2026): ~3 V hold rail + ~15 V
boost, per-FET source-sense pass bank, active clamp on OUT2.

This is NOT the hardware being built -- see channels.py / plant.py for the
MVP model (docs/mvp_architecture.md). Since MVP rewrite step 3 the HDL
benches and fixed-point tests run on the MVP plant; this file is kept only
for the channels.toml consistency test (host/tests/test_config.py), which
moves over in rewrite step 4. Delete it then.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FullArchChannel:
    name: str

    # --- Coil / circuit ---
    L: float                 # coil pair inductance [H] (Radia-computed)
    R_coil: float            # coil pair resistance [ohm] -- PROVISIONAL: Kelvin measurement pending
    R_extra: float           # pass bank on-state + cabling [ohm] -- PROVISIONAL estimate
    I_rated: float           # max operating current [A]

    # --- Rails ---
    V_rail: float = 3.0      # hold rail [V]
    V_boost: float = 15.0    # boost capacitor rail for fast ramps [V]
    V_clamp: float = 30.0    # active clamp pull-down voltage [V] -- PROVISIONAL (illustrative figure)
    V_ds_min: float = 0.5    # minimum pass-FET drop to stay linear [V] -- PROVISIONAL

    # --- Sensing ---
    # I_FS = current at +1.000 V on IN1; rated ~ 80% of full scale.
    I_FS: float = 0.0        # filled in __post_init__ if left 0

    # --- Pass bank / clamp actuator model -- ALL PROVISIONAL ---
    # Transconductance actuators: coil current tracks G_pass * V_out1 within
    # voltage compliance, G_pass scaled so +1 V commands I_FS; the clamp is
    # modelled the same way (pull-down, compliance V_clamp).
    G_pass: float = 0.0      # [A per normalized OUT1 unit], filled in __post_init__ if left 0
    G_clamp: float = 0.0     # ditto
    tau_inner: float = 1.6e-6  # inner analog loop time constant [s] -- PROVISIONAL
    clamp_is_voltage_mode: bool = False
    out2_enable: bool = True

    def __post_init__(self):
        if self.I_FS == 0.0:
            object.__setattr__(self, "I_FS", 1.25 * self.I_rated)
        if self.G_pass == 0.0:
            object.__setattr__(self, "G_pass", self.I_FS)   # +1 V -> I_FS
        if self.G_clamp == 0.0:
            object.__setattr__(self, "G_clamp", self.I_FS)

    @property
    def R_loop(self) -> float:
        return self.R_coil + self.R_extra

    @property
    def f_pole(self) -> float:
        return self.R_loop / (2.0 * math.pi * self.L)

    @property
    def amps_per_lsb(self) -> float:
        return self.I_FS / 8192.0

    @property
    def clamp_counts(self) -> int:
        """Hard output clamp in DAC counts = 100% of rated (review 2026-08-26)."""
        return round(self.I_rated / self.I_FS * 8192)


FULLARCH_CHANNELS = {
    "mot": FullArchChannel(name="mot", L=16e-6, R_coil=6.4e-3, R_extra=5.6e-3, I_rated=100.0),
    "z_shim": FullArchChannel(name="z_shim", L=29e-6, R_coil=11.6e-3, R_extra=6.0e-3, I_rated=60.0),
    "x_shim": FullArchChannel(name="x_shim", L=57e-6, R_coil=14.2e-3, R_extra=6.0e-3, I_rated=60.0),
    "y_shim": FullArchChannel(name="y_shim", L=57e-6, R_coil=14.2e-3, R_extra=6.0e-3, I_rated=60.0),
}


class FullArchPlant:
    """RL coil driven by a transconductance pass bank (OUT1) and active
    clamp (OUT2), drive frame (I >= 0). Voltage compliance:
      up:   dI/dt <= (V_avail - I*R_loop - V_ds_min) / L
      down: dI/dt >= -(V_clamp_avail + I*R_loop) / L
    V_avail = V_boost when the boost cap is switched in, else V_rail.
    """

    def __init__(self, channel: FullArchChannel, i0: float = 0.0):
        self.ch = channel
        self.I = float(i0)

    def step(self, v_out1: float, v_out2: float, boost: bool, dt: float,
             substeps: int = 8) -> float:
        ch = self.ch
        h = dt / substeps
        v_avail = ch.V_boost if boost else ch.V_rail

        for _ in range(substeps):
            I = self.I

            if v_out1 > 0.0 and v_out2 <= 0.0:
                target = ch.G_pass * v_out1
                dIdt = (target - I) / ch.tau_inner
                dIdt = min(dIdt, (v_avail - I * ch.R_loop - ch.V_ds_min) / ch.L)
                dIdt = max(dIdt, -(I * ch.R_loop) / ch.L)
            elif v_out2 > 0.0 and v_out1 <= 0.0:
                if ch.clamp_is_voltage_mode:
                    v_rev = ch.V_clamp * min(v_out2, 1.0)
                    dIdt = -(v_rev + I * ch.R_loop) / ch.L
                else:
                    target = I - ch.G_clamp * v_out2
                    dIdt = (target - I) / ch.tau_inner
                    dIdt = max(dIdt, -(ch.V_clamp + I * ch.R_loop) / ch.L)
                    dIdt = min(dIdt, -(I * ch.R_loop) / ch.L)
            elif v_out1 <= 0.0 and v_out2 <= 0.0:
                dIdt = -(I * ch.R_loop) / ch.L
            else:
                raise AssertionError(
                    "OUT1 and OUT2 driven simultaneously: mutual-exclusion "
                    "invariant violated upstream of the plant")

            self.I = I + h * dIdt

        return self.I
