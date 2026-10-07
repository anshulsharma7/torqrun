"""Request context middleware (pure ASGI).

Deliberately not ``BaseHTTPMiddleware``: that wrapper hides client disconnects from handlers,
which breaks long-polling (agent claim) and Server-Sent Events. It would also buffer streams.
"""

import json
import logging
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from torqrun_api.logging import request_id_var

logger = logging.getLogger("torqrun_api")

REQUEST_ID_HEADER = "X-Request-ID"
QUIET_PATHS = frozenset({"/healthz", "/readyz"})  # polled constantly by probes


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        # Accept a caller-supplied ID (e.g. from the proxy) only if it is short and printable.
        rid = incoming if 0 < len(incoming) <= 64 and incoming.isprintable() else uuid.uuid4().hex
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception(
                "unhandled error", extra={"method": scope["method"], "path": scope["path"]}
            )
            if response_started:
                raise
            body = json.dumps(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Internal server error",
                        "request_id": rid,
                    }
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (REQUEST_ID_HEADER.lower().encode(), rid.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            if scope["path"] not in QUIET_PATHS:
                logger.info(
                    "request",
                    extra={
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
            request_id_var.reset(token)
