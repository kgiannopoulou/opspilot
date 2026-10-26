"""Role-based access control. Roles live in the database; what each role may do is defined here, in code review."""

from enum import StrEnum


class Permission(StrEnum):
    TICKETS_READ = "tickets:read"
    TICKETS_WRITE = "tickets:write"  # create, edit, comment, transition, assign
    INCIDENTS_READ = "incidents:read"
    INCIDENTS_INVESTIGATE = "incidents:investigate"  # run investigations, link tickets, change status
    DASHBOARD_READ = "dashboard:read"
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    SERVICES_MANAGE = "services:manage"
    AUDIT_READ = "audit:read"
    AI_FEEDBACK = "ai:feedback"  # rate AI investigations (👍 / 👎)


class RoleName(StrEnum):
    ADMIN = "admin"
    ENGINEER = "engineer"
    SUPPORT_AGENT = "support_agent"
    VIEWER = "viewer"


_READ = {Permission.TICKETS_READ, Permission.INCIDENTS_READ, Permission.DASHBOARD_READ, Permission.USERS_READ}

ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {
    RoleName.VIEWER: frozenset(_READ),
    RoleName.SUPPORT_AGENT: frozenset(_READ | {Permission.TICKETS_WRITE, Permission.AI_FEEDBACK}),
    RoleName.ENGINEER: frozenset(
        _READ | {Permission.TICKETS_WRITE, Permission.INCIDENTS_INVESTIGATE, Permission.AI_FEEDBACK}
    ),
    RoleName.ADMIN: frozenset(Permission),
}


def permissions_for(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[RoleName(role)]
    except ValueError:
        return frozenset()  # unknown role → no access (fail closed)


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for(role)
