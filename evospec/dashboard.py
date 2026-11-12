"""Read-only local HTTP dashboard for the canonical EvoSpec context."""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Final, cast

from .agent import project_context
from .agent_protocol import context_json
from .persistence import RecoveryError
from .workflow import WorkflowService

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
_REVISION_CHECK_INTERVAL_SECONDS: Final = 0.5
_HEARTBEAT_INTERVAL_SECONDS: Final = 1.0
_ASSETS: Final = {
    "/": "index.html",
    "/index.html": "index.html",
    "/dashboard.css": "dashboard.css",
    "/dashboard.js": "dashboard.js",
}
_CONTENT_TYPES: Final = {
    "index.html": "text/html; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
    "dashboard.js": "application/javascript; charset=utf-8",
}


class DashboardServer(ThreadingHTTPServer):
    """Loopback server carrying the selected repository root."""

    daemon_threads: bool = True

    def __init__(self, root: Path, port: int) -> None:
        self.root: Path = root
        self._shutdown_event: threading.Event = threading.Event()
        super().__init__((DEFAULT_HOST, port), _DashboardRequestHandler)

    def shutdown(self) -> None:  # pyright: ignore[reportImplicitOverride]
        self._shutdown_event.set()
        super().shutdown()

    @property
    def shutting_down(self) -> threading.Event:
        return self._shutdown_event


class _DashboardRequestHandler(BaseHTTPRequestHandler):
    protocol_version: str = "HTTP/1.1"
    close_connection: bool

    def do_GET(self) -> None:
        if not self._valid_host():
            self._error(HTTPStatus.BAD_REQUEST, "invalid Host header")
            return
        if self.path == "/api/context":
            self._context()
            return
        if self.path == "/api/events":
            self._events()
            return
        asset = _ASSETS.get(self.path)
        if asset is None:
            self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
            return
        self._asset(asset)

    def _valid_host(self) -> bool:
        hosts = self.headers.get_all("Host")
        if hosts is None or len(hosts) != 1:
            return False
        authority = hosts[0]
        if authority != authority.strip():
            return False
        port = cast(DashboardServer, self.server).server_port
        expected = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            expected.update(("127.0.0.1", "localhost"))
        return authority.casefold() in {value.casefold() for value in expected}
    def do_POST(self) -> None:
        self._method_not_allowed()

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def do_HEAD(self) -> None:
        self._method_not_allowed()

    def log_message(self, format: str, *args: object) -> None:  # pyright: ignore[reportImplicitOverride]
        return

    def _context(self) -> None:
        try:
            server = cast(DashboardServer, self.server)
            payload = context_json(project_context(WorkflowService.open(server.root)))
        except RecoveryError as error:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
            return
        self._send(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")

    def _events(self) -> None:
        server = cast(DashboardServer, self.server)
        try:
            last_seen_revision = self._revision(server)
        except RecoveryError as error:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
            return

        _ = self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        if not self._write_sse_frame(
            f"event: revision\ndata: {json.dumps({'revision': last_seen_revision}, separators=(',', ':'))}\n\n"
        ):
            return

        next_heartbeat = time.monotonic() + _HEARTBEAT_INTERVAL_SECONDS
        while not server.shutting_down.wait(_REVISION_CHECK_INTERVAL_SECONDS):
            try:
                revision = self._revision(server)
            except RecoveryError:
                self.close_connection = True
                return
            if revision != last_seen_revision:
                if not self._write_sse_frame(
                    f"event: revision\ndata: {json.dumps({'revision': revision}, separators=(',', ':'))}\n\n"
                ):
                    return
                last_seen_revision = revision
            if time.monotonic() >= next_heartbeat:
                if not self._write_sse_frame(": heartbeat\n\n"):
                    return
                next_heartbeat = time.monotonic() + _HEARTBEAT_INTERVAL_SECONDS
        self.close_connection = True

    def _revision(self, server: DashboardServer) -> int:
        return project_context(WorkflowService.open(server.root)).revision

    def _write_sse_frame(self, frame: str) -> bool:
        try:
            _ = self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True
            return False
        return True

    def _asset(self, name: str) -> None:
        try:
            body = files("evospec").joinpath("resources", "dashboard", name).read_bytes()
        except FileNotFoundError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "dashboard resource is unavailable")
            return
        self._send(body, _CONTENT_TYPES[name])

    def _method_not_allowed(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "dashboard is read-only", allow="GET")

    def _error(self, status: HTTPStatus, detail: str, *, allow: str | None = None) -> None:
        body = json.dumps({"error": detail}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        _ = self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if allow is not None:
            self.send_header("Allow", allow)
        self.end_headers()
        _ = self.wfile.write(body)

    def _send(self, body: bytes, content_type: str) -> None:
        _ = self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)


def create_server(root: Path, port: int = DEFAULT_PORT) -> DashboardServer:
    """Bind a loopback dashboard server for one selected project."""
    return DashboardServer(root, port)


def serve(root: Path, port: int = DEFAULT_PORT, *, open_browser: bool = True) -> None:
    """Run the dashboard until interrupted, opening a browser after a successful bind."""
    with create_server(root, port) as server:
        host, bound_port = server.server_address[:2]
        if open_browser:
            _ = webbrowser.open(f"http://{host}:{bound_port}/")
        _ = server.serve_forever()
