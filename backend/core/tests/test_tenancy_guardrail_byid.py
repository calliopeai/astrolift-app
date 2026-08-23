"""Static-analysis CI guardrail for unscoped by-id/slug row fetches (#1183).

Companion to the two existing tenancy guards:

* ``test_tenancy_guardrail.py`` (#164) — every ``astrolift_*`` Strawberry
  resolver carries ``@require_permission`` + ``@tenant_scoped``.
* ``test_tenancy_guardrail_core.py`` (#537) — every root ``core`` /
  ``organization`` resolver is audited (scoped-at-runtime or exempt).

Those two prove a resolver *has* the decorators / is reviewed. Neither
catches the bug #1118/#1183 exposed: ``@tenant_scoped`` only ASSERTS a
tenant context exists, and ``TenantScopedManager`` is wired on zero
models — so a resolver that does a bare ``Model.objects.filter(guid=id)``
returns rows across orgs (guids are globally unique). Six merge
partitions fixed the real leaks by adding an explicit
``organization_id=org_id`` (fail-closed) clause. THIS guard stops the
class from recurring.

THE RULE
========

For every function in the scanned schema modules that is EITHER

  (A) a Strawberry resolver carrying ``@tenant_scoped``, OR
  (B) any function with a parameter named ``id`` / ``guid`` / ``slug`` /
      ``pk`` / ``*_id`` / ``*_slug``

we AST-walk the body for ``.filter(...)`` / ``.get(...)`` calls and FLAG
a call when it has a keyword argument whose name is exactly one of
``{guid, slug, id, pk}`` — or the bulk-by-global-id form ``guid__in`` /
``slug__in`` (a plural fetch of globally-unique keys leaks across orgs
the same way a single ``guid=`` does) — (FK kwargs like ``registered_app=``
are NOT identifiers) AND the call carries no org-path constraint.

``pk__in`` / ``id__in`` are deliberately NOT flagged: they address rows
by internal integer pk and are dominated by safe batch-resolution of
ids that were already org-scoped when gathered (dataloaders, label
helpers), so flagging them would be pure noise.

A flagged call is a *candidate leak* unless one of the escape hatches
below holds. Each escape hatch mirrors a genuinely-safe pattern already
in the tree.

ESCAPE HATCHES
==============

1. **Org clause on the (chained) query.** Any call in the same query
   chain carries an org constraint: a kwarg whose name contains
   ``organization`` (covers ``organization_id=``,
   ``registered_app__organization_id=``, and ``Q(organization_id=...)``
   nested in a positional arg), a ``pk``/``id``/``scope_id`` kwarg whose
   value is a caller-org variable (``org_id`` etc.) or an
   ``*.organization_id`` attribute, or a positional call to a blessed
   org-scope helper (``_org_scope_q`` …).

2. **Base queryset is already scoped.** The fetch does not start from a
   bare ``Model.objects`` / ``Model.all_objects``: it starts from a local
   variable (an already-scoped queryset), a related manager on a resolved
   instance (``app.approver_users.filter(...)``), or a blessed
   scoped-helper call (``visible_to_org(...)`` …).

3. **Identifier value is caller/user-derived.** The id kwarg's value is
   ``<x>.pk`` (an ``.update()`` re-select of a loaded row), rooted at
   ``tenant`` / ``viewer`` / ``request`` / ``self`` / ``actor*``, or the
   call carries a ``user_id=`` (non-org identity) kwarg.

4. **Staff / superuser gated.** An ``is_superuser=`` / ``is_staff=``
   kwarg on the call, or the enclosing resolver is in ``STAFF_EXEMPT``.

5. **Explicitly vouched.** An in-body ``# tenancy: <reason>`` marker
   comment scoped to the function, OR the function's ``app::qualname`` is
   in the ``EXEMPT`` dict with a written reason.

The ``EXEMPT`` dict is the grandfather set: resolvers that are safe by a
mechanism this per-call rule cannot see (a ``!= org_id`` post-check, a
pre-resolved org-scoped parent, org-narrowing in a following statement,
install-level platform-reference data, or inherited core framework), each
scrutinised and given a reason. Adding an entry is a security-relevant
code-review decision — the default fix when this guard fires is to ADD
the org clause, not to exempt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

# --- The rule's vocabulary -------------------------------------------

# Exact kwarg names that address a row by its identity. FK kwargs
# (``registered_app=``) are deliberately excluded — filtering by a FK is
# not a by-id fetch.
ID_KWARGS: frozenset[str] = frozenset({"guid", "slug", "id", "pk"})

# Bulk-by-global-id: a ``guid__in=`` / ``slug__in=`` fetch is the plural
# form of the same cross-org leak (guids/slugs are globally unique, so a
# caller-supplied id list reaches across orgs). ``pk__in`` / ``id__in``
# are intentionally excluded — those address internal integer pks and are
# dominated by safe batch-resolution of already-scoped ids (dataloaders,
# label helpers), so flagging them would be noise, not signal.
BULK_ID_KWARGS: frozenset[str] = frozenset({"guid__in", "slug__in"})

# Parameter-name shapes that put a plain (non-resolver) function in scope.
_ID_PARAM_EXACT: frozenset[str] = frozenset({"id", "guid", "slug", "pk"})

_FILTER_METHODS: frozenset[str] = frozenset({"filter", "get"})

# Variables that hold the caller's own org id (resolved from tenant
# context). ``Model.objects.filter(pk=org_id)`` is a self-scoped fetch of
# the caller's own org, and ``filter(scope_id=org_id)`` scopes a
# polymorphic scope row to the caller's org.
_ORG_ID_VARS: frozenset[str] = frozenset(
    {"org_id", "org_pk", "organization_id", "active_org_id", "caller_org_id", "caller_org_pk"}
)

# Kwarg names whose *value* we inspect for a caller-org variable / an
# ``*.organization_id`` attribute (rather than the kwarg name itself).
_ORG_VALUE_KWARGS: frozenset[str] = frozenset({"pk", "id", "scope_id", "organization_id", "organization"})

# Non-org identity kwargs — a call that scopes by the caller's user is
# self-service, not a tenant leak.
_USER_ID_KWARGS: frozenset[str] = frozenset(
    {"user_id", "actor_user_id", "triggered_by_user_id", "by_user_id"}
)

# Roots of a caller/user-derived identifier value.
_CALLER_ROOTS: frozenset[str] = frozenset({"tenant", "viewer", "request", "self"})

# Helpers that RETURN a ready-made org-scoping ``Q`` — a positional
# ``.filter(_org_scope_q(org_id))`` is an org clause.
_ORG_SCOPE_HELPERS: frozenset[str] = frozenset(
    {"_org_scope_q", "_viewer_scope_filter", "scope_to_caller_org"}
)

# Helpers that RETURN an already-org-scoped queryset used as the base.
_BLESSED_BASE_HELPERS: frozenset[str] = frozenset(
    {
        "visible_to_org",
        "_agent_workload_qs",
        "_org_qs",
        "_viewer_scope",
        "_scoped_qs",
        "for_org",
        "scoped_to_org",
    }
)

_TENANCY_MARKER = "# tenancy:"


# Staff/superuser-only resolvers: the fetch is unscoped by design because
# the operator is an install admin, not a tenant. Keyed ``app::qualname``.
STAFF_EXEMPT: dict[str, str] = {
    "core::UserMutations.switch_user": (
        "#1593: gated by _may_switch_to — superuser, or shared "
        "Profile.switch_group membership ('users in the same group are "
        "allowed to switch between them'). Not an org-scoped resolver; the "
        "gate is group membership, orthogonal to tenant context. The previous "
        "justification here claimed a core admin/auth gate that did not exist."
    ),
    "core::PermissionAnalysisQuery.effective_permissions": (
        "#537: _require_self_or_superuser gate immediately precedes the "
        "User.objects.filter(pk=...) fetch — self-or-superuser only."
    ),
    "core::PermissionAnalysisQuery.permission_diagnose": (
        "#537: _require_self_or_superuser gate immediately precedes the "
        "User.objects.filter(pk=...) fetch — self-or-superuser only."
    ),
    "astrolift_workflows::WorkflowManifestQuery.export_workflow_manifest": (
        "the unscoped WorkflowDefinition.filter(slug=...) is inside the "
        "``if user.is_superuser:`` branch; the non-superuser branch is "
        "org-scoped (caller org UNION global templates). A static rule "
        "can't see the branch guard."
    ),
}


# Grandfather set: safe by a mechanism the per-call rule can't see. Keyed
# ``app::qualname`` (app = the first path segment, e.g. ``astrolift_registry``,
# ``core``, ``organization``). Each entry was checked to have a real org
# gate — NOT an unfixed leak.
EXEMPT: dict[str, str] = {
    # --- Org self-resolution helpers: resolve the caller's OWN org (a
    #     guid the caller supplied is verified against the tenant's org
    #     pk right after the fetch — cross-org guid reads as not-found).
    "astrolift_agents::_resolve_org": (
        "resolves the caller's own org: after fetching by the supplied "
        "guid it asserts ``org.pk == active`` (the tenant's org) or fails."
    ),
    "astrolift_agents::_caller_org_id": (
        "resolves the caller's own org: fetch-by-guid then ``org.pk`` is "
        "checked against the tenant org; a foreign guid raises."
    ),
    "astrolift_workflows::_resolve_caller_org": (
        "#1042 deny-by-default: fetches the caller's own org by its "
        "``_caller_org_pk()`` pk, then confirms a supplied org_id guid "
        "matches it — cross-org guid returns a failure envelope."
    ),
    "astrolift_workflows::_org_pk_matches": (
        "resolves the caller's own org and confirms a supplied org_id guid "
        "maps to it (``org.pk != caller`` → False); never returns a "
        "foreign org's row."
    ),
    # --- Resolve-by-id then explicit ``!= org_id`` post-check
    #     (fail-closed). The org gate is the comparison a few lines down.
    "astrolift_identity::RoleMutations.update_role": (
        "post-check: ``if role.organization_id != org_id: NOT_FOUND`` "
        "immediately after the by-guid fetch (system roles handled first)."
    ),
    "astrolift_identity::RoleMutations.soft_delete_role": (
        "post-check: ``if role.organization_id != org_id: NOT_FOUND`` after " "the by-guid fetch."
    ),
    "astrolift_identity::RoleBindingMutations.grant_role": (
        "the ``User.objects.filter(pk=target_user_pk)`` fetch is pre-gated "
        "by an org-scoped Member existence check (scope_id=org_id) directly "
        "above — the user is proven to be a member of the caller's org; the "
        "role + scope fetches in the same resolver are org-scoped inline."
    ),
    "astrolift_identity::IdentityQuery.astrolift_organization": (
        "post-check: ``if org.id != org_id: return None`` after the "
        "by-slug fetch — the caller may read only their own org by slug."
    ),
    "astrolift_identity::IdentityQuery.astrolift_org_members_for_approval_picker": (
        "post-check: ``if org.id != tenant_org_id`` after the by-slug fetch "
        "— fails closed without leaking slug existence."
    ),
    "astrolift_identity::IdentityQuery.astrolift_team_members": (
        "post-check: ``if team.organization_id != org_id: return []`` after "
        "the by-guid fetch — a foreign team reads as empty."
    ),
    "astrolift_operations::NotificationMutations.test_notification_channel": (
        "post-check: ``if tenant.organization_id != org.id`` after the " "by-guid org fetch."
    ),
    "astrolift_registry::_resolve_approval_inputs": (
        "post-check: the approver team is fetched by guid then required to "
        "match ``organization.id`` (the app's org) — cross-org team is a "
        "field-tagged PRECONDITION."
    ),
    "astrolift_registry::RegistrationMutations.register_app": (
        "post-check: ``if project.organization_id != org_id: NOT_FOUND`` — "
        "the new app inherits project.organization, so the project gate is "
        "the org boundary."
    ),
    "astrolift_registry::RegistrationMutations.register_agent_repo": (
        "post-check: ``if project.organization_id != active_org_id`` — same "
        "org boundary as register_app; the repo is fetched via the org's "
        "own source connection."
    ),
    "astrolift_registry::RegistrationMutations.register_app_repo": (
        "post-check: project (and team) resolved by guid then required to "
        "belong to the caller's active org before any app is created."
    ),
    "astrolift_registry::AppMutations.transfer_app": (
        "source app is org-scoped (organization_id=org_id); the target "
        "team/project fetched by guid are then required to match "
        "``app.organization_id`` — cross-org transfer is refused."
    ),
    "astrolift_registry::TeamAccessMutations.move_app_to_team": (
        "source app is org-scoped (organization_id=org_id); the target team "
        "fetched by guid must match ``app.organization_id`` — cross-org "
        "move refused."
    ),
    "astrolift_registry::TeamAccessMutations.grant_team_access_to_app": (
        "source app is org-scoped (organization_id=org_id); the team fetched "
        "by guid must match ``app.organization_id`` before the grant."
    ),
    "astrolift_registry::TeamAccessMutations.revoke_team_access_from_app": (
        "source app is org-scoped (organization_id=org_id); the team fetched "
        "by guid must match ``app.organization_id`` before the revoke."
    ),
    "astrolift_registry::AppSettingMutations.assign_astrolift_app_to_project": (
        "app + target project are org-checked against the caller's tenant "
        "before the reassignment (foreign guids read as not-found)."
    ),
    "astrolift_lifecycle::EnvironmentSettingMutations.set_environment_setting": (
        "post-check: ``if env.registered_app.organization_id != "
        "tenant.organization_id: NOT_FOUND`` after the by-guid env fetch."
    ),
    "astrolift_lifecycle::EnvironmentSettingMutations.clear_environment_setting": (
        "post-check: ``if env.registered_app.organization_id != "
        "tenant.organization_id: NOT_FOUND`` after the by-guid env fetch."
    ),
    # --- Parent-scoped: the fetch is constrained by an already
    #     org-resolved parent object.
    "astrolift_identity::ProjectMutations.create_project": (
        "parent-scoped: ``Project.objects.filter(team=team, slug=...)`` where "
        "``team`` was resolved org-scoped via _resolve_team(team_id, org_id) "
        "just above — a slug-uniqueness check within the caller's own team."
    ),
    "astrolift_identity::RoleBindingMutations.bulk_assign_astrolift_team_member_roles": (
        "parent-scoped bulk fetch: the ``Member.objects.filter(guid__in=...)`` "
        "is constrained to ``scope_kind=TEAM, scope_id=team.pk`` where ``team`` "
        "was resolved org-scoped via _resolve_team(team_id, org_id) above, so a "
        "member guid from a sibling team/org simply doesn't match and reads as "
        "not-found. The per-call rule can't see that ``scope_id=team.pk`` is an "
        "org-bound scope (the value is an instance attr, not a caller-org var)."
    ),
    "astrolift_lifecycle::TaskMutations.run_task": (
        "parent-scoped: ``Workload.objects.filter(slug=..., "
        "registered_app=app)`` where ``app`` was resolved org-scoped just "
        "above — the workload is reachable only through the caller's app."
    ),
    # --- Org-narrowing in a following statement (``qs = M.objects.filter
    #     (guid=..)`` then ``if org_id: qs = qs.filter(...__organization_id
    #     =org_id)``). The narrowing is a separate statement the per-call
    #     rule can't chain to.
    "astrolift_lifecycle::LifecycleQuery.astrolift_compare_deployments": (
        "org-narrowed below: the nested ``_get_deploy`` helper filters by "
        "``registered_app__organization_id=org_id`` after the by-guid fetch "
        "when a tenant org is present."
    ),
    # --- Authorization gate on the resolved row (workflow ops).
    "astrolift_workflows::WorkflowsMutation.update_workflow_stage": (
        "gated by ``_definition_write_error(user, stage.definition)`` after "
        "the by-guid stage fetch — the write-permission check on the "
        "stage's definition is the org boundary; the nested workload fetch "
        "is org-scoped inline."
    ),
    "astrolift_workflows::WorkflowsMutation.delete_workflow_stage": (
        "gated by ``_definition_write_error(user, stage.definition)`` after "
        "the by-guid stage fetch — same as update_workflow_stage."
    ),
    # --- Install-level platform-reference data (not org-owned).
    "astrolift_clusters::ClustersMutation.configure_provider_plugin": (
        "ProviderPlugin is install-wide platform-reference data (not "
        "org-owned) — same category as the astrolift_provider_plugins "
        "resolver on #164's EXEMPT list."
    ),
    "astrolift_clusters::ClustersMutation.register_tenant_cluster": (
        "the by-slug fetches are ProviderPlugin (install-wide reference "
        "data) and a global TenantCluster slug-uniqueness check "
        "(``.exists()``; cluster slug is globally unique). The new cluster's "
        "org is set from ``organization_scoped`` + the caller's tenant."
    ),
    "astrolift_identity::IdentityQuery.astrolift_active_identity_provider": (
        "install-level: returns the install's active IdP singleton "
        "(_active_idp_pk()) for the pre-auth login screen — on #164's "
        "EXEMPT list; renders before any tenant is picked, no tenant data."
    ),
    # --- Inherited core (CDP heritage) framework: generic, not part of
    #     astrolift's org-scoped surface; gated by the core permission
    #     framework, addressed via ContentType / global-id.
    "core::GlobalIDUtils.find_object_by_global_id": (
        "generic relay/global-id → model resolver (core framework): decodes "
        "an arbitrary (type, pk) global id. Callers are responsible for "
        "scoping; not an astrolift org-scoped resolver."
    ),
    "core::resolve_instance_from_id": (
        "generic serializer-mutation base helper (core framework): resolves "
        "a model instance by pk for the DRF-serializer mutation path; "
        "gated by the serializer/permission layer, not org-scoped."
    ),
    "core::UploadMutations.pre_signed_url_image_upload": (
        "generic core upload framework: attaches an upload to an arbitrary "
        "(ContentType, pk) owner resolved from a global id; gated by "
        "create_upload's permission checks, not part of astrolift's org "
        "model."
    ),
    "core::UploadMutations.profile_image_field_upload": (
        "core profile-image upload framework: operates on the Profile "
        "resolved from the global id (self-service profile edit), gated by "
        "the core approval/permission flow."
    ),
}


# --- AST helpers (shared idiom with #164 / #537 / the webhook guard) ---


def _scan_files() -> list[Path]:
    """Schema modules to audit: every non-test ``.py`` under the platform,
    core, and organization schema trees (mirrors #1183's stated scope)."""
    out: set[Path] = set()
    for pattern in ("astrolift_*/schema", "core/schema", "organization/schema"):
        for base in BACKEND.glob(pattern):
            for path in base.rglob("*.py"):
                s = str(path)
                if "/tests/" in s or path.name.startswith("test_"):
                    continue
                out.add(path)
    return sorted(out)


def _app_label(path: Path) -> str:
    return path.relative_to(BACKEND).parts[0]


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_strawberry_field(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in {"strawberry", "strawberry_django"}
            and target.attr in {"field", "mutation", "subscription"}
        ):
            return True
    return False


def _decorator_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {_decorator_name(d) for d in fn.decorator_list}


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    names = {arg.arg for grp in (a.posonlyargs, a.args, a.kwonlyargs) for arg in grp}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def _has_id_param(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(n in _ID_PARAM_EXACT or n.endswith(("_id", "_slug")) for n in _param_names(fn))


def _in_scope(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if _is_strawberry_field(fn) and "tenant_scoped" in _decorator_names(fn):
        return True  # (A)
    return _has_id_param(fn)  # (B)


def _scanned_functions(tree: ast.Module):
    """Yield (qualname, fn) for every module-level function and every
    method of a top-level class. Nested functions are NOT scanned as their
    own units — their bodies roll up into the enclosing scanned function."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{m.name}", m


def _flagged_calls(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    """Yield (call, id_keyword) for each filter/get call carrying an exact
    id kwarg. Walks the whole body (incl. nested helpers/comprehensions)."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _call_name(node.func) in _FILTER_METHODS:
            for kw in node.keywords:
                if kw.arg in ID_KWARGS or kw.arg in BULK_ID_KWARGS:
                    yield node, kw
                    break


def _chain_calls(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> list[ast.Call]:
    """All calls in the fluent chain the flagged call belongs to — both
    the receiver side (``M.objects.filter(guid=..).exclude(..)``) and the
    result side (``..filter(guid=..).filter(org_q)``)."""
    calls: list[ast.Call] = []
    node: ast.AST | None = call
    while isinstance(node, ast.Call):
        calls.append(node)
        node = node.func.value if isinstance(node.func, ast.Attribute) else None
    node = call
    while True:
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            grandparent = parents.get(parent)
            if isinstance(grandparent, ast.Call) and grandparent.func is parent:
                calls.append(grandparent)
                node = grandparent
                continue
        break
    return calls


def _chain_root(call: ast.Call) -> ast.expr:
    node: ast.expr = call
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        node = node.func.value
    return node


def _is_org_kw_name(name: str | None) -> bool:
    return bool(name) and "organization" in name


def _is_org_value(value: ast.expr) -> bool:
    if isinstance(value, ast.Name) and value.id in _ORG_ID_VARS:
        return True
    return isinstance(value, ast.Attribute) and "organization" in value.attr


def _org_clause_in_chain(chain: list[ast.Call]) -> bool:
    for call in chain:
        for kw in call.keywords:
            if _is_org_kw_name(kw.arg):
                return True
            if kw.arg in _ORG_VALUE_KWARGS and _is_org_value(kw.value):
                return True
        for subtree in list(call.args) + [kw.value for kw in call.keywords]:
            for sub in ast.walk(subtree):
                if isinstance(sub, ast.keyword) and _is_org_kw_name(sub.arg):
                    return True
                if isinstance(sub, ast.Call) and _call_name(sub.func) in _ORG_SCOPE_HELPERS:
                    return True
    return False


def _base_is_scoped(call: ast.Call) -> bool:
    """EH2: the query does not start from a bare default manager."""
    root = _chain_root(call)
    if isinstance(root, ast.Name):
        return True  # local (already-scoped) queryset var
    if isinstance(root, ast.Attribute):
        # ``Model.objects`` / ``Model.all_objects`` is the UNSCOPED base we
        # want to catch; any other attribute (a related manager on a
        # resolved instance, a scoped property) is scoped.
        return root.attr not in {"objects", "all_objects"}
    if isinstance(root, ast.Call):
        return _call_name(root.func) in _BLESSED_BASE_HELPERS
    return False


def _value_is_caller_derived(id_kw: ast.keyword, chain: list[ast.Call]) -> bool:
    """EH3: the identifier value is the caller's own user/session."""
    value = id_kw.value
    if isinstance(value, ast.Attribute) and value.attr == "pk":
        return True
    root: ast.expr = value
    while isinstance(root, ast.Attribute):
        root = root.value
    if isinstance(root, ast.Name) and (root.id in _CALLER_ROOTS or root.id.startswith("actor")):
        return True
    return any(kw.arg in _USER_ID_KWARGS for call in chain for kw in call.keywords)


def _is_staff_gated(qualname_key: str, chain: list[ast.Call]) -> bool:
    """EH4."""
    if qualname_key in STAFF_EXEMPT:
        return True
    return any(kw.arg in {"is_superuser", "is_staff"} for call in chain for kw in call.keywords)


def _has_tenancy_marker(source_lines: list[str], fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """EH5 (marker half): a ``# tenancy:`` comment inside the function's
    line span. Scoped by line range so resolver methods that share a name
    across classes can't borrow each other's marker (the webhook guard
    scopes by ``def name(`` string search; line-range is stricter)."""
    end = fn.end_lineno or fn.lineno
    for i in range(fn.lineno - 1, end):
        if i < len(source_lines) and _TENANCY_MARKER in source_lines[i]:
            return True
    return False


def _residuals_in_source(src: str, app_label: str) -> list[tuple[str, int, str]]:
    """Return (qualname, lineno, id_kwarg) for every flagged call not
    cleared by an escape hatch. The whole rule, runnable on any source
    string (so the guard is unit-testable without touching the repo)."""
    tree = ast.parse(src)
    source_lines = src.splitlines()
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    residuals: list[tuple[str, int, str]] = []
    for qualname, fn in _scanned_functions(tree):
        if not _in_scope(fn):
            continue
        key = f"{app_label}::{qualname}"
        marker = _has_tenancy_marker(source_lines, fn)
        for call, id_kw in _flagged_calls(fn):
            chain = _chain_calls(call, parents)
            if _org_clause_in_chain(chain):
                continue
            if _base_is_scoped(call):
                continue
            if _value_is_caller_derived(id_kw, chain):
                continue
            if _is_staff_gated(key, chain):
                continue
            if marker or key in EXEMPT:
                continue
            residuals.append((qualname, call.lineno, id_kw.arg))
    return residuals


# --- The guard --------------------------------------------------------


@pytest.mark.parametrize("path", _scan_files(), ids=lambda p: str(p.relative_to(BACKEND)))
def test_no_unscoped_by_id_fetches(path: Path) -> None:
    """Every by-id/slug fetch in an in-scope schema function carries an
    org clause (or clears an escape hatch). A failure means a resolver
    fetches a globally-unique key with no tenant boundary — a cross-org
    leak. Fix: add the ``organization_id=org_id`` clause (fail-closed),
    or — if genuinely safe — a ``# tenancy:`` marker or an EXEMPT entry
    with a written reason."""
    residuals = _residuals_in_source(path.read_text(), _app_label(path))
    if residuals:
        rel = path.relative_to(BACKEND)
        lines = "\n  - ".join(f"{rel}::{q}  (line {ln}, {arg}=…)" for q, ln, arg in residuals)
        pytest.fail(
            "Unscoped by-id/slug fetch(es) with no org clause:\n  - "
            + lines
            + "\n\nFix: add an org constraint on the same query "
            "(``organization_id=org_id`` / ``Q(organization_id=org_id) | "
            "Q(organization_id__isnull=True)``), fail-closed. If the fetch "
            "is genuinely safe (staff-gated / user-scoped / parent-scoped / "
            "post-checked), add a ``# tenancy: <reason>`` marker in the "
            "function or an EXEMPT entry keyed ``app::qualname`` with a "
            "written reason. The default is to ADD the clause, not exempt."
        )


def test_guard_scans_a_meaningful_surface() -> None:
    """Fail loudly if discovery finds ~nothing — a refactor that moves the
    schema tree must not silently disarm the guard."""
    files = _scan_files()
    assert len(files) > 20, f"expected many schema modules, found {len(files)}"
    total_in_scope = 0
    for path in files:
        tree = ast.parse(path.read_text())
        total_in_scope += sum(1 for _q, fn in _scanned_functions(tree) if _in_scope(fn))
    assert total_in_scope > 100, f"expected 100s of in-scope functions, found {total_in_scope}"


def test_every_exempt_entry_is_still_reachable() -> None:
    """Ratchet: every EXEMPT / STAFF_EXEMPT key must still correspond to a
    real in-scope function that WOULD flag without the exemption. Stale
    entries (renamed/removed/newly-scoped resolvers) must be pruned so the
    dict never accumulates dead cover."""
    would_flag: set[str] = set()
    ledgers = {**EXEMPT, **STAFF_EXEMPT}
    for path in _scan_files():
        app_label = _app_label(path)
        src = path.read_text()
        tree = ast.parse(src)
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        source_lines = src.splitlines()
        for qualname, fn in _scanned_functions(tree):
            if not _in_scope(fn):
                continue
            key = f"{app_label}::{qualname}"
            if key not in ledgers:
                continue
            for call, id_kw in _flagged_calls(fn):
                chain = _chain_calls(call, parents)
                if _org_clause_in_chain(chain):
                    continue
                if _base_is_scoped(call):
                    continue
                if _value_is_caller_derived(id_kw, chain):
                    continue
                if any(kw.arg in {"is_superuser", "is_staff"} for c in chain for kw in c.keywords):
                    continue
                if _has_tenancy_marker(source_lines, fn):
                    continue
                would_flag.add(key)
                break

    stale = sorted(set(ledgers) - would_flag)
    assert not stale, (
        "These EXEMPT / STAFF_EXEMPT entries no longer correspond to a "
        "flagged by-id fetch (resolver renamed, removed, or since properly "
        "scoped). Prune them:\n  - " + "\n  - ".join(stale)
    )


# --- Tests on the guard itself (synthetic modules) --------------------
#
# The guard is CI infrastructure; if its matching logic rots, every
# resolver ships without by-id coverage. These exercise each arm on tiny
# synthetic sources so a regression in the AST logic is caught here.


def _residuals(src: str) -> list[tuple[str, int, str]]:
    return _residuals_in_source(src, app_label="synthetic")


def test_flags_bare_filter_by_guid() -> None:
    """(A): a ``@tenant_scoped`` resolver doing a bare by-guid fetch flags."""
    src = (
        "@strawberry.type\n"
        "class Q:\n"
        "    @strawberry.field\n"
        "    @tenant_scoped()\n"
        "    def get_it(self, info, input):\n"
        "        return Thing.objects.filter(guid=str(input.id)).first()\n"
    )
    res = _residuals(src)
    assert len(res) == 1 and res[0][2] == "guid", res


def test_flags_helper_with_id_param() -> None:
    """(B): a plain helper with an id-shaped param is in scope."""
    src = "def _resolve(thing_id):\n    return Thing.objects.filter(pk=thing_id).first()\n"
    assert [r[2] for r in _residuals(src)] == ["pk"]


def test_ignores_function_with_no_id_param_and_no_decorator() -> None:
    src = "def helper(info):\n    return Thing.objects.filter(guid=info.x).first()\n"
    assert _residuals(src) == []


def test_fk_kwarg_is_not_an_identifier() -> None:
    """``registered_app=`` is a FK, not a by-id identifier — not flagged."""
    src = "def f(app_id):\n    return Env.objects.filter(registered_app=app_id).first()\n"
    assert _residuals(src) == []


def test_flags_bulk_by_guid_in() -> None:
    """A bare ``filter(guid__in=ids)`` is the plural form of the leak."""
    src = "def _resolve(binding_id):\n    return list(Thing.objects.filter(guid__in=[binding_id]))\n"
    assert [r[2] for r in _residuals(src)] == ["guid__in"]


def test_bulk_pk_in_is_not_flagged() -> None:
    """``pk__in`` / ``id__in`` address internal integer pks (safe batch
    resolution of already-scoped ids) — deliberately out of scope so the
    guard doesn't drown in dataloader/label-helper false positives."""
    src = "def _resolve(user_id):\n    return list(User.objects.filter(pk__in=[user_id]))\n"
    assert _residuals(src) == []


def test_bulk_by_guid_in_cleared_by_org_scope_helper() -> None:
    """The same escape hatches apply to the bulk form: a chained
    ``_org_scope_q(org_id)`` clears a ``guid__in`` fetch."""
    src = (
        "def _resolve(binding_id, org_id):\n"
        "    return list(RoleBinding.objects.filter(guid__in=[binding_id])"
        ".filter(_org_scope_q(org_id)))\n"
    )
    assert _residuals(src) == []


def test_cleared_by_direct_org_kwarg() -> None:
    src = (
        "def f(app_id, org_id):\n    return App.objects.filter(guid=app_id, organization_id=org_id).first()\n"
    )
    assert _residuals(src) == []


def test_cleared_by_nested_q_org_clause() -> None:
    src = (
        "def f(role_id, org_id):\n"
        "    return (Role.objects.filter(slug=role_id)\n"
        "            .filter(Q(organization_id=org_id) | Q(organization__isnull=True)).first())\n"
    )
    assert _residuals(src) == []


def test_cleared_by_org_scope_helper_positional() -> None:
    src = "def f(bid, org_id):\n    return RoleBinding.objects.filter(guid=bid).filter(_org_scope_q(org_id)).first()\n"
    assert _residuals(src) == []


def test_cleared_by_org_id_valued_pk() -> None:
    """``Organization.objects.filter(pk=org_id)`` — self-fetch of the
    caller's own org."""
    src = "def f(org_id):\n    return Organization.objects.filter(pk=org_id).first()\n"
    assert _residuals(src) == []


def test_cleared_by_scope_id_valued_org() -> None:
    src = "def f(inv_id, org_id):\n    return Invite.objects.filter(guid=inv_id, scope_id=org_id).first()\n"
    assert _residuals(src) == []


def test_cleared_by_local_var_base() -> None:
    src = (
        "def f(guid, org_id):\n"
        "    qs = App.objects.filter(organization_id=org_id)\n"
        "    return qs.filter(guid=guid).first()\n"
    )
    assert _residuals(src) == []


def test_cleared_by_related_manager_base() -> None:
    """``app.approver_users.filter(pk=user_id)`` — scoped to the resolved
    instance."""
    src = "def f(app, user_id):\n    return app.approver_users.filter(pk=user_id).exists()\n"
    assert _residuals(src) == []


def test_cleared_by_caller_derived_value() -> None:
    src = "def f(info):\n    return Session.objects.filter(pk=info.viewer.pk).first()\n"
    assert _residuals(src) == []


def test_cleared_by_user_id_kwarg() -> None:
    src = "def f(row_id, user_id):\n    return Pref.objects.filter(id=row_id, user_id=user_id).first()\n"
    assert _residuals(src) == []


def test_cleared_by_staff_kwarg() -> None:
    src = "def f(uid):\n    return User.objects.filter(pk=uid, is_superuser=True).first()\n"
    assert _residuals(src) == []


def test_cleared_by_tenancy_marker() -> None:
    src = (
        "def _resolve(org_id):\n"
        "    # tenancy: resolves the caller's own org; verified vs tenant below\n"
        "    return Organization.objects.filter(guid=str(org_id)).first()\n"
    )
    assert _residuals(src) == []
    # …and without the marker the same body flags.
    src_bare = "def _resolve(org_id):\n    return Organization.objects.filter(guid=str(org_id)).first()\n"
    assert len(_residuals(src_bare)) == 1


def test_marker_is_scoped_to_its_function() -> None:
    """A marker in one function must not clear a bare fetch in another."""
    src = (
        "def safe(org_id):\n"
        "    # tenancy: verified\n"
        "    return Organization.objects.filter(guid=str(org_id)).first()\n"
        "def leaky(thing_id):\n"
        "    return Thing.objects.filter(guid=str(thing_id)).first()\n"
    )
    res = _residuals(src)
    assert [r[0] for r in res] == ["leaky"], res


def test_exempt_key_clears() -> None:
    """An ``app::qualname`` in EXEMPT clears; a real entry is exercised."""
    src = "def _resolve_org(org_id):\n    return Organization.objects.filter(guid=str(org_id)).first()\n"
    # Not exempt under a synthetic app label…
    assert len(_residuals_in_source(src, app_label="synthetic")) == 1
    # …but the real ``astrolift_agents::_resolve_org`` entry clears it.
    assert _residuals_in_source(src, app_label="astrolift_agents") == []
