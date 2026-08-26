"""Plant model: RL coil driven by the linear pass bank and the active clamp.

Drive-frame convention (design.md Node B): the H-bridge polarity rotation has
already happened, so coil current here is >= 0 in normal operation, the pass
bank pushes it up, the clamp pulls it down.

Actuator model (PROVISIONAL -- see channels.py):
  * Pass bank = transconductance. The per-FET source-sense loops regulate the
    series (== coil) current toward G_pass * v_out1, with a first-order inner
    time constant tau_inner. It can only SOURCE: when it throttles back, the
    current falls no faster than the passive L/R decay.
  * Clamp = same, mirrored: regulates current downward toward
    I - G_clamp * v_out2 territory by absorbing it, limited by V_clamp.
  * Voltage compliance: dI/dt is capped by the available headroom,
      up:   dI/dt <= (V_avail - I*R_loop - V_ds_min) / L
      down: dI/dt >= -(V_clamp_avail + I*R_loop) / L
    where V_avail is V_boost when the boost cap is switched in, else V_rail,
    and V_clamp_avail is V_clamp only while the clamp stage is commanded.

Integration: explicit Euler with substeps << tau_inner. Good enough for a
reference model; tests check dt-convergence.
"""

from .channels import Channel


class Plant:
    def __init__(self, channel: Channel, i0: float = 0.0):
        self.ch = channel
        self.I = float(i0)          # coil current [A], drive frame

    def step(self, v_out1: float, v_out2: float, boost: bool, dt: float,
             substeps: int = 8) -> float:
        """Advance by dt with OUT1/OUT2 held at v_out1/v_out2 volts.

        Returns the coil current at the end of the interval.
        """
        ch = self.ch
        h = dt / substeps
        v_avail = ch.V_boost if boost else ch.V_rail

        for _ in range(substeps):
            I = self.I

            if v_out1 > 0.0 and v_out2 <= 0.0:
                # Pass bank regulating toward its commanded current.
                target = ch.G_pass * v_out1
                dIdt = (target - I) / ch.tau_inner
                # Sourcing is voltage-limited; throttling back can't beat the
                # passive decay (the stage has no pull-down path).
                dIdt = min(dIdt, (v_avail - I * ch.R_loop - ch.V_ds_min) / ch.L)
                dIdt = max(dIdt, -(I * ch.R_loop) / ch.L)
            elif v_out2 > 0.0 and v_out1 <= 0.0:
                if ch.clamp_is_voltage_mode:
                    # Alternative model: clamp applies reverse volts
                    # proportional to command (full scale -> V_clamp).
                    v_rev = ch.V_clamp * min(v_out2, 1.0)
                    dIdt = -(v_rev + I * ch.R_loop) / ch.L
                else:
                    # Default: transconductance sink pulling toward
                    # (I - G_clamp*v_out2), compliance-limited by V_clamp.
                    target = I - ch.G_clamp * v_out2
                    dIdt = (target - I) / ch.tau_inner
                    dIdt = max(dIdt, -(ch.V_clamp + I * ch.R_loop) / ch.L)
                    dIdt = min(dIdt, -(I * ch.R_loop) / ch.L)
            elif v_out1 <= 0.0 and v_out2 <= 0.0:
                # Deadband: both stages off, passive decay through R_loop.
                dIdt = -(I * ch.R_loop) / ch.L
            else:
                # Both commanded -- forbidden by the output mux; the model
                # refuses to guess what the hardware would do.
                raise AssertionError(
                    "OUT1 and OUT2 driven simultaneously: mutual-exclusion "
                    "invariant violated upstream of the plant")

            self.I = I + h * dIdt

        return self.I
