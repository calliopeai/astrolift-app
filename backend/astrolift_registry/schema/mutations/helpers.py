"""Module constants and helper functions for the mutation package."""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import failure as gql_failure
from astrolift_identity.models import Team
from astrolift_registry.models import AppTeamAccess, RegisteredApp
from core.friendly_name import generate_friendly_slug
from core.mutations import ErrorCode
from core.tenancy import get_current_tenant


def _actor():
    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    if actor_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


def _ensure_owner_access(app, team_id: int, *, actor=None) -> None:
    """Idempotently materialize an active ``AppTeamAccess(OWNER)``
    row for ``(app, team_id)``.

    - If no row exists, create one at OWNER.
    - If a soft-deleted row exists, restore + promote to OWNER.
    - If a live row exists at a lower level, promote it to OWNER.
    - If a live row at OWNER exists, no-op.
    """

    live = AppTeamAccess.objects.filter(registered_app=app, team_id=team_id, deleted_at__isnull=True).first()
    if live is not None:
        if live.access_level != AppTeamAccess.AccessLevel.OWNER.value:
            live.access_level = AppTeamAccess.AccessLevel.OWNER.value
            live.save(update_fields=["access_level", "updated_at", "version"])
        return

    soft_deleted = (
        AppTeamAccess.all_objects.filter(registered_app=app, team_id=team_id)
        .exclude(deleted_at__isnull=True)
        .order_by("-deleted_at")
        .first()
    )
    if soft_deleted is not None:
        soft_deleted.deleted_at = None
        soft_deleted.deleted_by = None
        soft_deleted.access_level = AppTeamAccess.AccessLevel.OWNER.value
        soft_deleted.save(
            update_fields=[
                "deleted_at",
                "deleted_by",
                "access_level",
                "updated_at",
                "version",
            ]
        )
        return

    AppTeamAccess.objects.create(
        registered_app=app,
        team_id=team_id,
        access_level=AppTeamAccess.AccessLevel.OWNER.value,
    )


def _downgrade_to_deployer(app, team_id: int, *, actor=None) -> None:
    """Move the previous home team's grant to ``DEPLOYER`` rather
    than revoke it on move. The previous team keeps write+deploy
    access until the operator explicitly revokes — preserving
    in-flight humans' access and the audit trail.

    Creates a new DEPLOYER row when no active grant existed (the FK
    was the only thing pointing at that team).
    """

    if team_id is None:
        return
    live = AppTeamAccess.objects.filter(registered_app=app, team_id=team_id, deleted_at__isnull=True).first()
    if live is None:
        AppTeamAccess.objects.create(
            registered_app=app,
            team_id=team_id,
            access_level=AppTeamAccess.AccessLevel.DEPLOYER.value,
        )
        return
    if live.access_level == AppTeamAccess.AccessLevel.OWNER.value:
        live.access_level = AppTeamAccess.AccessLevel.DEPLOYER.value
        live.save(update_fields=["access_level", "updated_at", "version"])


def _resolve_approval_inputs(
    *,
    organization,
    requires_approval,
    approver_team_id,
    approver_user_ids,
    minimum_approvals,
):
    """Resolve the approval-policy input set against the org scope.

    Returns a ``(values, error)`` tuple. On success, ``values`` is a
    dict of resolved values: ``requires_approval`` (bool), ``team``
    (Team | None), ``user_ids`` (tuple[int, ...] | None — None means
    'leave untouched'), ``minimum_approvals`` (int). On failure,
    ``error`` is the MutationResult failure envelope.

    Each cross-org reference (team / user) is refused with a clear
    field-tagged validation error rather than silently dropped — the
    wizard surfaces these inline.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()

    team = None
    if approver_team_id is not None:
        team = (
            Team.objects.filter(guid=str(approver_team_id), deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if team is None:
            return None, gql_failure(
                ErrorCode.NOT_FOUND.value,
                "approver team not found",
                field="approverTeamId",
            )
        if team.organization_id != organization.id:
            return None, gql_failure(
                ErrorCode.PRECONDITION.value,
                "approver team must belong to the same organization as the app",
                field="approverTeamId",
            )

    user_ids: tuple[int, ...] | None = None
    if approver_user_ids is not None:
        # Empty list is meaningful ("clear approver users"); leave as
        # the empty tuple. Otherwise parse each value as an integer
        # User pk (matching ``AstroliftUser.id``) and refuse unknowns.
        raw = list(approver_user_ids)
        parsed: list[int] = []
        for raw_id in raw:
            try:
                parsed.append(int(str(raw_id).strip()))
            except (TypeError, ValueError):
                return None, gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"approverUserIds entry {raw_id!r} is not a valid user id",
                    field="approverUserIds",
                )
        if parsed:
            found = set(User.objects.filter(pk__in=parsed).values_list("pk", flat=True))
            missing = [pk for pk in parsed if pk not in found]
            if missing:
                return None, gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"approver users not found: {sorted(missing)}",
                    field="approverUserIds",
                )
            user_ids = tuple(parsed)
        else:
            user_ids = ()

    if minimum_approvals is not None and minimum_approvals < 1:
        return None, gql_failure(
            ErrorCode.VALIDATION.value,
            "minimumApprovals must be at least 1",
            field="minimumApprovals",
        )

    resolved = {
        "requires_approval": bool(requires_approval) if requires_approval is not None else None,
        "team": team,
        "team_provided": approver_team_id is not None,
        "user_ids": user_ids,
        "minimum_approvals": minimum_approvals,
    }
    return resolved, None


def _validate_effective_approval_policy(
    *,
    requires_approval: bool,
    effective_team,
    effective_user_ids: tuple[int, ...] | list[int] | set[int],
    effective_minimum_approvals: int,
):
    """Cross-field validation of the resolved approval policy (#410).

    Distinct from ``_resolve_approval_inputs`` which validates each
    incoming field on its own. This gate runs over the *post-update*
    effective state so the rules are equivalent on register_app and
    update_app whether the caller passed every field or only a delta.

    Rules:
      - When ``requires_approval`` is True, the policy must name at
        least one approver path — users OR team. Empty approver_users
        AND no team would fail-open on the caller side (any deploy
        would need any user with ``app.approve_deploy``), which is the
        documented behavior of the resolver but a bad default to land
        from the wizard — the operator clearly wanted a specific
        approver set.
      - Users and team are mutually exclusive on the picker: a single
        policy can't gate on both "this team's members" and "these
        specific users". Two approver paths in one app makes the
        approval-counting math ambiguous; pick one.
      - When approver_users is non-empty, ``minimumApprovals`` may not
        exceed the user count — otherwise the gate would never satisfy.
    """
    if not requires_approval:
        return None

    users_set = bool(effective_user_ids)
    team_set = effective_team is not None
    if not users_set and not team_set:
        return gql_failure(
            ErrorCode.VALIDATION.value,
            "requireApproval is on but no approvers selected — pick a team or one or more users",
            field="approverUserIds",
        )
    if users_set and team_set:
        return gql_failure(
            ErrorCode.VALIDATION.value,
            "approverTeamId and approverUserIds are mutually exclusive — pick a team OR specific users",
            field="approverTeamId",
        )
    if users_set and effective_minimum_approvals > len(list(effective_user_ids)):
        return gql_failure(
            ErrorCode.VALIDATION.value,
            "minimumApprovals cannot exceed the number of approver users",
            field="minimumApprovals",
        )
    return None


def _validate_build_mode(raw):
    """Validate an incoming ``build_mode`` against the model choices.

    Returns ``(value, error)``. ``value`` is the normalised string when
    valid; ``error`` is the MutationResult failure envelope otherwise.
    A None input (the update-mutation "leave untouched" sentinel) passes
    straight through as ``(None, None)`` so callers can skip the field.
    """
    if raw is None:
        return None, None
    value = str(raw).strip()
    valid = {choice.value for choice in RegisteredApp.BuildMode}
    if value not in valid:
        return None, gql_failure(
            ErrorCode.VALIDATION.value,
            f"buildMode must be one of {sorted(valid)}",
            field="buildMode",
        )
    return value, None


def _validate_build_strategy(raw):
    """Validate an incoming ``build_strategy`` against the model choices.

    Returns ``(value, error)`` mirroring ``_validate_build_mode``: the
    normalised string when valid, the failure envelope otherwise. A None
    input (the update "leave untouched" sentinel) passes through as
    ``(None, None)``. Deny-by-default: anything outside the four choices
    is refused rather than silently coerced.
    """
    if raw is None:
        return None, None
    value = str(raw).strip()
    valid = {choice.value for choice in RegisteredApp.BuildStrategy}
    if value not in valid:
        return None, gql_failure(
            ErrorCode.VALIDATION.value,
            f"buildStrategy must be one of {sorted(valid)}",
            field="buildStrategy",
        )
    return value, None


def _normalize_build_args(raw):
    """Coerce the incoming ``build_args`` JSON into a flat str→str map.

    Returns ``(args, error)``. ``args`` is a dict on success (the empty
    dict when ``raw`` is None — the row default). ``build_args`` are
    rendered as ``--build-arg KEY=VALUE`` pairs by the platform-build
    path, so a non-object payload or a non-scalar value is a caller
    error rather than something to silently coerce: refuse with a
    field-tagged validation envelope. Scalar values (str / int / bool)
    are stringified for convenience.
    """
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return None, gql_failure(
            ErrorCode.VALIDATION.value,
            "buildArgs must be a JSON object of string keys to string values",
            field="buildArgs",
        )
    out: dict[str, str] = {}
    for key, val in raw.items():
        if not isinstance(key, str):
            return None, gql_failure(
                ErrorCode.VALIDATION.value,
                "buildArgs keys must be strings",
                field="buildArgs",
            )
        if isinstance(val, (str, int, float, bool)):
            out[key] = str(val)
        else:
            return None, gql_failure(
                ErrorCode.VALIDATION.value,
                f"buildArgs[{key!r}] must be a string, number, or boolean",
                field="buildArgs",
            )
    return out, None


def _generate_unique_app_slug(organization) -> str:
    """A friendly app slug that is free within ``organization``.

    Tries fresh three-word slugs (huge namespace, so a first-try hit is the
    common case); after a run of collisions it numeric-suffixes a base within
    the 40-char slug limit rather than looping forever. Mirrors the per-org
    active-slug uniqueness the create path already enforces.
    """
    for _ in range(20):
        candidate = generate_friendly_slug()
        if not RegisteredApp.objects.filter(organization=organization, slug=candidate).exists():
            return candidate

    base = generate_friendly_slug()
    n = 2
    candidate = base
    while RegisteredApp.objects.filter(organization=organization, slug=candidate).exists():
        suffix = f"-{n}"
        candidate = base[: 40 - len(suffix)] + suffix
        n += 1
    return candidate


def _viewer_can_access_project(*, project, viewer) -> bool:
    """True when ``viewer`` has any active RoleBinding that reaches
    ``project`` — directly or via an ancestor scope.

    Mirrors the read-side resolution done in ``astrolift_my_apps``
    (queries.py): a binding at ORG level covers every project in the
    org; TEAM covers every project under the team; PROJECT covers the
    project itself. APP-scope bindings don't cover the project — a
    user with app-only access to one app under a project shouldn't be
    able to retarget OTHER apps onto that project.

    Superusers short-circuit to True so a platform operator can fix
    nav scoping without needing an explicit grant.
    """
    if viewer is None:
        return False
    if getattr(viewer, "is_superuser", False) and getattr(viewer, "is_active", True):
        return True

    from django.db.models import Q

    from astrolift_identity.models import RoleBinding

    now = timezone.now()
    bindings = RoleBinding.objects.filter(
        user_id=viewer.pk,
        deleted_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    for binding in bindings:
        if binding.scope_kind == RoleBinding.ScopeKind.ORG and binding.scope_id == project.organization_id:
            return True
        if binding.scope_kind == RoleBinding.ScopeKind.TEAM and binding.scope_id == project.team_id:
            return True
        if binding.scope_kind == RoleBinding.ScopeKind.PROJECT and binding.scope_id == project.id:
            return True
    return False


def _bootstrap_app_environments(app: RegisteredApp, env_names: list[str]) -> None:
    """Create AppEnvironment rows and kick off OnboardAppWorkflow when the
    app is still in its initial ``pending`` provisioning state.

    Called after a successful "Resync from source" so that apps whose
    source was connected post-registration (bypassing the wizard) get the
    same environment setup the wizard would have applied.

    Idempotent: existing active environments are left untouched.
    If no managed cluster is available the function is a no-op and lets the
    operator retry once a cluster is adopted.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_manifest.normalize import normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_manifest.persist import reconcile_managed_services
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, OnboardAppInput

    manifest_services = ()
    if app.manifest_raw:
        manifest_services = normalize(parse_raw(app.manifest_raw)).managed_services

    # Must have at least one managed cluster to bind environments to.
    # TenantCluster.organization is nullable: null means shared (available to
    # all orgs). Accept clusters scoped to this org OR shared clusters.
    cluster = app.default_tenant_cluster
    if cluster is None:
        cluster = (
            TenantCluster.objects.filter(
                Q(organization=app.organization) | Q(organization__isnull=True),
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            )
            .order_by("pk")
            .first()
        )
    if cluster is None:
        return

    # Ensure default_tenant_cluster is set on the app.
    if app.default_tenant_cluster_id is None:
        app.default_tenant_cluster = cluster
        app.save(update_fields=["default_tenant_cluster", "updated_at", "version"])

    # Create AppEnvironment records for each env in the manifest that
    # doesn't already exist.  When the manifest carries no [environments.*]
    # sections env_names is empty; fall back to a single "production"
    # environment so the provisioning activities (provision_registry_repo,
    # provision_namespace) have a cluster binding to work with.
    org_slug = (
        getattr(app.organization, "slug", None) or getattr(app.organization, "name", "") or "org"
    ).lower()
    # Resolve the managed domain: org default → platform fallback → None.
    # Used both for the URL string and to bind the FK on AppEnvironment
    # so the deploy pipeline emits an Ingress for the platform hostname.
    from astrolift_clusters.models import resolve_managed_domain

    _managed_domain = resolve_managed_domain(app.organization, for_preview=False)
    base_zone: str = getattr(_managed_domain, "zone", None) or org_slug
    created_any = False
    effective_env_names = list(env_names) if env_names else []
    for service in manifest_services:
        if service.environment not in effective_env_names:
            effective_env_names.append(service.environment)
    if not effective_env_names:
        has_any = AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).exists()
        if not has_any:
            effective_env_names = ["production"]
    for env_name in effective_env_names:
        existing = AppEnvironment.objects.filter(
            registered_app=app,
            name=env_name,
            deleted_at__isnull=True,
        ).exists()
        if not existing:
            AppEnvironment.objects.create(
                registered_app=app,
                tenant_cluster=cluster,
                name=env_name,
                url=f"https://{app.subdomain or app.slug}.{base_zone}",
                managed_domain=_managed_domain,
                required_approvals=0,
            )
            created_any = True

    # ``persist_manifest`` runs before environment bootstrap during initial
    # registration, so it deliberately defers managed services when there is
    # no cluster-bound environment. Complete that reconciliation now, before
    # onboarding can deploy workloads that expect the bindings.
    if manifest_services:
        reconcile_managed_services(app, manifest_services)

    # Trigger OnboardAppWorkflow when the app is still pending (never
    # provisioned). The workflow is idempotent via its workflow_id guard,
    # so a concurrent click is safe.
    provisioning_pending = getattr(app, "provisioning_status", None) in (
        None,
        "pending",
        RegisteredApp.ProvisioningStatus.PENDING.value
        if hasattr(RegisteredApp, "ProvisioningStatus")
        else "pending",
    )
    if created_any or provisioning_pending:
        try:
            # OnboardAppWorkflow only reads registered_app_id from its
            # input; actor / provider_plugin_id / tenant_cluster_id are
            # legacy fields carried in the dataclass for schema stability
            # but are not forwarded to any activity.  Supply system
            # defaults so the frozen dataclass can be constructed here
            # without access to a live request context.
            start_workflow(
                "OnboardAppWorkflow",
                args=[
                    OnboardAppInput(
                        registered_app_id=app.pk,
                        actor=Actor(kind="system", display="resync-bootstrap"),
                        provider_plugin_id=0,
                        tenant_cluster_id=cluster.pk,
                    )
                ],
                workflow_id=f"OnboardAppWorkflow-{app.guid}",
            )
        except Exception:  # noqa: BLE001 — log and move on; don't fail the resync
            import logging

            logging.getLogger(__name__).exception(
                "bootstrap: failed to start OnboardAppWorkflow for app %s",
                app.slug,
            )
