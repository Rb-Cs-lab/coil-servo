"""Per-channel parameters for the MVP power stage (docs/mvp_architecture.md).

This is the single Python source for plant and scaling numbers. Values marked
PROVISIONAL are computed/assumed, not measured; MEASURED values cite their
source. host/config/channels.toml overrides the measurable ones per board
(host/coil_servo_host/config.py builds a Channel from it and derives the
register values); host/tests/test_config.py keeps the two in agreement.
The superseded full-architecture model is in git history (removed in MVP
rewrite step 4).

MVP actuator, per channel: one fixed low-voltage rail, an H-bridge on the
high side, and a LOW-SIDE linear pass bank of N MOSFETs (each with its own
ballast resistor) whose gates are driven from OUT1 through a non-inverting
op-amp stage. No active clamp, no boost rail, no per-FET current loops:
OUT2 and the boost output are unused.
"""

import math
from dataclasses import dataclass, replace

# Pass-bank MOSFET thresholds, MEASURED 2026-11-02 (Construction notes):
# IXTN200N10L2 at 3 mA, installed devices #1, 2, 4, 7, 9, 10.
VTH_INSTALLED = (3.52, 3.53, 3.52, 3.56, 3.50, 3.52)


@dataclass(frozen=True)
class Channel:
    name: str

    # --- Coils (Radia-computed) ---
    L: float                 # coil pair inductance [H]
    R_coil: float            # coil pair resistance [ohm] -- PROVISIONAL: Kelvin measurement pending
    I_rated: float           # max operating current [A]

    # --- Supply and series path ---
    V_rail: float = 5.0          # fixed rail [V] (Mean Well SE-600-5 on the MOT)
    # Supply current limit [A]. None = the supply never limits (the SE-600-5
    # at <= 100 A). A bench supply in constant-current mode: the coil current
    # cannot rise above this; the supply voltage collapses instead.
    I_supply_max: float | None = None
    R_cable: float = 2.5e-3      # 4/0 cabling + contacts, whole loop [ohm] -- PROVISIONAL
    R_coil_cable: float = 1.0e-3  # bridge-to-coil part of R_cable [ohm] -- PROVISIONAL
    R_bridge: float = 3.0e-3     # one conducting bridge switch, warm [ohm] -- PROVISIONAL (2.3 mOhm cold)
    V_f: float = 0.8             # MOSFET body-diode drop at high current [V] -- PROVISIONAL

    # --- Pass bank ---
    V_th: tuple = VTH_INSTALLED  # per-FET gate threshold [V] -- MEASURED (3 mA)
    g_fs: float = 50.0           # per-FET transconductance in this regime [A/V] -- PROVISIONAL
    R_ballast: float = 0.030     # per-FET source ballast (Caddock MP930) [ohm]
    R_ds_on: float = 11e-3       # per-FET fully-on resistance [ohm] (datasheet, 10 V gate)
    gain_opamp: float = 1.0 + 6.98 / 1.0   # OPA994 non-inverting stage, R_f 6.98k / R_g 1k
    out1_terminated: bool = True  # OUT1 into 50 ohm? unterminated doubles the swing -- PROVISIONAL
    V_opamp_max: float = 12.0    # op-amp output ceiling [V] -- PROVISIONAL (supply rail undocumented)
    # Lumped lag between OUT1 and bank current: gate RC (24.9 ohm x 23 nF
    # = 0.57 us) + op-amp + LEM response. PROVISIONAL.
    tau_inner: float = 1.4e-6

    # --- Turn-off physics (mvp_architecture.md 3.3) ---
    # "freewheel": bank throttles, coil current circulates through one bridge
    #   switch + the opposite low-side body diode (decided design; analysis
    #   pending scope confirmation).
    # "tvs": the rewrite brief's alternative -- bank voltage rises to the
    #   5KP12CA clamp. Kept for comparison until the scope capture.
    turnoff_model: str = "freewheel"
    V_tvs: float = 13.3          # 5KP12CA V_BR(min) [V]

    # --- Sensing: LEM LF 310-S (2000:1) -> burden -> AD8421 -> IN1 ---
    lem_ratio: float = 2000.0
    R_burden: float = 10.0       # [ohm] (MPR24000Z1009BCU00)
    G_inamp: float = 1.0         # AD8421 gain -- PROVISIONAL (1 or 2)
    # Primary turns through the LEM aperture: N turns multiply the sensor's
    # sensitivity by N (low-current bench profile). 1 = the coil lead passes
    # through once.
    lem_turns: int = 1

    out2_enable: bool = False    # OUT2 (active clamp) not fitted in the MVP

    # OUT1 command clamp set directly [DAC counts] instead of derived from
    # I_rated (a measured calibration, or the bench profile, where the
    # supply's current limit -- not the clamp -- limits the current).
    out_clamp_counts: int | None = None

    # --- Sensing scale ---
    @property
    def I_FS(self) -> float:
        """Coil current at +1.000 V on IN1 [A]."""
        return self.lem_ratio / (self.R_burden * self.G_inamp * self.lem_turns)

    @property
    def amps_per_lsb(self) -> float:
        """Coil current per IN1 ADC code."""
        return self.I_FS / 8192.0

    # --- Series path ---
    @property
    def R_series(self) -> float:
        """Everything in series with the bank while the bridge conducts."""
        return self.R_coil + self.R_cable + 2.0 * self.R_bridge

    @property
    def R_fw(self) -> float:
        """Freewheel loop: coils + one conducting switch + coil-side cable
        (the body diode adds V_f)."""
        return self.R_coil + self.R_bridge + self.R_coil_cable

    # --- Pass bank ---
    @property
    def out1_factor(self) -> float:
        """Volts at the op-amp input per normalized OUT1 unit (1.0 = full
        DAC scale): 1 V into 50 ohm, about 2 V into a high impedance."""
        return 1.0 if self.out1_terminated else 2.0

    def gate_command(self, u_out1: float) -> float:
        """Gate voltage commanded by a normalized OUT1 value [V]."""
        v = u_out1 * self.out1_factor * self.gain_opamp
        return min(max(v, 0.0), self.V_opamp_max)

    def fet_currents(self, V_gate: float) -> list:
        """Per-FET current a gate voltage asks for, in the linear region:
        I = g_fs (V_g - V_th) / (1 + g_fs R2), zero below threshold."""
        k = self.g_fs / (1.0 + self.g_fs * self.R_ballast)
        return [k * (V_gate - vt) if V_gate > vt else 0.0 for vt in self.V_th]

    def bank_current(self, V_gate: float) -> float:
        return sum(self.fet_currents(V_gate))

    def v_bank_min(self, I: float) -> float:
        """Smallest drop across the bank (all FETs fully on) at current I."""
        return I * (self.R_ds_on + self.R_ballast) / len(self.V_th)

    @property
    def G_pass(self) -> float:
        """Small-signal bank transconductance above threshold [A per
        normalized OUT1 unit]."""
        k = self.g_fs / (1.0 + self.g_fs * self.R_ballast)
        return len(self.V_th) * k * self.gain_opamp * self.out1_factor

    @property
    def dead_zone(self) -> float:
        """Normalized OUT1 below which no FET conducts."""
        return min(self.V_th) / (self.gain_opamp * self.out1_factor)

    def out1_for_current(self, I: float) -> float:
        """Normalized OUT1 that asks the bank for current I (bisection)."""
        lo, hi = min(self.V_th), self.V_opamp_max
        if self.bank_current(hi) < I:
            raise ValueError(f"{self.name}: gate ceiling cannot command {I} A")
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self.bank_current(mid) < I:
                lo = mid
            else:
                hi = mid
        return hi / (self.gain_opamp * self.out1_factor)

    @property
    def clamp_counts(self) -> int:
        """OUT1 command clamp in DAC counts: the command that asks for 100% of
        rated current with the nominal parameters. PROVISIONAL -- the real
        value comes from the measured G_pass and dead zone (it limits the
        gate command, not the current; mvp_architecture.md 3.5). A directly
        set out_clamp_counts wins."""
        if self.out_clamp_counts is not None:
            return self.out_clamp_counts
        return round(self.out1_for_current(self.I_rated) * 8192)

    @property
    def max_current(self) -> float:
        """Steady current with the bank fully on [A]: the rail's compliance
        limit, or the supply's current limit if that is lower."""
        i = self.V_rail / (self.R_series + (self.R_ds_on + self.R_ballast) / len(self.V_th))
        if self.I_supply_max is not None:
            i = min(i, self.I_supply_max)
        return i

    @property
    def freewheel_time(self) -> float:
        """Time for the freewheel decay from I_rated to zero [s]."""
        tau = self.L / self.R_fw
        i_eq = self.V_f / self.R_fw
        return tau * math.log((self.I_rated + i_eq) / i_eq)


# Coil table (Radia-computed). MVP builds the MOT channel only; the shim rows
# reuse the MOT power-stage parts as PLACEHOLDERS until their stage exists
# (shim supply not chosen -- rail <= 10 V).
CHANNELS = {
    "mot": Channel(name="mot", L=16e-6, R_coil=6.4e-3, I_rated=100.0),
    "z_shim": Channel(name="z_shim", L=29e-6, R_coil=11.6e-3, I_rated=60.0),
    "x_shim": Channel(name="x_shim", L=57e-6, R_coil=14.2e-3, I_rated=60.0),
    "y_shim": Channel(name="y_shim", L=57e-6, R_coil=14.2e-3, I_rated=60.0),
}

# Low-current test profiles (docs/bench_supply_test.md): the MOT power stage
# fed from a current-limited bench supply in place of the SE-600-5, driving
# ONE coil of the anti-Helmholtz pair. Kept out of CHANNELS (whose entries
# are the production channels and whose tests assume a 100 A-class supply).
# All bench values PROVISIONAL.
BENCH_CHANNELS = {
    "mot_bench": replace(
        CHANNELS["mot"], name="mot_bench",
        # One coil, not the pair: not calculated. The pair is 16 uH series-
        # opposed (2 L1 - 2 M), so one coil is L_pair/2 + M ~ 8-10 uH --
        # PROVISIONAL estimate. At <= 3 A it only sets ramp and freewheel
        # times of a few tens of us. R: half the pair, PROVISIONAL.
        L=9e-6,
        R_coil=3.2e-3,
        I_rated=2.5,         # highest closed-loop setpoint on the bench
        I_supply_max=3.0,    # bench supply CC limit -- the real hard limit
        lem_turns=1,         # LEM on the 4/0 lead, one pass: I_FS = 200 A
        # Clamp set directly (PENDING USER CONFIRMATION), ~11 % (407 counts, ~0.4 V of gate) above the
        # nominal dead zone: at 2.5 A the clamp derived from I_rated would
        # sit only ~44 counts above it, inside the dead-zone uncertainty and
        # V_th's thermal drift, and could block the first open-loop
        # measurement. On the bench the supply's CC limit bounds the current.
        out_clamp_counts=4000,
    ),
}
