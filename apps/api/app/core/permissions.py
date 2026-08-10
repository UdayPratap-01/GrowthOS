"""
Server-side role-based authorization.

Frontend button visibility is a usability affordance, never a control. Every
sensitive action is gated here and enforced in the route handler.

Roles
-----
OWNER   Full control, including billing and ownership transfer.
ADMIN   Full operational control: publish, spend, connect integrations, autonomy.
MEMBER  Day-to-day work: create drafts, manage clients and leads, propose actions.
        Cannot approve or execute anything that spends money or writes to a
        platform, and cannot change integrations or autonomy settings.
VIEWER  Read-only.
"""

from __future__ import annotations

import enum
from typing import Callable

from fastapi import Depends, HTTPException, status

from app.core.deps import AuthContext, get_current_auth
from app.models.enums import MemberRole
from app.observability import events


class Permission(str, enum.Enum):
    # Reading
    read = "read"

    # Content and CRM work that costs nothing and touches no external platform
    content_write = "content_write"
    client_write = "client_write"
    lead_write = "lead_write"

    # Money and external side effects
    campaign_publish = "campaign_publish"
    budget_change = "budget_change"
    financial_action = "financial_action"
    action_approve = "action_approve"
    action_execute = "action_execute"

    # Configuration with security or spend consequences
    integration_connect = "integration_connect"
    integration_disconnect = "integration_disconnect"
    autonomy_manage = "autonomy_manage"
    autonomous_execution = "autonomous_execution"

    # Commercial
    billing_manage = "billing_manage"
    organization_manage = "organization_manage"


_VIEWER: frozenset[Permission] = frozenset({Permission.read})

_MEMBER: frozenset[Permission] = _VIEWER | {
    Permission.content_write,
    Permission.client_write,
    Permission.lead_write,
}

# Everything operational, including spend and external writes.
_ADMIN: frozenset[Permission] = _MEMBER | {
    Permission.campaign_publish,
    Permission.budget_change,
    Permission.financial_action,
    Permission.action_approve,
    Permission.action_execute,
    Permission.integration_connect,
    Permission.integration_disconnect,
    Permission.autonomy_manage,
    Permission.autonomous_execution,
}

_OWNER: frozenset[Permission] = _ADMIN | {
    Permission.billing_manage,
    Permission.organization_manage,
}

ROLE_PERMISSIONS: dict[MemberRole, frozenset[Permission]] = {
    MemberRole.viewer: _VIEWER,
    MemberRole.member: _MEMBER,
    MemberRole.admin: _ADMIN,
    MemberRole.owner: _OWNER,
}


def permissions_for(role: MemberRole) -> frozenset[Permission]:
    # Unknown roles get the least privilege rather than defaulting open.
    return ROLE_PERMISSIONS.get(role, _VIEWER)


def has_permission(role: MemberRole, permission: Permission) -> bool:
    return permission in permissions_for(role)


class PermissionDenied(HTTPException):
    def __init__(self, permission: Permission, role: MemberRole) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"PERMISSION_DENIED: '{permission.value}' requires a higher role. "
                f"Your role is '{role.value}'."
            ),
        )


def require_permission(permission: Permission) -> Callable[..., AuthContext]:
    """
    FastAPI dependency factory enforcing a permission on a route.

    Usage:
        auth: AuthContext = Depends(require_permission(Permission.campaign_publish))
    """

    async def _dependency(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        role = auth.membership.role
        if not has_permission(role, permission):
            events.authorization_denied(
                user_id=auth.user_id,
                organization_id=auth.organization_id,
                permission=permission.value,
                role=role.value,
            )
            raise PermissionDenied(permission, role)
        return auth

    return _dependency


def assert_permission(auth: AuthContext, permission: Permission) -> None:
    """Imperative check for service-layer call sites."""
    role = auth.membership.role
    if not has_permission(role, permission):
        raise PermissionDenied(permission, role)
