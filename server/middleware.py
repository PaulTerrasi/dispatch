from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject /api/* and /internal/* requests that lack the correct bearer token.

    When MORNING_DIGEST_AUTH_TOKEN is unset (local dev), auth is skipped.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._token and (
            request.url.path.startswith("/api") or request.url.path.startswith("/internal")
        ):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != self._token:
                return Response("Unauthorized", status_code=401)
        return await call_next(request)
