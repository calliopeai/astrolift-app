"""Static-analysis CI guardrail for ``core/schema`` + ``organization/schema``.

The original guardrail (``core/tests/test_tenancy_guardrail.py``, #164)
walks ``astrolift_*/schema/{queries,mutations}.py`` and enforces a
``@require_permission`` + ``@tenant_scoped`` stack on every resolver.
The ``core`` and ``organization`` schemas were left out at the time
because they predate the decorator stack — and #537 showed up exactly
in that gap, with ``UploadQuerySet.with_view_permission`` silently
returning the cross-tenant table.

This guardrail walks the modules in those two trees and enforces a
different, narrower contract: every list-returning resolver on the root
``Query`` type must apply an explicit scope. We can't require the
``@tenant_scoped`` decorator here because the legacy modules pre-date
it; instead we maintain an EXEMPT list with a written justification per
resolver.

When a new list-returning resolver lands in ``core/schema/`` or
``organization/schema/``, the author MUST either:

  * write a tenant-scoped filter in the resolver body, then add the
    qualified name to ``SCOPED_AT_RUNTIME`` with a one-line reason; OR
  * add it to ``EXEMPT`` with a written justification.

Adding either is a code-review decision — the CI ratchet keeps
unreviewed leaks out of the merge queue.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


# Resolvers we've audited and confirmed apply a tenant-scoped filter
# inline (because the @tenant_scoped decorator wasn't introduced when
# these modules were written). Each entry is a one-line reason.
SCOPED_AT_RUNTIME: dict[str, str] = {
    "Query.organization": "#537 — _caller_org_ids gate against caller memberships",
    "Query.organizations": "#537 — filters by id__in=_caller_org_ids(info)",
    "Query.members": "#537 — filters by organization_id__in=_caller_org_ids(info)",
    "Query.employees": "#537 — filters by organization_id__in=_caller_org_ids(info)",
    "Mutation.organization": "#537 — _require_caller_in_org for edits; creates allowed for any authed user",
    "Mutation.upsert_organization": "#537 — _require_caller_in_org when editing existing org row",
    "Mutation.organization_member_status": "#537 — _require_caller_in_org against the target org",
}


# Resolvers that legitimately escape tenant scope. Mirrors the original
# guardrail's EXEMPT list; each entry needs a written reason.
EXEMPT: dict[str, str] = {
    # Audit log is install-operator surface, gated on superuser inline
    # (#537). Not tenant-scoped because cross-tenant inspection IS the
    # point — the install operator needs to see every mutation.
    "AuditLogQuery.audit_logs": (
        "#537: install-operator surface; superuser-gated inline. "
        "Cross-tenant inspection is the intended use case."
    ),
    # Permission analysis is gated inline on self-or-superuser (#537).
    # The shape is "diagnose THIS user", not "list rows" — tenant
    # scoping doesn't fit the shape.
    "PermissionAnalysisQuery.effective_permissions": (
        "#537: self-or-superuser gate inline; not a row-listing resolver."
    ),
    "PermissionAnalysisQuery.permission_diagnose": (
        "#537: self-or-superuser gate inline; not a row-listing resolver."
    ),
    "PermissionAnalysisQuery.permission_compare": (
        "#537: superuser-only gate inline; cross-user by construction."
    ),
    # Install handshake is unauthenticated by design (see #479 + the
    # module docstring on AstroliftServerInfoQuery).
    "AstroliftServerInfoQuery.astrolift_server_info": (
        "#479: unauthenticated install handshake; carries no operator-"
        "sensitive data. Marked with @allow_anonymous in the module."
    ),
}


def _schema_files() -> list[Path]:
    """Files we audit. Limit to root-level Query/Mutation modules — type
    definition modules (``core/schema/types/*``) declare ``get_queryset``
    methods, not top-level resolvers, and the bug class we're catching
    is at the root resolver entry point.
    """
    targets: list[Path] = []
    # organization/schema/queries.py + mutations.py
    org_schema = BACKEND / "organization" / "schema"
    for name in ("queries.py", "mutations.py"):
        p = org_schema / name
        if p.exists():
            targets.append(p)
    # core root-level query modules (the ones wired into config.schema)
    core_query_modules = [
        BACKEND / "core" / "schema" / "types" / "audit.py",
        BACKEND / "core" / "schema" / "types" / "permission_analysis.py",
        BACKEND / "core" / "schema" / "types" / "server_info.py",
    ]
    targets.extend(p for p in core_query_modules if p.exists())
    return sorted(targets)


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_strawberry_field(decorators: list[ast.expr]) -> bool:
    for d in decorators:
        target = d.func if isinstance(d, ast.Call) else d
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in {"strawberry", "strawberry_django"}
            and target.attr in {"field", "mutation", "subscription"}
        ):
            return True
    return False


def _is_query_or_mutation_class(cls: ast.ClassDef) -> bool:
    """We only care about root-level Query/Mutation types in this audit.

    Type modules under ``core/schema/types/`` define both data shapes
    (e.g. ``UploadType``) and root queries (e.g. ``AuditLogQuery``).
    The bug class we're catching lives on root queries — get_queryset
    hooks on data shapes are exercised via the cross-tenant tests in
    ``test_tenant_isolation.py``.
    """
    for d in cls.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "strawberry"
            and target.attr == "type"
        ):
            return cls.name.endswith("Query") or cls.name.endswith("Mutation") or cls.name == "Query"
    return False


def _walk_root_classes(tree: ast.Module) -> list[ast.ClassDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _is_query_or_mutation_class(node)
    ]


def _resolver_methods(cls: ast.ClassDef) -> list[ast.FunctionDef]:
    return [
        m
        for m in cls.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_strawberry_field(m.decorator_list)
    ]


def _resolvers_in_file(path: Path) -> list[tuple[str, ast.FunctionDef]]:
    tree = ast.parse(path.read_text())
    out: list[tuple[str, ast.FunctionDef]] = []
    for cls in _walk_root_classes(tree):
        for m in _resolver_methods(cls):
            out.append((f"{cls.name}.{m.name}", m))
    return out


@pytest.mark.parametrize(
    "path",
    _schema_files(),
    ids=lambda p: str(p.relative_to(BACKEND)),
)
def test_core_org_resolvers_are_audited(path: Path) -> None:
    """Every root-level Query/Mutation resolver in core+organization
    must appear in ``SCOPED_AT_RUNTIME`` or ``EXEMPT``.

    The point isn't that the resolver MUST be tenant-scoped — some
    legitimately aren't (audit log, server info handshake). The point
    is that the author + reviewer made the call deliberately and the
    decision is grepable.
    """
    failures: list[str] = []
    for qualname, _fn in _resolvers_in_file(path):
        if qualname in SCOPED_AT_RUNTIME:
            continue
        if qualname in EXEMPT:
            continue
        failures.append(
            f"{path.relative_to(BACKEND)}::{qualname} is not audited. "
            "Add it to SCOPED_AT_RUNTIME (with a one-line reason) if it "
            "filters rows inline, or to EXEMPT (with a written reason) "
            "if it intentionally escapes tenant scope."
        )
    if failures:
        pytest.fail("\n  - " + "\n  - ".join(failures))
