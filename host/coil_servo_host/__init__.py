"""Host-side tools for the coil servo: register access over the wired
Ethernet link, configuration from channels.toml, capture, step response,
and swept-sine transfer-function measurement.

The board must run host/board/coil_servo_server.py (deploy.py sets that up).
"""

from .board import Board
from .config import load_channel

__all__ = ["Board", "load_channel"]
