"""The web server: static pages plus a small JSON API (docs/dashboard.md).

    GET  /api/whoami                          your address, what you may do
    GET  /api/devices                         every device: facts + state
    GET  /api/devices/{name}                  one device's state
    GET  /api/devices/{name}/history?span=60  last N seconds, as columns
    GET  /api/devices/{name}/info             configuration, CFG read-back
    GET  /api/devices/{name}/capture          latest capture (404 if none)
    GET  /api/stream                          server-sent events: all states
    POST /api/devices/{name}/actions/{action} {"confirm": true, ...params}

Reads are open to every viewer. POSTs are checked against the access
policy (access.py) and must come from this page or a deliberate API client:
JSON body plus the header X-Coil-Servo: 1. A browser will not send that
header cross-site without a CORS preflight, which this server never grants,
so a web page open elsewhere on the lab PC cannot drive the coils through
the localhost exception.
"""

import asyncio
import contextlib
import ipaddress
import json
import socket
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (FileResponse, JSONResponse, Response,
                                 StreamingResponse)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .access import AccessPolicy
from .device import ActionError

STATIC = Path(__file__).resolve().parent / "static"
API_HEADER = "x-coil-servo"


def _host_ok(host_header: str | None) -> bool:
    """DNS-rebinding guard for control requests: the Host header must name
    this machine by IP address, 'localhost' or its own host name."""
    if not host_header:
        return False
    host = host_header.rsplit(":", 1)[0] if not host_header.endswith("]") \
        else host_header
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    return host in names or host.split(".")[0] in names


def create_app(devices: dict, policy: AccessPolicy | None = None,
               action_log=None, stream_interval: float = 0.1) -> Starlette:
    """devices: name -> Device (started and closed with the app)."""
    policy = policy or AccessPolicy()

    def client_of(request: Request) -> str | None:
        return request.client.host if request.client else None

    def get_device(request: Request):
        name = request.path_params["name"]
        if name not in devices:
            raise ActionError(f"no device {name!r}", 404)
        return devices[name]

    async def index(request):
        return FileResponse(STATIC / "index.html")

    async def whoami(request):
        return JSONResponse(policy.describe(client_of(request)))

    async def list_devices(request):
        return JSONResponse([dict(d.describe(), state=d.snapshot())
                             for d in devices.values()])

    async def device_state(request):
        return JSONResponse(get_device(request).snapshot())

    async def device_history(request):
        try:
            span = float(request.query_params.get("span", 600))
        except ValueError:
            raise ActionError("span: seconds", 400) from None
        return JSONResponse(get_device(request).history(span))

    async def device_info(request):
        return JSONResponse(get_device(request).info())

    async def device_capture(request):
        cap = get_device(request).latest_capture()
        if cap is None:
            raise ActionError("no capture yet", 404)
        return JSONResponse(cap)

    async def stream(request):
        max_events = int(request.query_params.get("max_events", 0))

        async def events():
            n = 0
            while True:
                data = {name: d.snapshot() for name, d in devices.items()}
                yield f"data: {json.dumps(data)}\n\n"
                n += 1
                if max_events and n >= max_events:
                    return
                await asyncio.sleep(stream_interval)
                if await request.is_disconnected():
                    return

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    async def action(request):
        dev = get_device(request)
        name = request.path_params["action"]
        client = client_of(request) or "?"
        spec = dev.actions.get(name)
        if spec is None:
            raise ActionError(f"{dev.name}: no action {name!r}", 404)
        if not policy.allowed(spec.permission, client):
            if action_log:
                action_log.log(dev.name, client, name, "",
                               "refused: not on the control allowlist")
            raise ActionError(
                f"'{spec.label}' is accepted only from the lab PC "
                f"({', '.join(policy.allow)}); this request came from "
                f"{client}. Viewing works from anywhere. On the lab PC "
                f"itself, open http://localhost:<port>.", 403)
        if request.headers.get(API_HEADER) != "1":
            raise ActionError("control requests need the header "
                              "X-Coil-Servo: 1", 400)
        if not _host_ok(request.headers.get("host")):
            raise ActionError("unexpected Host header", 400)
        try:
            params = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise ActionError("body must be JSON", 400) from None
        if not isinstance(params, dict):
            raise ActionError("body must be a JSON object", 400)
        if spec.confirm and params.get("confirm") is not True:
            raise ActionError(f"'{spec.label}' needs explicit confirmation "
                              f"(\"confirm\": true)", 400)
        # board I/O blocks: run it off the event loop
        result = await asyncio.to_thread(dev.perform, name, params, client)
        return JSONResponse(result)

    async def action_error(request, exc: ActionError):
        return JSONResponse({"ok": False, "error": str(exc)},
                            status_code=exc.status)

    async def favicon(request):
        return Response(status_code=204)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        for d in devices.values():
            d.start()
        try:
            yield
        finally:
            for d in devices.values():
                await asyncio.to_thread(d.close)
            if action_log:
                action_log.close()

    routes = [
        Route("/", index),
        Route("/favicon.ico", favicon),
        Route("/api/whoami", whoami),
        Route("/api/devices", list_devices),
        Route("/api/devices/{name}", device_state),
        Route("/api/devices/{name}/history", device_history),
        Route("/api/devices/{name}/info", device_info),
        Route("/api/devices/{name}/capture", device_capture),
        Route("/api/devices/{name}/actions/{action}", action,
              methods=["POST"]),
        Route("/api/stream", stream),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes, lifespan=lifespan,
                     exception_handlers={ActionError: action_error})
