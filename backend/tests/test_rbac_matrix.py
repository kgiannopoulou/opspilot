"""Every API route × every role, derived from the application itself — a new endpoint cannot ship without a check.

The permission each route requires is read from its dependency tree (the `require(Permission.X)` checker), so the
test needs no hand-maintained list: it proves that (1) every route except the sign-in flow needs a token, (2) every
role without the permission gets 403, and (3) every role with it gets past authorization.
"""

import inspect
import re

import pytest
from fastapi.routing import APIRoute

from app.auth.dependencies import get_current_user
from app.auth.permissions import Permission, has_permission
from app.main import app
from tests.conftest import as_user

PUBLIC = {("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/refresh"), ("POST", "/api/v1/auth/logout")}
ROLES = {"viewer": "viewer", "agent": "support_agent", "maria": "engineer", "admin": "admin"}


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep
        yield from _walk(dep)


def _requirements(route: APIRoute) -> tuple[bool, Permission | None]:
    authenticated, permission = False, None
    for dep in _walk(route.dependant):
        if dep.call is get_current_user:
            authenticated = True
        if getattr(dep.call, "__name__", "") == "checker":
            permission = inspect.getclosurevars(dep.call).nonlocals["permission"]
    return authenticated, permission


def _flatten(routes, prefix=""):
    """FastAPI includes routers lazily; walk them to get every effective path."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            yield from _flatten(route.original_router.routes, prefix + route.include_context.prefix)


ROUTES = [
    (method, path, *_requirements(route))
    for path, route in _flatten(app.routes)
    if path.startswith("/api/v1")
    for method in sorted(route.methods)
]


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "999999", path)  # nothing exists with that id: authorized calls end in 404/422


def test_the_matrix_covers_the_api():
    assert len(ROUTES) > 40
    unprotected = [(m, p) for m, p, authenticated, _ in ROUTES if not authenticated and (m, p) not in PUBLIC]
    assert unprotected == [], "routes without authentication"


@pytest.mark.parametrize(("method", "path"), [(m, p) for m, p, auth, _ in ROUTES if (m, p) not in PUBLIC])
def test_anonymous_requests_are_rejected(client, method, path):
    assert client.request(method, _url(path)).status_code == 401


def test_every_role_against_every_permissioned_route(client, seeded):
    checked = 0
    for method, path, _, permission in ROUTES:
        if permission is None:
            continue
        for user_key, role in ROLES.items():
            status = client.request(method, _url(path), headers=as_user(seeded[user_key])).status_code
            if has_permission(role, permission):
                assert status not in (401, 403), f"{role} should pass {permission} on {method} {path}"
            else:
                assert status == 403, f"{role} must not pass {permission} on {method} {path} (got {status})"
            checked += 1
    assert checked > 150
