"""The generic device interface the dashboard and its JSON API are built on.

A coil-servo channel is one kind of device. Lasers, temperature
controllers, etc. can later be added as further Device subclasses: the web
API (app.py) only uses the methods below, so a new kind of device needs no
change to the server, only (optionally) a page that knows how to draw it.
"""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ActionSpec:
    """One thing a device can be asked to do.

    permission: "control", "stop" or "capture" (see access.py).
    confirm: the request must carry "confirm": true -- the page shows a
    confirmation dialog first. Every action that can drive current has it.
    params: parameter name -> short description.
    """
    name: str
    label: str
    permission: str
    confirm: bool
    description: str
    params: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


class ActionError(Exception):
    """An action was refused (bad state, bad parameter, board error).
    status is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class Device:
    """Base class: a named thing with a live state, a history and actions."""

    kind = "device"
    actions: dict = {}

    def __init__(self, name: str):
        self.name = name

    # lifecycle
    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    # monitoring (every viewer)
    def describe(self) -> dict:
        """Static facts: name, kind, available actions."""
        return {"name": self.name, "kind": self.kind,
                "actions": [a.to_json() for a in self.actions.values()]}

    def snapshot(self) -> dict:
        """The current state as JSON-safe values."""
        raise NotImplementedError

    def history(self, span_s: float) -> dict:
        """Time series of the last span_s seconds, as columns."""
        return {"t": []}

    def info(self, profile: str | None = None) -> dict:
        """Configuration and other slowly-changing details (for one of the
        device's settings profiles, if it has several)."""
        return {}

    def latest_capture(self) -> dict | None:
        return None

    # control (checked against the access policy by the caller)
    def perform(self, action: str, params: dict, client: str) -> dict:
        raise ActionError(f"{self.name}: no action {action!r}", 404)
