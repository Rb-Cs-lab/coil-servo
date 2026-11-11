"""Plant model for the MVP power stage (docs/mvp_architecture.md section 3).

Drive-frame convention (design.md Node B): the H-bridge polarity rotation has
already happened, so coil current here is >= 0.

The pass bank is a voltage-controlled CURRENT source with a dead zone: OUT1 ->
op-amp -> gate (one lumped lag, tau_inner) -> per-FET current
g_fs (V_g - V_th,k) / (1 + g_fs R2). The coil's L/R pole is not in the loop;
the coil only shows up through the voltage limits on how fast the current
can change:

  * up (bank asks for more than flows): the rail is the only push,
      L dI/dt = V_rail - I*R_series - V_bank,min(I)
    and the current stops at what the bank asks for -- or at the supply's
    current limit I_supply_max, if set (a bench supply in constant-current
    mode: its voltage collapses so the current cannot rise further; the
    bank's FETs then sit further on than needed);
  * down (bank asks for less): turnoff_model "freewheel" (decided design):
    the coil current circulates through one bridge switch and the opposite
    low-side body diode, independent of the bank,
      L dI/dt = -(V_f + I*R_fw);
    turnoff_model "tvs" (rewrite brief alternative): the bank's voltage rises
    to the TVS clamp,
      L dI/dt = -(V_tvs - V_rail + I*R_series);
    in both cases the current stops falling at what the bank asks for;
  * bridge open at current (heartbeat-latch trip): body diodes push the
    current back into the supply,
      L dI/dt = -(V_rail + 3 V_f + I*R_series), down to zero.

OUT2 and the boost output are not connected in the MVP: the plant rejects any
nonzero command on them, so a firmware/config mistake shows up in simulation.

Integration: explicit Euler with substeps; the gate lag uses the exact
first-order update.
"""

import math

from .channels import Channel


class Plant:
    def __init__(self, channel: Channel, i0: float = 0.0):
        self.ch = channel
        if channel.I_supply_max is not None:
            assert i0 <= channel.I_supply_max, "i0 above the supply current limit"
        self.I = float(i0)          # coil current [A], drive frame
        self.bridge_enabled = True
        if i0 > 0.0:
            # start in steady state: gate already asking for i0
            self.V_gate = channel.gate_command(channel.out1_for_current(i0))
        else:
            self.V_gate = 0.0

    def step(self, v_out1: float, v_out2: float, boost: bool, dt: float,
             substeps: int = 8) -> float:
        """Advance by dt with OUT1 held at v_out1 (normalized DAC units, 1.0 =
        full scale). Returns the coil current at the end of the interval."""
        assert v_out2 == 0.0, "OUT2 commanded but no active clamp is fitted (MVP)"
        assert not boost, "boost commanded but no boost rail is fitted (MVP)"
        ch = self.ch
        h = dt / substeps
        v_cmd = ch.gate_command(v_out1)
        decay = math.exp(-h / ch.tau_inner)
        I_lim = math.inf if ch.I_supply_max is None else ch.I_supply_max

        for _ in range(substeps):
            self.V_gate = v_cmd + (self.V_gate - v_cmd) * decay
            I = self.I

            if not self.bridge_enabled:
                dIdt = -(ch.V_rail + 3.0 * ch.V_f + I * ch.R_series) / ch.L
                self.I = max(I + h * dIdt, 0.0) if I > 0.0 else 0.0
                continue

            I_cmd = ch.bank_current(self.V_gate)
            if I_cmd >= I:
                dIdt = (ch.V_rail - I * ch.R_series - ch.v_bank_min(I)) / ch.L
                I_new = I + h * dIdt
                if dIdt > 0.0:
                    I_new = min(I_new, I_cmd, I_lim)
            else:
                if ch.turnoff_model == "freewheel":
                    dIdt = -(ch.V_f + I * ch.R_fw) / ch.L
                elif ch.turnoff_model == "tvs":
                    dIdt = -(ch.V_tvs - ch.V_rail + I * ch.R_series) / ch.L
                else:
                    raise ValueError(f"unknown turnoff_model {ch.turnoff_model!r}")
                I_new = max(I + h * dIdt, I_cmd)
            self.I = max(I_new, 0.0)

        return self.I

    def fet_currents(self) -> list:
        """Current each FET carries right now [A]. The bank passes the coil
        current, split in the ratio the gate voltage asks for."""
        asked = self.ch.fet_currents(self.V_gate)
        total = sum(asked)
        if total <= 0.0:
            return [0.0] * len(asked)
        share = min(self.I, total) / total
        return [a * share for a in asked]
