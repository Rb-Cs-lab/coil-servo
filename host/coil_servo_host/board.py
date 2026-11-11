"""TCP client for the board-side register server.

Talks the 12-byte-header protocol of host/board/coil_servo_server.py and
layers the register map (model/coil_servo_model/registers.py) on top:
named CFG writes with a local shadow of the full CFG block, parsed STS
reads, and FIFO capture.
"""

import socket
import struct
import time

import numpy as np

from .capture import decode_words
from coil_servo_model.registers import (CFG_BASE, CFG_FIELDS, CFG_WORDS,
                                        MAP_REV, STS_BASE, _encode,
                                        parse_cfg, parse_sts)

FIFO_ADDR = 0x4200_0000     # axi_hub stream port 2 (S00): reads pop the FIFO
FIFO_DEPTH = 16384
_HEADER = struct.Struct("<4sII")


class Board:
    def __init__(self, host: str, port: int = 9001, timeout: float = 5.0,
                 max_clamp: int | None = None, check_map: bool = True):
        """max_clamp: refuse out_clamp writes above this many counts
        (pass the channel's rated counts, i.e. cfg["out_clamp"] from
        load_channel). None disables the guard -- an explicit choice.
        This is a convenience rail against typos, NOT a safety layer:
        the enforcement that matters is the clamp in the FPGA fabric.

        check_map: before the first CFG write, verify the bitstream reports
        the register-map revision this host knows (registers.MAP_REV) and
        refuse otherwise -- on an older bitstream the new fields would land
        on other registers. False only for diagnostics."""
        self._map_checked = not check_map
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            # ConnectionError is an OSError: deploy.py's "no server yet"
            # check still works
            raise ConnectionError(
                f"no register server answering at {host}:{port} ({exc}). "
                f"Is the board powered and on the wired network? After a "
                f"reboot the server is not running: start it (and load the "
                f"bitstream) with  python -m coil_servo_host.deploy <channel>"
            ) from exc
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.max_clamp = max_clamp
        # Local shadow of the full CFG block, synced FROM THE
        # HARDWARE at connect: a fresh connection that guessed zeros would
        # clobber every field its first write_cfg didn't mention (this bit
        # us live: a new REPL's write_cfg(open_loop=1) cleared servo_enable
        # and gracefully stopped the servo mid-measurement).
        self.sync_shadow()

    def sync_shadow(self) -> None:
        """Reload the local CFG shadow from the hardware. Call it before
        writing when another client (a second tool, the dashboard) may have
        changed CFG since this connection last looked -- otherwise the next
        write_cfg would put that client's word back the way this one
        remembers it."""
        cfg = 0
        for k, w in enumerate(self.read_words(CFG_BASE, CFG_WORDS)):
            cfg |= w << (32 * k)
        self._cfg = cfg

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- raw protocol ----------------------------------------------------
    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("server closed")
            buf += chunk
        return buf

    def _request(self, cmd: bytes, addr: int, count: int, payload: bytes = b""):
        self.sock.sendall(_HEADER.pack(cmd, addr, count) + payload)
        status = self._recv(4)
        if status != b"OK  ":
            raise IOError(f"{cmd!r} at 0x{addr:08x} x{count} refused")
        if cmd in (b"READ", b"POPS"):
            return self._recv(4 * count)
        return b""

    def read_words(self, addr: int, count: int) -> list:
        data = self._request(b"READ", addr, count)
        return list(struct.unpack(f"<{count}I", data))

    def pop_words(self, addr: int, count: int) -> np.ndarray:
        data = self._request(b"POPS", addr, count)
        return np.frombuffer(data, dtype="<u4")

    def write_words(self, addr: int, words) -> None:
        payload = struct.pack(f"<{len(words)}I", *[w & 0xFFFFFFFF for w in words])
        self._request(b"WRIT", addr, len(words), payload)

    # ---- register map ----------------------------------------------------
    def _check_map(self) -> None:
        if self._map_checked:
            return
        rev = self.sts()["map_rev"]
        if rev != MAP_REV:
            raise RuntimeError(
                f"the board's bitstream reports register-map revision {rev}, "
                f"this host expects {MAP_REV}: writing would land fields on "
                f"the wrong registers. Deploy the current coil_servo.bit.bin "
                f"(python -m coil_servo_host.deploy <channel>).")
        self._map_checked = True

    def _field(self, name: str) -> int:
        """A CFG field's value in the local shadow."""
        word, lsb, width, _ = CFG_FIELDS[name]
        return (self._cfg >> (32 * word + lsb)) & ((1 << width) - 1)

    def write_cfg(self, **fields) -> None:
        """Update named CFG fields, preserving everything else via the local
        shadow, and write only the words that changed.

        Leaving open loop (open_loop 1 -> 0) is only applied while the FSM
        is IDLE or TRIPPED: the firmware's PI keeps integrating in open loop
        against an error the open-loop drive does not correct, so switching
        to closed loop while running would apply a wound-up integrator to
        OUT1 (found on the loopback, 2026-11-11: it tripped the board).
        - together with servo_enable=0 (a stop): open_loop is left at 1 --
          the stop then runs with OUT1 = the forced-zero setpoint = 0 -- and
          is cleared by Board.stop() or the next enable from IDLE;
        - otherwise: refused with RuntimeError."""
        self._check_map()
        if (fields.get("open_loop") == 0 and self._field("open_loop") == 1):
            state = self.sts()["fsm_state_name"]
            if state not in ("IDLE", "TRIPPED"):
                if fields.get("servo_enable") == 0:
                    fields = {k: v for k, v in fields.items()
                              if k != "open_loop"}
                else:
                    raise RuntimeError(
                        f"refusing to leave open loop while the servo runs "
                        f"(state {state}): the PI integrator winds up in "
                        f"open loop and would jump OUT1. Stop first "
                        f"(Board.stop()), then switch to closed loop and "
                        f"re-arm.")
        touched = set()
        for name, value in fields.items():
            if (name == "out_clamp" and self.max_clamp is not None
                    and value > self.max_clamp):
                raise ValueError(
                    f"out_clamp {value} exceeds 100% of rated current "
                    f"({self.max_clamp} counts). If you really mean to raise "
                    f"the hard clamp above rated, construct "
                    f"Board(..., max_clamp=None) explicitly.")
            word, lsb, width, signed = CFG_FIELDS[name]
            bit = 32 * word + lsb
            self._cfg &= ~(((1 << width) - 1) << bit)
            self._cfg |= _encode(value, width, signed) << bit
            touched.add(word)
        for w in sorted(touched):
            self.write_words(CFG_BASE + 4 * w,
                             [(self._cfg >> (32 * w)) & 0xFFFFFFFF])

    def stop(self, wait: bool = True, timeout: float = 5.0) -> str:
        """Graceful stop: servo_enable = 0 (the firmware forces the setpoint
        to zero, lets the current fall into the zero window and opens the
        bridge). With wait, poll until IDLE, then leave open loop and clear
        the integrator. Returns the final state name."""
        self.write_cfg(servo_enable=0)
        state = self.sts()["fsm_state_name"]
        if not wait:
            return state
        t_end = time.monotonic() + timeout
        while state not in ("IDLE", "TRIPPED") and time.monotonic() < t_end:
            time.sleep(0.02)
            state = self.sts()["fsm_state_name"]
        if state == "IDLE":
            if self._field("open_loop"):
                self.write_cfg(open_loop=0)
            self.pulse("int_clear")
        return state

    def pulse(self, field: str) -> None:
        """1-then-0 on a CFG bit (int_clear, fifo_rst, flip_fault_ack)."""
        self.write_cfg(**{field: 1})
        self.write_cfg(**{field: 0})

    def warn_flags(self) -> dict:
        """Read status and print prominent warnings for conditions the
        design doc promises the host tools will shout about. Returns sts."""
        s = self.sts()
        if s["tripped"]:
            print("*** WARNING: OVER-CURRENT TRIP (state "
                  f"{s['fsm_state_name']}). The bridge is open or opening. "
                  "Find out why, then Board.pulse('trip_ack') and re-raise "
                  "arm (DIO4) to run again. ***")
        if s["fault"]:
            print("*** WARNING: DIO5 fault input is high. (Not wired in the "
                  "MVP -- check what is connected to E1 pin 13.) The "
                  "firmware cannot clear it. ***")
        if s["sp_sign_mismatch"]:
            print("*** WARNING: setpoint sign disagrees with the bridge "
                  "polarity -- the loop will sit safely at zero current "
                  "until they agree (see design.md Node B). ***")
        return s

    def wait_for_run(self, timeout: float = 30.0) -> dict:
        """After servo_enable = 1, wait for the FSM to reach RUN. Leaving
        IDLE needs a rising EDGE on arm (DIO4) *after* servo_enable is set;
        an arm line that was already high does nothing. Prompts the user
        and raises SystemExit on timeout. Returns sts."""
        s = self.sts()
        if s["fsm_state_name"] == "RUN":
            return s
        print("servo enabled, waiting in IDLE: raise arm (DIO4) now -- if it "
              "is already high, drop it and raise it again")
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            s = self.sts()
            if s["fsm_state_name"] == "RUN":
                return s
            if s["tripped"]:
                break
            time.sleep(0.05)
        self.warn_flags()
        # don't leave the servo enabled: a later arm edge would start it
        self.write_cfg(servo_enable=0)
        raise SystemExit(f"FSM did not reach RUN (state {s['fsm_state_name']}); "
                         f"servo_enable set back to 0")

    def read_cfg(self) -> dict:
        """CFG as the hardware holds it (not the local shadow), decoded."""
        block = 0
        for k, w in enumerate(self.read_words(CFG_BASE, CFG_WORDS)):
            block |= w << (32 * k)
        return parse_cfg(block)

    def sts(self) -> dict:
        words = self.read_words(STS_BASE, 8)
        block = 0
        for k, w in enumerate(words):
            block |= w << (32 * k)
        return parse_sts(block)

    def apply_config(self, cfg_fields: dict) -> None:
        """Write a full configuration; the control word goes LAST so gains
        and limits are in place before the servo can be enabled."""
        ctrl = {k: v for k, v in cfg_fields.items() if CFG_FIELDS[k][0] == 0}
        rest = {k: v for k, v in cfg_fields.items() if CFG_FIELDS[k][0] != 0}
        if rest:
            self.write_cfg(**rest)
        if ctrl:
            self.write_cfg(**ctrl)

    # ---- capture FIFO ----------------------------------------------------
    def capture(self, decimated: bool, timeout: float = 1.0) -> np.ndarray:
        """Restart the FIFO, wait for it to fill, drain it.

        Returns an int32 array of shape (N, 2):
          decimated=False: columns (IN1 code, IN2 code), 125 MS/s, 131 us
          decimated=True:  columns (i_dec>>6, e_dec>>6), 976.6 kS/s, 16.8 ms
        """
        self.write_cfg(capture_sel=1 if decimated else 0)
        self.pulse("fifo_rst")
        deadline = time.monotonic() + timeout + (0.020 if decimated else 0.001)
        while time.monotonic() < deadline:
            count = self.sts()["fifo_count"]
            if count >= FIFO_DEPTH:
                break
            time.sleep(0.002)
        else:
            raise TimeoutError(f"FIFO filled only {count}/{FIFO_DEPTH}")
        return decode_words(self.pop_words(FIFO_ADDR, FIFO_DEPTH), decimated)
