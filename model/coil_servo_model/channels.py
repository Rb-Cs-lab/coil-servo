"""Per-channel parameters for the four coil servo channels.

This is the single Python source for plant and scaling numbers. Values marked
PROVISIONAL are computed/assumed, not measured -- see "Open unknowns" in
BOOTSTRAP.md. The runtime configuration the boards actually receive lives in
host/config/channels.toml (session 7) and must stay consistent with this file.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Channel:
    name: str

    # --- Coil / circuit (BOOTSTRAP plant table) ---
    L: float                 # coil pair inductance [H] (Radia-computed)
    R_coil: float            # coil pair resistance [ohm] -- PROVISIONAL: Kelvin measurement pending
    R_extra: float           # pass bank on-state + cabling [ohm] -- PROVISIONAL estimate
    I_rated: float           # max operating current [A]

    # --- Rails (BOOTSTRAP) ---
    V_rail: float = 3.0      # hold rail [V]
    V_boost: float = 15.0    # boost capacitor rail for fast ramps [V]
    V_clamp: float = 30.0    # active clamp pull-down voltage [V] -- PROVISIONAL (illustrative figure)
    V_ds_min: float = 0.5    # minimum pass-FET drop to stay linear [V] -- PROVISIONAL

    # --- Sensing (design.md Node 0) ---
    # I_FS = current at +1.000 V on IN1 = 2000 / (R_M * G_ia).
    # DECIDED: rated ~ 80% of full scale => I_FS = 1.25 * I_rated.
    # PROVISIONAL until the burden resistor R_M is chosen for real.
    I_FS: float = 0.0        # filled in __post_init__ if left 0

    # --- Pass bank / clamp actuator model -- ALL PROVISIONAL ---
    # The pass bank is a transconductance: per-FET source-sense op-amp loops
    # follow the OUT1 reference, so coil current tracks G_pass * V_out1 within
    # voltage compliance. G_pass is assumed scaled so +1 V commands I_FS.
    # The clamp uses "the same conditioning chain" (BOOTSTRAP), so it is
    # modelled the same way (transconductance pull-down, compliance V_clamp).
    # FLAG: the clamp's real transfer characteristic is an Open Unknown; if it
    # turns out to be a voltage-mode stage, set clamp_is_voltage_mode=True and
    # re-run the tuning tests.
    G_pass: float = 0.0      # [A/V], filled in __post_init__ if left 0
    G_clamp: float = 0.0     # [A/V], ditto
    tau_inner: float = 1.6e-6  # inner analog loop time constant [s] -- PROVISIONAL (~100 kHz)
    clamp_is_voltage_mode: bool = False

    def __post_init__(self):
        if self.I_FS == 0.0:
            object.__setattr__(self, "I_FS", 1.25 * self.I_rated)
        if self.G_pass == 0.0:
            object.__setattr__(self, "G_pass", self.I_FS)   # +1 V -> I_FS
        if self.G_clamp == 0.0:
            object.__setattr__(self, "G_clamp", self.I_FS)

    @property
    def R_loop(self) -> float:
        """Total series resistance seen by the loop [ohm]."""
        return self.R_coil + self.R_extra

    @property
    def f_pole(self) -> float:
        """Open-loop electrical pole R_loop / (2 pi L) [Hz]."""
        import math
        return self.R_loop / (2.0 * math.pi * self.L)

    @property
    def amps_per_lsb(self) -> float:
        """Current per ADC/DAC code (14-bit, +/-1 V <-> +/-I_FS)."""
        return self.I_FS / 8192.0

    @property
    def clamp_counts(self) -> int:
        """Hard output clamp in DAC counts = 100% of rated (review 2026-08-26)."""
        return round(self.I_rated / self.I_FS * 8192)


# BOOTSTRAP plant table. R_extra: BOOTSTRAP quotes "roughly 10-15 mOhm" total
# loop resistance for the MOT channel (coil 6.4 mOhm + pass bank + cabling);
# we take 12 mOhm total => R_extra = 5.6 mOhm, and assume a similar 6 mOhm of
# extras on the shim channels. PROVISIONAL until the Kelvin measurement.
CHANNELS = {
    "mot": Channel(name="mot", L=16e-6, R_coil=6.4e-3, R_extra=5.6e-3, I_rated=100.0),
    "z_shim": Channel(name="z_shim", L=29e-6, R_coil=11.6e-3, R_extra=6.0e-3, I_rated=60.0),
    "x_shim": Channel(name="x_shim", L=57e-6, R_coil=14.2e-3, R_extra=6.0e-3, I_rated=60.0),
    "y_shim": Channel(name="y_shim", L=57e-6, R_coil=14.2e-3, R_extra=6.0e-3, I_rated=60.0),
}
