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
    "MeType.modules": (
        "self-service (spec 34/36 §0.3): the ``me.modules`` capability "
        "manifest computes the caller's OWN module entitlements for the "
        "active tenant via resolve_effective_permissions — same category "
        "as astrolift_my_permissions. Cannot be @tenant_scoped: the spec "
        "requires anonymous / no-tenant to return ``[]`` (the shell still "
        "renders Dashboard), whereas @tenant_scoped would RAISE "
        "TenantRequired. The viewer's own effective permissions ARE the "
        "gate; no other tenant's data is reachable."
    ),
    "IdentityQuery.astrolift_my_profile": "self-service: editable profile fields",
    "IdentityQuery.my_memberships": (
        "self-service: enumerates orgs the caller belongs to — needed before any tenant context can be picked"
    ),
    "IdentityQuery.astrolift_organizations": (
        "self-service bootstrap: returns the orgs the caller can pick as "
        "the active tenant (superusers see all; regular users see only "
        "orgs they're an active Member of). Can't be @tenant_scoped — "
        "it's the chicken-and-egg resolver the FE calls to discover which "
        "org to set as the tenant in the first place. The Member filter "
        "on the caller's pk IS the gate; same category as my_memberships."
    ),
    "IdentityQuery.astrolift_my_permissions": (
        "self-service: returns the caller's effective permissions — "
        "ordering matters because permissions drive nav rendering"
    ),
    "IdentityQuery.astrolift_nav_tree": (
        "self-service: returns the shape of the tenant the caller is "
        "already scoped to. tenant_scoped is the visibility boundary; "
        "every authed user is allowed to see the Org→Team→Project→App "
        "tree of their own org so the sidebar can render. Resource-"
        "level reads still flow through permission-gated resolvers."
    ),
    "IdentityQuery.astrolift_active_identity_provider": (
        "renders before any tenant is picked — the IdP catalogue is the entry point to the login flow"
    ),
    "IdentityQuery.astrolift_active_sessions": (
        "self-service: caller's own django_session rows, filtered by viewer pk; orthogonal to tenant context"
    ),
    "IdentityMutation.update_my_profile": (
        "self-service: callers can edit their own profile fields when the IdP doesn't lock them"
    ),
    "IdentityMutation.generate_install_enrollment_qr": (
        "#494 self-service: any authed user can enroll their own mobile device — the "
        "operator's web session IS the proof, same pattern as update_my_profile. The "
        "permission check is implicit (authenticated user), not org-scoped; per-user "
        "rate limit (ENROLLMENT_MAX_ACTIVE_PER_USER) caps abuse. The resulting "
        "session inherits the operator's active org when one is set, so the redeemed "
        "token is tenant-scoped at issue time even though the mutation isn't."
    ),
    "IdentityMutation.logout_all_sessions": (
        "self-service: revokes the caller's own sessions; tenant context "
        "is irrelevant — a session is bound to a user, not an org"
    ),
    "IdentityMutation.revoke_astrolift_session": (
        "self-service OR cross-tenant org-admin: callers can revoke their "
        "own sessions regardless of active org (you can sign out a stale "
        "phone before re-entering a tenant context). Org-admin revoke "
        "uses the active tenant for the org-membership check inline; the "
        "decorator can't gate it because the permitted path branches on "
        "is-owner-vs-is-org-admin at resolver entry."
    ),
    "IdentityMutation.heartbeat_session": (
        "self-service: a session is bound to a user, not an org; the "
        "ping is the caller asserting their own liveness"
    ),
    "IdentityMutation.elevate_admin_session": (
        "#487 step-up auth: elevates the caller's own session timer; "
        "tenant context is irrelevant — elevation is session-scoped, "
        "not org-scoped. The credential verifier IS the gate; "
        "@require_permission would need a special 'can elevate' "
        "permission that every authed user trivially has."
    ),
    "IdentityMutation.deelevate_admin_session": (
        "#487 step-up auth: the 'log me out of admin' counterpart to "
        "elevate_admin_session — self-service, tenant-orthogonal, no "
        "permission gate (every authed user can drop their own "
        "elevation)."
    ),
    "IdentityMutation.request_attestation_challenge": (
        "#496 device attestation: issues a one-shot nonce bound to "
        "the caller's user + chosen kind. Self-service, tenant-"
        "orthogonal — the mobile app attests its own session before "
        "any tenant context can be picked. Per-user rate via the "
        "ATTESTATION_CHALLENGE_TTL_SECONDS knob caps abuse."
    ),
    "IdentityMutation.attest_session": (
        "#496 device attestation: submits the iOS App Attest / Android "
        "Play Integrity blob for the caller's own session. Self-"
        "service, tenant-orthogonal — the session is bound to a user, "
        "not an org, and the verifier itself (Apple/Google signature) "
        "is the gate. The nonce-consume step rejects replays + "
        "wrong-user submissions inline."
    ),
    "IdentityMutation.assert_session": (
        "#496 device attestation: periodic iOS App Attest assertion "
        "against the caller's own session. Self-service, tenant-"
        "orthogonal — same rationale as attest_session; the stored "
        "public key + monotonic counter are the gate."
    ),
    "IdentityQuery.astrolift_elevation_status": (
        "#487 step-up auth: returns the caller's own session elevation "
        "snapshot. Self-service, tenant-orthogonal — drives the nav "
        "indicator that has to render before any tenant context is "
        "picked (e.g. on the org switcher itself)."
    ),
    "IdentityMutation.set_active_organization": (
        "the act of selecting a tenant context cannot itself be tenant-scoped"
    ),
    "IdentityMutation.create_organization": (
        "creates the tenant — by definition there is no tenant context yet at the time of this call"
    ),
    "IdentityMutation.update_organization": (
        "operates on the org row directly; permission check restricts to org admins of that specific org"
    ),
    "IdentityMutation.soft_delete_organization": (
        "operates on the org row directly; permission check restricts to org admins of that specific org"
    ),
    "IdentityMutation.accept_invitation": (
        "the invitation token is the auth proof; by definition the "
        "accepting user has no tenant context yet at the moment they "
        "click the link. Email match against the caller's account "
        "prevents leaked-token redemption against another account."
    ),
    # Notifications are user-scoped: the resolver filters by the
    # caller's user_id directly, not by tenant.
    "OperationsQuery.astrolift_my_notifications": "self-service: caller's own notifications",
    # Push device registry / notification prefs / alert subscriptions
    # (#476, #490, #499, #747) — all self-service: each resolver filters
    # by the caller's own user (viewer.pk / tenant.actor_user_id), never
    # by org. A DeviceRegistration / NotificationPreference / alert
    # subscription belongs to a *user*, not a tenant, so there is no org
    # to scope on. Same category as astrolift_my_notifications. Auth is
    # is_authenticated (every authed user may see/manage their own); no
    # meaningful org-scoped permission applies.
    "OperationsQuery.astrolift_my_mobile_devices": (
        "self-service: caller's own push registrations, filtered by viewer pk"
    ),
    "OperationsQuery.astrolift_my_notification_preferences": (
        "self-service: caller's own notification preferences, filtered by viewer pk"
    ),
    "OperationsQuery.astrolift_my_devices": (
        "self-service: caller's own registered push devices, filtered by tenant.actor_user_id"
    ),
    "OperationsQuery.astrolift_my_alert_subscriptions": (
        "self-service: caller's own per-app alert subscriptions, filtered by tenant.actor_user_id"
    ),
    "RegistryQuery.astrolift_my_apps": (
        "self-service: returns apps the caller can reach via their own RoleBindings"
        " (any scope from app up to org). Permission visibility IS the gate."
    ),
    "RegistryQuery.astrolift_my_apps_page": (
        "self-service: cursor-paginated companion to astrolift_my_apps (#481)."
        " Same rationale — the viewer's RoleBindings are the gate; filter args"
        " (search/status/team/project) compose on top of that scope filter."
    ),
    "RegistryQuery.astrolift_app_permissions": (
        "self-service: returns the caller's own effective permission slugs on "
        "one app, computed via the same RoleBinding scope traversal the "
        "@require_permission decorator uses (#478). No data leaks past what "
        "the caller already has — the resolver hides apps in other tenants "
        "and returns [] when there is no binding."
    ),
    "RegistryQuery.astrolift_platform_api_url": (
        "platform-level value (the install's PLATFORM_API_URL) surfaced to the "
        "Settings / CI-setup page so the operator can paste it into "
        "``ASTROLIFT_API_URL`` on their CI side. Not tenant-scoped because the "
        "API origin is the same regardless of tenant; the resolver still rejects "
        "anonymous callers so the origin doesn't leak to unauthenticated probes."
    ),
    "RegistryQuery.assignable_astrolift_projects": (
        "self-service: returns projects the caller can assign apps to via their "
        "own RoleBindings (#391), same shape as astrolift_my_apps. Permission "
        "visibility from the caller's bindings IS the gate."
    ),
    "OperationsMutation.mark_notification_read": "self-service: marks the caller's own notification",
    "OperationsMutation.mark_all_notifications_read": "self-service: marks all the caller's notifications",
    # Push device registry / notification prefs / alert subscriptions
    # (#476, #499, #747) — self-service writes: each binds the row to the
    # caller's own user (viewer.pk / tenant.actor_user_id), never to a
    # tenant. Registering a phone, toggling a notification preference, or
    # subscribing to an app's alerts is "manage my own settings"; the
    # device/preference/subscription belongs to a user, not an org, so
    # there is no org to scope on. is_authenticated IS the gate (same
    # pattern as generate_install_enrollment_qr / mark_notification_read).
    "OperationsMutation.register_mobile_device": (
        "self-service: registers a push device on the caller's own account (viewer.pk)"
    ),
    "OperationsMutation.revoke_mobile_device": (
        "self-service: soft-deletes one of the caller's own push registrations (viewer.pk)"
    ),
    "OperationsMutation.set_notification_preference": (
        "self-service: upserts one notification preference row for the caller (viewer.pk)"
    ),
    "OperationsMutation.set_alert_subscription": (
        "self-service: upserts the caller's own per-app alert subscription (tenant.actor_user_id)"
    ),
    "OperationsMutation.clear_alert_subscription": (
        "self-service: soft-deletes one of the caller's own alert subscriptions (tenant.actor_user_id)"
    ),
    "ClustersQuery.astrolift_provider_plugins": (
        "platform-level reference data — the list of provider plugins is "
        "install-wide (not per-tenant). Surfaced on /resources/drivers to "
        "any authed operator so they can see the install's capability "
        "surface. The resolver enforces is_authenticated inline."
    ),
    # Token-based public approval (#125, spec 06 §4.6). The single-use
    # magic link in the operator's email is the auth proof — by
    # construction the resolver runs unauthenticated, hash-at-rest in
    # the DB row is the source of truth. Bot/email integrations
    # cannot supply tenant context.
    "LifecycleQuery.scan_cloud_orphans": (
        "install-wide operator capability (#995): orphan detection enumerates "
        "platform-owned cloud resources whose owning DB row no longer exists. "
        "An orphan has no live owner, so by construction there is no tenant to "
        "scope on — scan_orphans() unions every managed cluster + every live "
        "app across ALL orgs and diffs against dangling cloud resources. Same "
        "category as the Temporal admin resolvers that span tenants; gated on "
        "APP_DELETE. Read-only — never deletes."
    ),
    "LifecycleMutation.approve_deployment_by_token": (
        "public: approval magic-link token IS the auth proof; no tenant context at the point of click"
    ),
    "LifecycleMutation.reject_deployment_by_token": (
        "public: rejection magic-link token IS the auth proof; no tenant context at the point of click"
    ),
    "TemporalWorkflowsMutation.cancel_workflow_instance": (
        "admin-only: Temporal workflow ids span tenants; gated on AUDIT_LOG_READ + ADMIN_ELEVATE permissions"
    ),
    "TemporalWorkflowsMutation.terminate_workflow_instance": (
        "admin-only: Temporal workflow ids span tenants; gated on AUDIT_LOG_READ + ADMIN_ELEVATE permissions"
    ),
    "TemporalWorkflowsMutation.signal_workflow_instance": (
        "admin-only: Temporal signals span tenants; gated on AUDIT_LOG_READ + ADMIN_ELEVATE permissions"
    ),
    # Forms (#453): conditional gate on a runtime row.
    "FormsMutation.submit_form": (
        "permission gate depends on FormDefinition.is_public, which has "
        "to be loaded first — public forms accept submissions without "
        "form.submit (anonymous / non-member submitters on a published "
        "public form), private forms gate on form.submit. The gate "
        "lives inline in the resolver body. @tenant_scoped is still "
        "applied for the org filter on the form lookup."
    ),
    "FormsQuery.form_field_types": (
        "public metadata: returns the static palette of widget kinds "
        "the form builder UI offers. Knowing which renderers exist "
        "leaks nothing about any tenant."
    ),
    "AgentsQuery.agent_runtimes": (
        "platform-level reference data: the public runtime catalog is "
        "install-wide (not per-tenant) — the same 12 published agent images "
        "are selectable by every org, same shape as astrolift_provider_plugins "
        "and form_field_types. tenant_scoped() would have nothing to filter. "
        "The resolver rejects anonymous callers inline so the catalog doesn't "
        "leak to unauthenticated probes; no tenant data is exposed."
    ),
    "AgentsQuery.dispatchers": (
        "platform-level routing fabric: DispatcherInstances span tenants "
        "(one per cluster/cloud/region) and are the dispatch router's "
        "targets, not tenant-owned rows — same shape as "
        "astrolift_provider_plugins. tenant_scoped() would filter the "
        "platform fleet to nothing. Staff/superuser-only: the resolver "
        "rejects anonymous + non-staff callers inline, and the type "
        "omits api_key_hash so the scoped key never surfaces."
    ),
}


def _platform_schema_files() -> list[Path]:
    return sorted(
        p for p in BACKEND.glob("astrolift_*/schema/*.py") if p.name in {"queries.py", "mutations.py"}
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
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_strawberry_field(m.decorator_list)
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
            failures.append(f"{path.relative_to(BACKEND)}::{qualname} is missing {' and '.join(missing)}")

    if failures:
        msg = (
            "Tenancy/permission guard missing on these resolvers:\n  - "
            + "\n  - ".join(failures)
            + "\n\nIf the resolver legitimately escapes tenant scope, add it to "
            "EXEMPT in this file with a written justification."
        )
        pytest.fail(msg)
