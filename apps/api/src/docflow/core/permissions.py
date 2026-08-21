"""Role-based permissions.

A pure function over a table, not a policy engine. Four roles and ~20 actions do
not justify OPA or Casbin — those buy dynamic, customer-authored policy, which is
a Phase 4 problem. A dict is faster, readable in one screen, and exhaustively
testable, which is what actually prevents authorization bugs.
"""

from __future__ import annotations

from enum import StrEnum

from docflow.models.enums import WorkspaceRole


class Permission(StrEnum):
    DOCUMENT_VIEW = "document:view"
    DOCUMENT_UPLOAD = "document:upload"
    DOCUMENT_DELETE_OWN = "document:delete_own"
    DOCUMENT_DELETE_ANY = "document:delete_any"
    DOCUMENT_REPROCESS = "document:reprocess"

    FACT_VIEW = "fact:view"
    FACT_EDIT = "fact:edit"

    TASK_VIEW = "task:view"
    TASK_CREATE = "task:create"
    TASK_EDIT = "task:edit"
    TASK_DELETE = "task:delete"

    CHAT_USE = "chat:use"
    SEARCH_USE = "search:use"

    MEMBER_VIEW = "member:view"
    MEMBER_INVITE = "member:invite"
    MEMBER_REMOVE = "member:remove"
    ROLE_CHANGE = "role:change"

    WORKSPACE_EDIT = "workspace:edit"
    WORKSPACE_DELETE = "workspace:delete"
    BILLING_MANAGE = "billing:manage"
    AUDIT_VIEW = "audit:view"
    USAGE_VIEW = "usage:view"


_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENT_VIEW,
        Permission.FACT_VIEW,
        Permission.TASK_VIEW,
        Permission.CHAT_USE,
        Permission.SEARCH_USE,
        Permission.MEMBER_VIEW,
        Permission.USAGE_VIEW,
    }
)

_MEMBER: frozenset[Permission] = _VIEWER | {
    Permission.DOCUMENT_UPLOAD,
    Permission.DOCUMENT_DELETE_OWN,
    Permission.FACT_EDIT,
    Permission.TASK_CREATE,
    Permission.TASK_EDIT,
    Permission.TASK_DELETE,
}

_ADMIN: frozenset[Permission] = _MEMBER | {
    Permission.DOCUMENT_DELETE_ANY,
    Permission.DOCUMENT_REPROCESS,
    Permission.MEMBER_INVITE,
    Permission.MEMBER_REMOVE,
    Permission.WORKSPACE_EDIT,
    Permission.AUDIT_VIEW,
}

_OWNER: frozenset[Permission] = _ADMIN | {
    Permission.ROLE_CHANGE,
    Permission.WORKSPACE_DELETE,
    Permission.BILLING_MANAGE,
}

ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[Permission]] = {
    WorkspaceRole.VIEWER: _VIEWER,
    WorkspaceRole.MEMBER: _MEMBER,
    WorkspaceRole.ADMIN: _ADMIN,
    WorkspaceRole.OWNER: _OWNER,
}


def can(role: WorkspaceRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def permissions_for(role: WorkspaceRole) -> list[str]:
    """Sent to the client so the UI can hide what the user cannot do.

    The UI hiding a control is a courtesy; the server check is the control.
    """
    return sorted(p.value for p in ROLE_PERMISSIONS.get(role, frozenset()))


def can_delete_document(role: WorkspaceRole, *, is_uploader: bool) -> bool:
    """Members may delete what they uploaded; admins may delete anything."""
    if can(role, Permission.DOCUMENT_DELETE_ANY):
        return True
    return is_uploader and can(role, Permission.DOCUMENT_DELETE_OWN)
