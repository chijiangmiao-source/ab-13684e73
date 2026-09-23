"""HTTP service: /health and POST /audit.

Implemented with the standard library so the image needs no third-party
packages.  Large optimal_solution_count values are arbitrary precision
integers and are serialized as plain JSON integers (digits, no quotes).
"""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .solver import solve
from .validation import validate

MAX_BODY_BYTES = 2 * 1024 * 1024


class AuditHandler(BaseHTTPRequestHandler):
    server_version = "PulseAudit/1.0"

    def log_message(self, fmt, *args):  # quiet by default; structured stderr
        if os.environ.get("AUDIT_HTTP_LOG"):
            super().log_message(fmt, *args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/audit":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        length = self.headers.get("Content-Length")
        try:
            n = int(length) if length is not None else -1
        except ValueError:
            n = -1
        if n < 0:
            self._send_json(HTTPStatus.BAD_REQUEST,
                            {"errors": [{"path": "",
                                         "message": "missing or invalid Content-Length"}]})
            return
        if n > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            {"errors": [{"path": "", "message": "request body too large"}]})
            return

        raw = self.rfile.read(n)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST,
                            {"errors": [{"path": "",
                                         "message": f"invalid JSON: {exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)}"}]})
            return

        hits, window, errors = validate(payload)
        if errors:
            # Errors only: no result fields leak into the response.
            self._send_json(HTTPStatus.BAD_REQUEST, {"errors": errors})
            return

        try:
            result = solve(hits, window)
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                            {"errors": [{"path": "", "message": f"solver failure: {exc}"}]})
            return

        self._send_json(HTTPStatus.OK, {"result": result})


def create_server(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), AuditHandler)
    return httpd


def main() -> None:
    host = os.environ.get("AUDIT_HOST", "0.0.0.0")
    port = int(os.environ.get("AUDIT_PORT", "8080"))
    httpd = create_server(host, port)
    print(f"audit service listening on {host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
