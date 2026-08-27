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

from coil_servo_model.registers import (CFG_BASE, CFG_FIELDS, STS_BASE,
                                        _encode, parse_sts)

FIFO_ADDR = 0x4200_0000     # axi_hub stream port 2 (S00): reads pop the FIFO
FIFO_DEPTH = 16384
_HEADER = struct.Struct("<4sII")


class Board:
    def __init__(self, host: str, port: int = 9001, timeout: float = 5.0,
                 max_clamp: int | None = None):
        """max_clamp: refuse out_clamp writes above this many counts
        (pass the channel's rated counts, i.e. cfg["out_clamp"] from
        load_channel). None disables the guard -- an explicit choice.
        This is a convenience rail against typos, NOT a safety layer:
        the enforcement that matters is the clamp in the FPGA fabric."""
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.max_clamp = max_clamp
        # Local shadow of the full 512-bit CFG block, synced FROM THE
        # HARDWARE at connect: a fresh connection that guessed zeros would
        # clobber every field its first write_cfg didn't mention (this bit
        # us live: a new REPL's write_cfg(open_loop=1) cleared servo_enable
        # and gracefully stopped the servo mid-measurement).
        self._cfg = 0
        for k, w in enumerate(self.read_words(CFG_BASE, 16)):
            self._cfg |= w << (32 * k)

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
    def write_cfg(self, **fields) -> None:
        """Update named CFG fields, preserving everything else via the local
        shadow, and write only the words that changed."""
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

    def pulse(self, field: str) -> None:
        """1-then-0 on a CFG bit (int_clear, fifo_rst, flip_fault_ack)."""
        self.write_cfg(**{field: 1})
        self.write_cfg(**{field: 0})

    def warn_flags(self) -> dict:
        """Read status and print prominent warnings for conditions the
        design doc promises the host tools will shout about. Returns sts."""
        s = self.sts()
        if s["fault"]:
            print("*** WARNING: hardware interlock FAULT flag is set "
                  "(DIO5). The firmware cannot clear it -- check the "
                  "interlock chain. ***")
        if s["sp_sign_mismatch"]:
            print("*** WARNING: setpoint sign disagrees with the bridge "
                  "polarity -- the loop will sit safely at zero current "
                  "until they agree (see design.md Node B). ***")
        return s

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
        raw = self.pop_words(FIFO_ADDR, FIFO_DEPTH).astype(np.int64)

        lo = (raw & 0xFFFF).astype(np.int32)
        hi = ((raw >> 16) & 0xFFFF).astype(np.int32)
        if decimated:
            lo[lo >= 1 << 15] -= 1 << 16      # s16: i_dec>>6
            hi[hi >= 1 << 15] -= 1 << 16      # s16: e_dec>>6
        else:
            lo &= 0x3FFF
            hi &= 0x3FFF
            lo[lo >= 1 << 13] -= 1 << 14      # s14 IN1
            hi[hi >= 1 << 13] -= 1 << 14      # s14 IN2
        return np.stack([lo, hi], axis=1)
