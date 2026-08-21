"""The authorization matrix, asserted exhaustively.

Every (role x permission) pair is checked against an explicit table written
independently of the implementation. If someone widens a role in
core/permissions.py, this fails — which is the point. A privilege escalation that
nobody notices is the worst kind.
"""

from __future__ import annotations

import pytest

from docflow.core.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    can,
    can_delete_document,
    permissions_for,
)
from docflow.models.enums import WorkspaceRole

# Written from the spec table in docs/10-architecture.md §17.5, deliberately by
# hand rather than derived from the implementation.
EXPECTED: dict[Permission, set[WorkspaceRole]] = {
    Permission.DOCUMENT_VIEW: {
        WorkspaceRole.VIEWER,
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.FACT_VIEW: {
        WorkspaceRole.VIEWER,
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.TASK_VIEW: {
        WorkspaceRole.VIEWER,
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.CHAT_USE: {
        WorkspaceRole.VIEWER,
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.SEARCH_USE: {
        WorkspaceRole.VIEWER,
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.MEMBER_VIEW: {
        WorkspaceRole.VIEWER,
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.USAGE_VIEW: {
        WorkspaceRole.VIEWER,
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.DOCUMENT_UPLOAD: {WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.DOCUMENT_DELETE_OWN: {
        WorkspaceRole.MEMBER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.OWNER,
    },
    Permission.FACT_EDIT: {WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.TASK_CREATE: {WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.TASK_EDIT: {WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.TASK_DELETE: {WorkspaceRole.MEMBER, WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.DOCUMENT_DELETE_ANY: {WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.DOCUMENT_REPROCESS: {WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.MEMBER_INVITE: {WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.MEMBER_REMOVE: {WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.WORKSPACE_EDIT: {WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.AUDIT_VIEW: {WorkspaceRole.ADMIN, WorkspaceRole.OWNER},
    Permission.ROLE_CHANGE: {WorkspaceRole.OWNER},
    Permission.WORKSPACE_DELETE: {WorkspaceRole.OWNER},
    Permission.BILLING_MANAGE: {WorkspaceRole.OWNER},
}


@pytest.mark.parametrize("permission", list(Permission))
@pytest.mark.parametrize("role", list(WorkspaceRole))
def test_every_role_permission_pair(role: WorkspaceRole, permission: Permission) -> None:
    expected = role in EXPECTED[permission]
    assert can(role, permission) is expected, (
        f"{role.value} should {'have' if expected else 'NOT have'} {permission.value}"
    )


def test_expected_table_covers_every_permission() -> None:
    """Guard the guard: a new permission must be added to the table above,
    or it would be silently untested."""
    assert set(EXPECTED) == set(Permission)


def test_roles_are_strictly_nested() -> None:
    """viewer < member < admin < owner, with no gaps.

    A non-nested model is defensible, but it is not what this product means by
    these role names — so if it ever stops being true, that should be deliberate.
    """
    viewer = ROLE_PERMISSIONS[WorkspaceRole.VIEWER]
    member = ROLE_PERMISSIONS[WorkspaceRole.MEMBER]
    admin = ROLE_PERMISSIONS[WorkspaceRole.ADMIN]
    owner = ROLE_PERMISSIONS[WorkspaceRole.OWNER]

    assert viewer < member < admin < owner


def test_viewer_cannot_mutate_anything() -> None:
    mutating = {
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_DELETE_OWN,
        Permission.DOCUMENT_DELETE_ANY,
        Permission.FACT_EDIT,
        Permission.TASK_CREATE,
        Permission.TASK_EDIT,
        Permission.TASK_DELETE,
        Permission.MEMBER_INVITE,
        Permission.ROLE_CHANGE,
        Permission.WORKSPACE_DELETE,
    }
    for permission in mutating:
        assert not can(WorkspaceRole.VIEWER, permission)


def test_only_owner_can_change_roles_or_delete_the_workspace() -> None:
    """Privilege escalation guard: an admin must not be able to promote itself."""
    for role in (WorkspaceRole.VIEWER, WorkspaceRole.MEMBER, WorkspaceRole.ADMIN):
        assert not can(role, Permission.ROLE_CHANGE)
        assert not can(role, Permission.WORKSPACE_DELETE)
        assert not can(role, Permission.BILLING_MANAGE)


# --------------------------------------------------------------------------
# ownership-sensitive rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "is_uploader", "expected"),
    [
        (WorkspaceRole.VIEWER, True, False),
        (WorkspaceRole.VIEWER, False, False),
        (WorkspaceRole.MEMBER, True, True),
        (WorkspaceRole.MEMBER, False, False),
        (WorkspaceRole.ADMIN, False, True),
        (WorkspaceRole.OWNER, False, True),
    ],
)
def test_document_deletion_respects_ownership(
    role: WorkspaceRole, is_uploader: bool, expected: bool
) -> None:
    assert can_delete_document(role, is_uploader=is_uploader) is expected


def test_permissions_for_is_sorted_and_serialisable() -> None:
    values = permissions_for(WorkspaceRole.MEMBER)
    assert values == sorted(values)
    assert all(isinstance(v, str) for v in values)
