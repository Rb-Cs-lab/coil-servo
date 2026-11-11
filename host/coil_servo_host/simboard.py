"""A simulated board: the Board API, with the physics from the model.

For developing and demonstrating the dashboard (and testing host code)
without hardware. SimBoard subclasses Board and replaces only the three raw
protocol calls (read_words / write_words / pop_words) with an in-memory
register file, so everything Board does on top -- the CFG shadow, the
register-map check, the clamp guard, apply_config, capture -- runs exactly
as it does against a real board.

Two time scales:

  * Status reads (sts) advance a coarse simulation in wall-clock time:
    the setpoint follows the register (or IN2) through the configured slew
    limits, the current follows the setpoint inside the compliance limits
    (rail push up, freewheel down), the OUT1 command is what the pass-bank
    model needs for that current. FSM: IDLE -> RUN on servo_enable + an
    arm EDGE, RUN -> RAMP_DOWN -> IDLE on servo_enable = 0, TRIP ->
    TRIPPED on over-current, TRIPPED -> IDLE on trip_ack.
  * A FIFO capture runs the bit-exact fixed-point PI (FixedPI) against the
    MVP plant (Plant) tick by tick over the 16.8 ms window, starting from
    the coarse state, including setpoint writes made during the window (so
    step.capture_step shows a real model step response).

Not simulated: flips (polarity stays 0, no TIMEOUT_HOLD), the heartbeat-
latch board, the DIO5 fault input, sigma-delta dither in captures
(approximated by its average). The raw 125 MS/s capture (capture_sel = 0)
is only a quiet record of the sensor zero: IN1/IN2 codes = the simulated
sensor offsets + noise (what coil_servo_host.zero measures; meaningful with
the servo stopped).

Sensor zero offsets: `sensor_offset_amps` = (IN1, IN2) is what the
simulated LEM / control-computer output reads with nothing flowing; like
the fabric, the simulation subtracts CFG in1_offset from IN1 before the
status and decimated captures see it.

Inputs a real board gets from the outside world are sim-only methods:
set_arm(level) is the DIO4 arm line; inject_trip() forces an over-current
trip; set_power(False) takes the drive away behind the board's back (the
logic-board heartbeat latch tripping, the supply off, an open circuit):
the Pitaya keeps bridge_en high but the current decays to zero.

One board can hold several channels.toml profiles (mot / mot_bench share
the MOT board). Pass the others as `variants`: when a CFG write changes
the board-LED pattern to a variant's, the simulation switches to that
variant's physics (bench supply limit, scale) -- as if the matching
power stage were connected. IN2 is 0 V, except in the swept-sine configuration (open_loop = 1,
sp_source = 0), where a simulated function generator steps through
frequencies so a sweep has something to measure.
"""

import math
import time

import numpy as np

from coil_servo_model.fixed_point import FixedPI
from coil_servo_model.loop import T_S
from coil_servo_model.plant import Plant
from coil_servo_model.registers import (CFG_BASE, CFG_WORDS, FSM_STATES,
                                        MAP_REV, STS_BASE, STS_FIELDS,
                                        STS_WORDS, _encode, parse_cfg)

from .board import FIFO_ADDR, FIFO_DEPTH, Board

WINDOW_S = FIFO_DEPTH * T_S           # 16.8 ms decimated capture window

# simulated function generator for the swept-sine demo (in IN2 counts)
GEN_FREQS = [200, 400, 800, 1500, 3000, 6000, 12000, 25000]
GEN_HOLD_S = 2.0                      # seconds per frequency
GEN_AMP_COUNTS = 40                   # ~1 A on the sensor scale


def pack_sts(**fields) -> list:
    """Named STS fields -> STS words (the inverse of parse_sts)."""
    block = 0
    for name, value in fields.items():
        w, lsb, width, signed = STS_FIELDS[name]
        block |= _encode(value, width, signed) << (32 * w + lsb)
    return [(block >> (32 * k)) & 0xFFFF_FFFF for k in range(STS_WORDS)]


class SimBoard(Board):
    def __init__(self, ch: dict, map_rev: int = MAP_REV,
                 max_clamp: int | None = None, check_map: bool = True,
                 seed: int | None = None, clock=time.monotonic,
                 variants=(), sensor_offset_amps=(0.0, 0.0),
                 build_id: int = 0):
        """ch: load_channel() result. map_rev: what the simulated bitstream
        reports (0 simulates the old 2026-08-27 image). variants: other
        load_channel() results for the same board (see the module doc)."""
        self.ch = ch
        self.variants = [ch] + [v for v in variants if v is not ch]
        self.power = True
        self.m = ch["model"]
        self.i_fs = ch["i_fs"]
        self.map_rev = map_rev
        self.sensor_offset_amps = tuple(sensor_offset_amps)
        self.build_id = build_id
        self.max_clamp = max_clamp
        self._map_checked = not check_map
        self._clock = clock
        self._rng = np.random.default_rng(seed)
        self.cfg_words = [0] * CFG_WORDS
        self.cfg_writes = []             # (word, value) of every CFG write
        self._c = parse_cfg(0)           # decoded CFG, kept in step
        self.state = "IDLE"
        self.I = 0.0                     # coil current [A], >= 0 (drive frame)
        self.sp = 0.0                    # slewed setpoint [A]
        self.u14 = 0
        self.arm = False
        self.bridge_en = False
        self.mismatch = False
        self.t_trip = 0.0
        self.t_last = clock()
        self.t_start = self.t_last
        self.fifo = None                 # capture in progress
        self.sync_shadow()

    def close(self):
        pass

    # ---- raw protocol, in memory -----------------------------------------
    def read_words(self, addr: int, count: int) -> list:
        if CFG_BASE <= addr < CFG_BASE + 4 * CFG_WORDS:
            k = (addr - CFG_BASE) // 4
            return list(self.cfg_words[k:k + count])
        if STS_BASE <= addr < STS_BASE + 4 * STS_WORDS:
            k = (addr - STS_BASE) // 4
            return self._sts_words()[k:k + count]
        return [0] * count

    def write_words(self, addr: int, words) -> None:
        if not CFG_BASE <= addr < CFG_BASE + 4 * CFG_WORDS:
            return
        self._advance()
        old = self._c
        k0 = (addr - CFG_BASE) // 4
        for k, w in enumerate(words):
            self.cfg_words[k0 + k] = w & 0xFFFF_FFFF
            self.cfg_writes.append((k0 + k, w & 0xFFFF_FFFF))
        block = 0
        for k, w in enumerate(self.cfg_words):
            block |= w << (32 * k)
        self._c = parse_cfg(block)
        self._on_cfg(old, self._c)

    def pop_words(self, addr: int, count: int) -> np.ndarray:
        if addr != FIFO_ADDR or self.fifo is None:
            return np.zeros(count, dtype=np.uint32)
        words = (self._run_raw(count) if self.fifo["raw"]
                 else self._run_capture(count))
        self.fifo = None                 # drained
        return words

    # ---- sim-only inputs ---------------------------------------------------
    def set_arm(self, level: bool) -> None:
        """The DIO4 arm line. A rising edge in IDLE with servo_enable = 1
        starts the servo."""
        self._advance()
        if level and not self.arm and self.state == "IDLE" \
                and self._c["servo_enable"]:
            self.state = "RUN"
            self.bridge_en = True
        self.arm = bool(level)

    def set_power(self, on: bool) -> None:
        """False: the drive disappears (latch tripped, supply off, open
        circuit) while the firmware still thinks the bridge is enabled."""
        self._advance()
        self.power = bool(on)

    def inject_trip(self) -> None:
        """Pretend the current crossed trip_level."""
        self._advance()
        if self.state in ("RUN", "RAMP_DOWN"):
            self._trip()

    # ---- physics -----------------------------------------------------------
    def _amps_per_s(self, code: int) -> float:
        return code / 2**14 * 125e6 * self.m.amps_per_lsb

    def _in2_counts(self, t: float) -> float:
        """IN2 in counts: 0 V, or the generator in the sweep setup."""
        c = self._c
        if not (c["open_loop"] and not c["sp_source"]):
            return 0.0
        k = int((t - self.t_start) / GEN_HOLD_S) % len(GEN_FREQS)
        offset = self.m.out1_for_current(30.0) * 8192
        return offset + GEN_AMP_COUNTS * math.sin(2 * math.pi * GEN_FREQS[k] * t)

    def _target_amps(self, t: float) -> float:
        c = self._c
        if self.state != "RUN":
            return 0.0
        counts = c["setpoint"] if c["sp_source"] else self._in2_counts(t)
        return counts * self.m.amps_per_lsb

    def _trip(self) -> None:
        self.state = "TRIP"
        self.t_trip = self._clock()
        self.u14 = 0

    def _on_cfg(self, old: dict, new: dict) -> None:
        now = self._clock()
        if new["led"] != old["led"]:
            for v in self.variants:
                if v.get("cfg", {}).get("led") == new["led"]:
                    self.ch, self.m, self.i_fs = v, v["model"], v["i_fs"]
                    break
        if old["fifo_rst"] and not new["fifo_rst"]:
            # FIFO restarts: remember where the window begins
            self.fifo = {"t0": now, "I": self.I, "sp": self.sp,
                         "raw": not new["capture_sel"],
                         "state": self.state, "setpoint": new["setpoint"],
                         "events": []}
        elif self.fifo is not None and new["setpoint"] != old["setpoint"]:
            self.fifo["events"].append((now - self.fifo["t0"], new["setpoint"]))
        if new["trip_ack"] and not old["trip_ack"] and self.state == "TRIPPED":
            self.state = "IDLE"
        if old["servo_enable"] and not new["servo_enable"] \
                and self.state == "RUN":
            self.state = "RAMP_DOWN"

    def _advance(self) -> None:
        """Bring the coarse simulation up to now (1 ms steps)."""
        now = self._clock()
        dt_total = min(now - self.t_last, 1.0)
        t = self.t_last
        self.t_last = now
        m, c = self.m, self._c
        zero_win = c["zero_win"] * m.amps_per_lsb
        up = self._amps_per_s(c["slew_up"])
        down = self._amps_per_s(c["slew_down"])
        n = max(1, int(math.ceil(dt_total / 1e-3)))
        h = dt_total / n
        for _ in range(n):
            t += h
            target = self._target_amps(t)
            if self.state in ("RUN", "RAMP_DOWN"):
                if abs(target) < zero_win:
                    self.sp = target
                else:
                    away = abs(target) > abs(self.sp) and target * self.sp >= 0
                    r = (up if away else down) * h
                    self.sp += max(-r, min(r, target - self.sp))
            else:
                self.sp = 0.0

            if self.state == "RUN" and c["open_loop"]:
                u = min(max(self.sp / m.amps_per_lsb, 0), c["out_clamp"])
                i_cmd = m.bank_current(m.gate_command(u / 8192))
            elif self.state in ("RUN", "RAMP_DOWN"):
                # drive frame, polarity 0: a negative setpoint is a sign
                # mismatch and the loop holds zero
                i_cmd = min(max(self.sp, 0.0), m.max_current)
            else:
                i_cmd = 0.0
            if m.I_supply_max is not None:           # bench supply CC limit
                i_cmd = min(i_cmd, m.I_supply_max)
            self.mismatch = self.state == "RUN" and \
                self.sp < -64 * m.amps_per_lsb
            if self.bridge_en and self.state != "TRIP" and not self.power:
                self.I = max(self.I - (m.V_f + self.I * m.R_fw) / m.L * h, 0)
            elif self.bridge_en and self.state != "TRIP":
                rise = max(m.V_rail - self.I * m.R_series
                           - m.v_bank_min(self.I), 0.0) / m.L
                fall = (m.V_f + self.I * m.R_fw) / m.L
                self.I += max(-fall * h, min(rise * h, i_cmd - self.I))
            elif self.state == "TRIP":       # freewheel, bridge closed
                self.I = max(self.I - (m.V_f + self.I * m.R_fw) / m.L * h, 0)
            else:                            # bridge open
                self.I = max(self.I - (m.V_rail + 3 * m.V_f) / m.L * h, 0)

            if self.state in ("RUN", "RAMP_DOWN") and \
                    self.I > c["trip_level"] * m.amps_per_lsb > 0:
                self._trip()
            if self.state == "TRIP" and (
                    self.I < zero_win
                    or now - self.t_trip > c["trip_timeout"] * 8e-9):
                self.state = "TRIPPED"
                self.bridge_en = False
            if self.state == "RAMP_DOWN" and self.sp == 0 \
                    and self.I < zero_win:
                self.state = "IDLE"
                self.bridge_en = False

        # OUT1 command: what the pass bank needs for this current
        if self.state in ("RUN", "RAMP_DOWN") and c["open_loop"]:
            self.u14 = int(min(max(self.sp / m.amps_per_lsb, 0), c["out_clamp"]))
        elif self.state in ("RUN", "RAMP_DOWN"):
            floor = c["u_floor"] if self.state == "RUN" else 0
            need = m.out1_for_current(self.I) * 8192 if self.I > 0.05 else 0
            dith = self._rng.integers(-1, 2) if c["dither_enable"] else 0
            self.u14 = int(min(max(round(need) + dith, floor), c["out_clamp"]))
        else:
            self.u14 = 0

    def _sts_words(self) -> list:
        self._advance()
        c, m = self._c, self.m
        meas = (self.I + self._residual_offset_counts() * m.amps_per_lsb
                + self._rng.normal(0.0, 0.3 * m.amps_per_lsb))
        in_run = self.state in ("RUN", "RAMP_DOWN")
        fifo_count = 0
        if self.fifo is not None:
            fifo_count = min(FIFO_DEPTH,
                             int((self._clock() - self.fifo["t0"]) / T_S))
        return pack_sts(
            fsm_state=FSM_STATES.index(self.state),
            bridge_en=int(self.bridge_en),
            armed=int(self.arm),
            out_sat=int(in_run and self.u14 >= c["out_clamp"] > 0),
            sp_sign_mismatch=int(self.mismatch),
            u_floor=int(self.state == "RUN" and not c["open_loop"]
                        and self.u14 <= c["u_floor"]),
            tripped=int(self.state in ("TRIP", "TRIPPED")),
            map_rev=self.map_rev,
            i_meas=max(-(1 << 21), min((1 << 21) - 1,
                                       round(meas / m.amps_per_lsb * 128))),
            sp_active=max(-8192, min(8191, round(self.sp / m.amps_per_lsb))),
            u14=self.u14,
            heartbeat=int((self._clock() - self.t_start) * 1e6) & 0xFFFF_FFFF,
            fifo_count=fifo_count,
            build_id=self.build_id,
        )

    def _residual_offset_counts(self) -> float:
        """IN1 sensor offset left after the fabric's in1_offset correction."""
        return (self.sensor_offset_amps[0] / self.m.amps_per_lsb
                - self._c["in1_offset"])

    def _run_raw(self, count: int) -> np.ndarray:
        """Raw IN1/IN2 codes (uncorrected, as capture_sel = 0 records)."""
        lsb = self.m.amps_per_lsb
        noise = self._rng.normal(0.0, 0.7, (count, 2))  # ADC + LEM [codes]
        in1 = np.round((self.I + self.sensor_offset_amps[0]) / lsb
                       + noise[:, 0]).astype(np.int64)
        in2 = np.round(self.sensor_offset_amps[1] / lsb
                       + noise[:, 1]).astype(np.int64)
        in1 = np.clip(in1, -8192, 8191) & 0x3FFF
        in2 = np.clip(in2, -8192, 8191) & 0x3FFF
        return ((in2 << 16) | in1).astype(np.uint32)

    # ---- capture: the fixed-point loop, tick by tick -------------------------
    def _run_capture(self, count: int) -> np.ndarray:
        f, c, m = self.fifo, self._c, self.m
        lsb = m.amps_per_lsb
        n = count
        i16 = np.zeros(n, dtype=np.int64)
        e16 = np.zeros(n, dtype=np.int64)
        noise = (self._rng.normal(0.0, 0.3, n)       # IN1 noise [codes]
                 + self._residual_offset_counts())
        events = [(int(t / T_S), sp) for t, sp in f["events"]]
        running = f["state"] in ("RUN", "RAMP_DOWN")

        if running:
            pi = FixedPI(c["kp_mant"], c["kp_shift"], c["ki_mant"],
                         c["ki_shift"], c["out_clamp"],
                         floor_counts=c["u_floor"] if f["state"] == "RUN" else 0)
            u0 = m.out1_for_current(f["I"]) * 8192 if f["I"] > 0.05 \
                else c["u_floor"]
            pi.acc = int(round(u0)) << (7 + c["ki_shift"])
            plant = Plant(m, i0=f["I"])
            # setpoint ramp per tick, in counts (the fabric's slew
            # limiter, servo_slew.v / SlewLimiter, averaged over the 128
            # clocks of a tick: rate codes are 2^-14 counts per clock)
            sl_acc = f["sp"] / lsb
            r_up = 128 * c["slew_up"] / 2**14
            r_down = 128 * c["slew_down"] / 2**14
        target = f["setpoint"]           # register value at window start

        I = f["I"]
        for k in range(n):
            while events and events[0][0] <= k:
                target = events.pop(0)[1]
            if not running:
                I = max(I - (m.V_rail + 3 * m.V_f) / m.L * T_S, 0.0)
                meas = I / lsb + noise[k]
                i16[k] = round(2 * meas)
                e16[k] = round(-2 * meas)
                continue
            tgt = target if c["sp_source"] else round(
                self._in2_counts(f["t0"] + k * T_S))
            if f["state"] == "RAMP_DOWN":
                tgt = 0
            if abs(tgt) < c["zero_win"]:
                sl_acc = float(tgt)                  # snap to zero
            else:
                d = tgt - sl_acc
                away = (sl_acc >= 0 and d > 0) or (sl_acc <= 0 and d < 0)
                r = r_up if away else r_down
                sl_acc += max(-r, min(r, d))
            sp = int(round(sl_acc))
            meas = plant.I / lsb + noise[k]
            meas_code = int(round(meas))
            e15 = sp - meas_code
            if c["open_loop"]:
                v1 = min(max(sp, 0), c["out_clamp"]) / 8192
            else:
                u14 = pi.step(max(-(1 << 21), min((1 << 21) - 1, e15 << 7)))
                # dither: the gate sees the average, u14 + resid/128
                v1 = (u14 + (pi.resid / 128 if c["dither_enable"] else 0)) / 8192
            plant.step(v1, 0.0, False, T_S, substeps=2)
            i16[k] = round(2 * meas)
            e16[k] = 2 * e15
        i16 = np.clip(i16, -32768, 32767)
        e16 = np.clip(e16, -32768, 32767)
        return (((e16 & 0xFFFF) << 16) | (i16 & 0xFFFF)).astype(np.uint32)
