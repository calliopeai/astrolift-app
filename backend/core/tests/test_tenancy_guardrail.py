"""Static-analysis CI test for tenancy filtering enforcement (#164).

Walks every platform GraphQL resolver and verifies it carries both
``@require_permission`` and ``@tenant_scoped`` decorators. Catches the
class of bug where a new resolver lands without the tenancy guard and
silently leaks rows across orgs.

Approach: AST scan of every ``astrolift_*/schema/{queries,mutations}.py``
module. For each method on a ``@strawberry.type`` class that itself is
decorated with ``@strawberry.field``, ``@strawberry.mutation``, or the
older ``@strawberry.field()`` form, assert the decorator stack includes
both checks.

Strawberry resolvers that legitimately escape tenant scope (e.g. the
``me`` resolver, public health surfaces) live on the EXEMPT list with a
written justification. The list is intentionally tight; new entries
need a code review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

# Resolvers that intentionally do NOT need @tenant_scoped.
# Each entry must include the qualified name and a why.
EXEMPT: dict[str, str] = {
    # Identity self-service: the user reads their own profile and the
    # set of memberships they belong to. The org filter is the user
    # itself; tenant context is the *output*, not the input.
    "IdentityQuery.me": "self-service: returns the caller's profile",
    "IdentityQuery.astrolift_my_profile": "self-service: editable profile fields",
    "IdentityQuery.my_memberships": (
        "self-service: enumerates orgs the caller belongs to — needed "
        "before any tenant context can be picked"
    ),
    "IdentityQuery.astrolift_my_permissions": (
        "self-service: returns the caller's effective permissions — "
        "ordering matters because permissions drive nav rendering"
    ),
    "IdentityQuery.astrolift_active_identity_provider": (
        "renders before any tenant is picked — the IdP catalogue is "
        "the entry point to the login flow"
    ),
    "IdentityMutation.update_my_profile": (
        "self-service: callers can edit their own profile fields when "
        "the IdP doesn't lock them"
    ),
    "IdentityMutation.set_active_organization": (
        "the act of selecting a tenant context cannot itself be "
        "tenant-scoped"
    ),
    "IdentityMutation.create_organization": (
        "creates the tenant — by definition there is no tenant "
        "context yet at the time of this call"
    ),
    "IdentityMutation.update_organization": (
        "operates on the org row directly; permission check restricts "
        "to org admins of that specific org"
    ),
    "IdentityMutation.soft_delete_organization": (
        "operates on the org row directly; permission check restricts "
        "to org admins of that specific org"
    ),
    # Notifications are user-scoped: the resolver filters by the
    # caller's user_id directly, not by tenant.
    "OperationsQuery.astrolift_my_notifications": "self-service: caller's own notifications",
    "OperationsMutation.mark_notification_read": "self-service: marks the caller's own notification",
    "OperationsMutation.mark_all_notifications_read": "self-service: marks all the caller's notifications",
}


def _platform_schema_files() -> list[Path]:
    return sorted(
        p
        for p in BACKEND.glob("astrolift_*/schema/*.py")
        if p.name in {"queries.py", "mutations.py"}
    )


def _decorator_name(node: ast.expr) -> str:
    """Return the bare function name of a decorator expression."""
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_strawberry_field(decorators: list[ast.expr]) -> bool:
    """True when the method is exposed as a Strawberry resolver.

    Covers ``@strawberry.field``, ``@strawberry.mutation``, and the
    parameterized variants ``@strawberry.field(...)`` etc. — basically
    any decorator whose attribute is field/mutation/subscription.
    """
    for d in decorators:
        target = d.func if isinstance(d, ast.Call) else d
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "strawberry"
            and target.attr in {"field", "mutation", "subscription"}
        ):
            return True
    return False


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    return {_decorator_name(d) for d in node.decorator_list}


def _walk_strawberry_classes(tree: ast.Module) -> list[ast.ClassDef]:
    out: list[ast.ClassDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for d in node.decorator_list:
            target = d.func if isinstance(d, ast.Call) else d
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "strawberry"
                and target.attr in {"type", "input", "interface", "subscription"}
            ):
                out.append(node)
                break
    return out


def _resolver_methods(cls: ast.ClassDef) -> list[ast.FunctionDef]:
    return [
        m
        for m in cls.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _is_strawberry_field(m.decorator_list)
    ]


def _resolvers_in_file(path: Path) -> list[tuple[str, ast.FunctionDef]]:
    tree = ast.parse(path.read_text())
    out: list[tuple[str, ast.FunctionDef]] = []
    for cls in _walk_strawberry_classes(tree):
        for m in _resolver_methods(cls):
            qualname = f"{cls.name}.{m.name}"
            out.append((qualname, m))
    return out


@pytest.mark.parametrize("path", _platform_schema_files(), ids=lambda p: str(p.relative_to(BACKEND)))
def test_resolvers_have_tenancy_and_permission_guards(path: Path) -> None:
    """Every platform Strawberry resolver must carry both
    ``@tenant_scoped`` and ``@require_permission`` (or be on the
    EXEMPT list with a written reason)."""
    failures: list[str] = []
    for qualname, fn in _resolvers_in_file(path):
        if qualname in EXEMPT:
            continue
        names = _decorator_names(fn)
        missing = []
        if "tenant_scoped" not in names:
            missing.append("@tenant_scoped")
        if "require_permission" not in names:
            missing.append("@require_permission")
        if missing:
            failures.append(
                f"{path.relative_to(BACKEND)}::{qualname} is missing {' and '.join(missing)}"
            )

    if failures:
        msg = (
            "Tenancy/permission guard missing on these resolvers:\n  - "
            + "\n  - ".join(failures)
            + "\n\nIf the resolver legitimately escapes tenant scope, add it to "
            "EXEMPT in this file with a written justification."
        )
        pytest.fail(msg)
