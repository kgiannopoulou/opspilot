"""HTTP-level hardening: security headers on every response and a request-body size limit.

The API serves JSON to a same-origin Next.js proxy, so its responses never need to be framed, sniffed or cached, and
a strict Content-Security-Policy costs nothing. The interactive docs (/docs, /redoc) load Swagger UI / ReDoc from a
CDN with an inline bootstrap script, so they get their own, narrower exception.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "connect-src 'self'; worker-src blob:; frame-ancestors 'none'; base-uri 'none'"
)
DOCS_PATHS = ("/docs", "/redoc")

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}
HSTS = "max-age=63072000; includeSubDomains"


def security_headers(path: str, hsts: bool) -> dict[str, str]:
    headers = dict(BASE_HEADERS)
    headers["Content-Security-Policy"] = DOCS_CSP if path.startswith(DOCS_PATHS) else API_CSP
    if hsts:
        headers["Strict-Transport-Security"] = HSTS
    return headers


async def apply_http_security(request: Request, call_next: Any, max_body_bytes: int, hsts: bool) -> Response:
    """Reject oversized bodies before they are read, then add the security headers to whatever comes back."""
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > max_body_bytes):
        response: Response = JSONResponse(
            status_code=413,
            content={"error": "payload_too_large", "detail": f"request bodies are limited to {max_body_bytes} bytes"},
        )
    else:
        response = await call_next(request)
    response.headers.update(security_headers(request.url.path, hsts))
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"  # API responses carry customer and incident data
    return response
