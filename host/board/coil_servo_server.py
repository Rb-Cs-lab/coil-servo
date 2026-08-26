#!/usr/bin/env python3
"""Register-access server that runs ON the Red Pitaya (official OS 3.x).

Maps the FPGA register window at 0x40000000 (axi_hub) and serves a tiny
binary protocol over TCP so the lab PC can read/write registers and drain
the capture FIFO over the wired Ethernet connection. Standard library only;
deploy.py copies this file to the board and starts it.

Protocol (little-endian), one request at a time per connection:
  request : 12-byte header  <4s I I> = command, address, count
            command b"WRIT" is followed by count*4 bytes of data
  commands: b"READ" -> count words from address, address incrementing
            b"POPS" -> count words all read from the SAME address
                       (pops an axi_hub stream port, e.g. the FIFO)
            b"WRIT" -> write count words starting at address
  response: 4-byte status (b"OK  " or b"ERR ") then, for READ/POPS,
            count*4 bytes of data.

Security note: no authentication -- run it only on the lab's private wired
network, never on anything routable.
"""

import argparse
import mmap
import socket
import struct
import sys

HUB_BASE = 0x4000_0000
HUB_SPAN = 0x0800_0000        # ports 0-7, 16 MiB each
MAX_WORDS = 65536

HEADER = struct.Struct("<4sII")


class RealMem:
    def __init__(self):
        self.fd = open("/dev/mem", "r+b", buffering=0)
        self.mm = mmap.mmap(self.fd.fileno(), HUB_SPAN, offset=HUB_BASE)

    def read(self, addr, count, pop):
        out = bytearray()
        off = addr - HUB_BASE
        for k in range(count):
            out += self.mm[off:off + 4]
            if not pop:
                off += 4
        return bytes(out)

    def write(self, addr, data):
        off = addr - HUB_BASE
        self.mm[off:off + len(data)] = data


class MockMem:
    """For protocol tests on a PC: a sparse word store instead of /dev/mem."""

    def __init__(self):
        self.words = {}

    def read(self, addr, count, pop):
        out = bytearray()
        for k in range(count):
            a = addr if pop else addr + 4 * k
            out += struct.pack("<I", self.words.get(a, 0))
        return bytes(out)

    def write(self, addr, data):
        for k in range(0, len(data), 4):
            self.words[addr + k] = struct.unpack_from("<I", data, k)[0]


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client closed")
        buf += chunk
    return buf


def valid(addr, count):
    return (HUB_BASE <= addr and addr + 4 * count <= HUB_BASE + HUB_SPAN
            and addr % 4 == 0 and 1 <= count <= MAX_WORDS)


def serve(mem, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    print(f"LISTENING {srv.getsockname()[1]}", flush=True)
    while True:
        conn, peer = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                cmd, addr, count = HEADER.unpack(recv_exact(conn, HEADER.size))
                if cmd == b"WRIT":
                    data = recv_exact(conn, 4 * count)
                    if valid(addr, count):
                        mem.write(addr, data)
                        conn.sendall(b"OK  ")
                    else:
                        conn.sendall(b"ERR ")
                elif cmd in (b"READ", b"POPS"):
                    if valid(addr, count):
                        conn.sendall(b"OK  " + mem.read(addr, count,
                                                        cmd == b"POPS"))
                    else:
                        conn.sendall(b"ERR ")
                else:
                    conn.sendall(b"ERR ")
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9001)
    p.add_argument("--mock", action="store_true",
                   help="serve a fake register store (protocol tests)")
    args = p.parse_args()
    mem = MockMem() if args.mock else RealMem()
    try:
        serve(mem, args.port)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
