"""Who may do what: monitoring for everyone, control from the allowlist.

Permission classes (each action declares one):
  monitor -- read state, history, captures: every viewer
  capture -- start a FIFO capture (writes the two capture bits, never the
             loop): allowlist, or everyone with --allow-remote-capture
  stop    -- servo_enable = 0 (graceful ramp to zero): allowlist, or
             everyone with --allow-remote-stop
  control -- anything that can drive current or change the configuration:
             allowlist only

The allowlist holds IP addresses or networks ("127.0.0.1", "::1",
"192.168.0.10", "192.168.0.0/24"); it defaults to the lab PC itself.
"""

import ipaddress
from dataclasses import dataclass, field

DEFAULT_ALLOW = ("127.0.0.1", "::1")


def _parse(entry: str):
    return ipaddress.ip_network(entry.strip(), strict=False)


@dataclass
class AccessPolicy:
    allow: tuple = DEFAULT_ALLOW
    remote_capture: bool = False
    remote_stop: bool = False
    _nets: list = field(init=False, repr=False)

    def __post_init__(self):
        self._nets = [_parse(a) for a in self.allow]

    def is_local(self, client: str | None) -> bool:
        """True if the client address is on the allowlist."""
        if not client:
            return False
        try:
            ip = ipaddress.ip_address(client.split("%")[0])
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped                # ::ffff:127.0.0.1
        return any(ip.version == n.version and ip in n for n in self._nets)

    def allowed(self, permission: str, client: str | None) -> bool:
        if permission == "monitor":
            return True
        if permission == "capture" and self.remote_capture:
            return True
        if permission == "stop" and self.remote_stop:
            return True
        return self.is_local(client)

    def describe(self, client: str | None) -> dict:
        return {"client": client,
                "control": self.allowed("control", client),
                "capture": self.allowed("capture", client),
                "stop": self.allowed("stop", client),
                "allowlist": list(self.allow)}
