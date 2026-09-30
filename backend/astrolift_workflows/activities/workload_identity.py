"""Ensure an app's workload-identity (IRSA) role + ServiceAccount binding
exist before its pods start (#1011).

Apps that consume IAM-authed managed services (e.g. S3 object stores) need a
ServiceAccount annotated with an IAM role ARN; the EKS pod-identity webhook
then exchanges the projected SA token for STS credentials so the pod reaches
the service with no static keys. This activity:

* aggregates the IAM grants every active managed service declares in its
  driver ``binding().iam_grants`` into the target cloud's grant shape — an
  inline policy on AWS, IAM roles on GCP, ``(role definition, ARM scope)``
  pairs on Azure,
* creates/updates the scoped IAM role (idempotent) via the cluster's
  WorkloadIdentityDriver, and
* binds it to the app's ServiceAccount (adds the ``(namespace, sa)`` subject
  to the role's OIDC trust policy).

The deploy render (``render_resources_for_deployment``) emits the matching
annotated ServiceAccount + sets ``serviceAccountName`` on the pods, using the
same name (``workload_identity_role_name``) and account/role-path config, so
the SA annotation points at exactly the role this activity creates.

Password-authed services (postgres/redis) declare no ``iam_grants``. Apps with
only those still receive an empty cloud identity because deploy rendering is
deterministic and cannot depend on an activity's transient return value.

Where a cloud expresses a grant as its own object — Azure's role assignments —
this activity also persists what became of each one against the binding that
declared it (:class:`~astrolift_services.models.WorkloadIdentityGrant`), so no
surface can report a binding ready while its authorization is still
propagating or was rejected (#1367).
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.workload_identity")


def _permissions_from_bindings(
    bindings: list[Any],
    *,
    plugin_slug: str = "aws",
) -> list[dict[str, Any]]:
    """Translate portable binding grants into the provider identity shape.

    AWS consumes policy statements. GCP consumes predefined/custom IAM roles
    and Azure consumes ``(role definition, ARM scope)`` pairs; handing either
    of them an AWS policy statement makes provisioning appear to work while
    granting nothing, so both paths translate explicitly and fail closed.
    """
    permissions: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for binding in bindings:
        permissions.extend(
            _permissions_for_binding(binding, plugin_slug=plugin_slug, seen=seen),
        )
    return permissions


def _permissions_by_binding(
    bindings: list[Any],
    *,
    plugin_slug: str = "aws",
) -> list[list[dict[str, Any]]]:
    """The same translation, kept split per binding.

    :func:`_permissions_from_bindings` is what the driver consumes and
    deduplicates globally, which loses who declared what. Attributing a
    pending or failed assignment back to the binding that needs it requires
    the unflattened view, and duplicates must survive it: two services that
    both declare Blob Data Contributor on the same container each depend on
    that one assignment, so it is required for both.
    """
    return [_permissions_for_binding(binding, plugin_slug=plugin_slug, seen=set()) for binding in bindings]


def _permissions_for_binding(
    binding: Any,
    *,
    plugin_slug: str,
    seen: set[Any],
) -> list[dict[str, Any]]:
    """Translate one binding's grants, skipping anything already in ``seen``."""
    permissions: list[dict[str, Any]] = []
    if binding is None:
        return permissions
    for grant in getattr(binding, "iam_grants", None) or []:
        actions = list(getattr(grant, "actions", None) or [])
        resource = getattr(grant, "resource", None)
        if not actions or not resource:
            continue
        if plugin_slug == "azure":
            permissions.extend(
                _azure_role_assignments(
                    actions=actions,
                    resource=str(resource),
                    seen=seen,
                ),
            )
            continue
        if plugin_slug == "gcp":
            for action in actions:
                if not (
                    action.startswith("roles/")
                    or (action.startswith(("projects/", "organizations/")) and "/roles/" in action)
                ):
                    raise ValueError(
                        "GCP managed-service grants must declare IAM roles, "
                        f"not raw permissions; received {action!r} for {resource!r}",
                    )
                if action not in seen:
                    permissions.append({"role": action})
                    seen.add(action)
            continue
        permissions.append(
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": resource,
            },
        )
    return permissions


def _refuse_unscoped_secret_grants(services: list[Any], bindings: list[Any]) -> None:
    """Refuse a Secrets Manager grant a driver built from a secret its
    service's config names outside the owning org's namespace (#1921).

    The grant would let the app's own role read that secret, so it is held to
    the namespace the binding rows are.
    """
    from astrolift_services.secret_ref_config import (
        _role_arn_reason,
        managed_binding_ref_reason,
        service_owner,
    )

    for svc, binding in zip(services, bindings, strict=True):
        for grant in getattr(binding, "iam_grants", None) or []:
            actions = [str(action) for action in grant.actions or ()]
            if "iam:PassRole" in actions:
                # A pass-role grant on a role the tenant named lets the app's
                # role hand that role to an AWS service; only the org's own
                # roles, as at config write (#1960).
                reason = _role_arn_reason(str(grant.resource or ""), service_owner(svc))
                if reason is not None:
                    raise ValueError(
                        f"managed service {svc.name or svc.kind!r} grant on {grant.resource!r}: {reason}"
                    )
            if not any(action.startswith("secretsmanager:") for action in actions):
                continue
            reason = managed_binding_ref_reason(svc, str(grant.resource or ""))
            if reason is not None:
                raise ValueError(
                    f"managed service {svc.name or svc.kind!r} grant on {grant.resource!r}: {reason}"
                )


def _azure_role_assignments(
    *,
    actions: list[str],
    resource: str,
    seen: set[Any],
) -> list[dict[str, Any]]:
    """Resolve one Azure grant into deduplicated ``(role, scope)`` pairs.

    Grants classified as control-plane work contribute nothing: the pod reads
    that material from its projected Secret, so assigning the workload a role
    for it would be over-permission. Anything the catalog cannot classify
    raises out of the activity.
    """
    from azure.role_catalog import (
        ControlPlaneGrant,
        resolve_grant_action,
        validate_arm_scope,
    )

    assignments: list[dict[str, Any]] = []
    resolved = [resolve_grant_action(action) for action in actions]
    roles = [entry for entry in resolved if not isinstance(entry, ControlPlaneGrant)]
    if not roles:
        return assignments

    scope = validate_arm_scope(resource)
    for role in roles:
        key = (role.role_definition_guid, scope)
        if key in seen:
            continue
        seen.add(key)
        assignments.append(
            {
                "role_definition_id": role.role_definition_guid,
                "role_name": role.role_name,
                "scope": scope,
            },
        )
    return assignments


def _ensure_cluster_oidc_issuer(cluster: Any) -> None:
    """Ensure ``cluster.auth_config['cluster_oidc_issuer']`` is populated.

    IRSA's trust policy is built from the cluster's OIDC issuer. Rather than
    require it to be set out-of-band, discover it from EKS and cache it on
    the cluster row (mirrors the on-demand discovery managed-service
    networking does). No-op when already set or when the provider isn't AWS.
    """
    ac = cluster.auth_config or {}
    if ac.get("cluster_oidc_issuer"):
        return
    if getattr(getattr(cluster, "provider_plugin", None), "slug", "") != "aws":
        return
    from aws.identity_irsa import discover_oidc_issuer

    pc = cluster.provider_config or {}
    region = str(pc.get("region", ac.get("region", cluster.region or "")))
    cluster_name = str(pc.get("cluster_name", ac.get("cluster_name", cluster.slug)))
    from core.cluster_credentials import credential_for_cluster

    issuer = discover_oidc_issuer(region, cluster_name, credential=credential_for_cluster(cluster))
    if not issuer:
        return
    new_ac = dict(ac)
    new_ac["cluster_oidc_issuer"] = issuer
    cluster.auth_config = new_ac
    cluster.save(update_fields=["auth_config"])


def _identity_services_by_environment(app, cluster):
    """One app role covers coherent consumers on this live provider target."""
    from django.db.models import Prefetch, Q

    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models import ManagedService, ManagedServiceAttachment

    environments = list(
        AppEnvironment.objects.filter(registered_app=app, tenant_cluster=cluster)
        .select_related("registered_app__organization")
        .order_by("pk")
    )
    environment_ids = [env.pk for env in environments]
    rows = (
        ManagedService.objects.filter(
            Q(app_environment_id__in=environment_ids)
            | Q(attachments__app_environment_id__in=environment_ids, attachments__deleted_at__isnull=True),
        )
        .filter(
            Q(
                registered_app=app,
                project__isnull=True,
                app_environment__registered_app=app,
                app_environment__tenant_cluster=cluster,
                app_environment__deleted_at__isnull=True,
            )
            | Q(
                registered_app__isnull=True,
                app_environment__isnull=True,
                project_id=app.project_id,
                project__deleted_at__isnull=True,
                tenant_cluster=cluster,
            ),
            Q(tenant_cluster__isnull=True) | Q(tenant_cluster=cluster),
        )
        .select_related("app_environment__tenant_cluster__provider_plugin", "tenant_cluster__provider_plugin")
        .prefetch_related(
            Prefetch(
                "attachments",
                queryset=ManagedServiceAttachment.objects.filter(app_environment_id__in=environment_ids),
                to_attr="identity_attachments",
            )
        )
        .distinct()
        .order_by("pk")
    )
    services_by_env: dict[int, list[ManagedService]] = {env.pk: [] for env in environments}
    for service in rows:
        consumers = {attachment.app_environment_id for attachment in service.identity_attachments}
        if service.app_environment_id in services_by_env:
            consumers.add(service.app_environment_id)
        for env_id in consumers:
            services_by_env[env_id].append(service)
    return [(env, services_by_env[env.pk]) for env in environments]


def _ensure_workload_identity_sync(registered_app_id: int, app_environment_id: int) -> dict[str, Any]:
    from django.db import transaction

    # The shared policy and trust must reconcile together across concurrent deploys.
    failure = None
    with transaction.atomic():
        try:
            return _ensure_workload_identity_locked(registered_app_id, app_environment_id)
        except Exception as exc:
            # Assignment failures must remain observable after the activity raises.
            failure = exc
    raise failure


def _ensure_workload_identity_locked(
    registered_app_id: int,
    app_environment_id: int,
) -> dict[str, Any]:
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_lifecycle.visibility import cluster_owned_and_live, live_app_rows
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.capability_deprovision import (
        _resolve_capability_driver,
    )
    from astrolift_workflows.activities.managed_service_lifecycle import (
        _managed_binding_for,
    )
    from core.app_deploy import namespace_for_environment, workload_identity_role_name

    app = (
        RegisteredApp.objects.select_for_update(of=("self",))
        .select_related("organization")
        .get(
            pk=registered_app_id,
        )
    )
    if not live_app_rows(RegisteredApp.objects.filter(pk=app.pk), org_id=app.organization_id).exists():
        raise ValueError("workload identity app has no coherent live owner")

    environment = AppEnvironment.all_objects.select_related(
        "registered_app",
        "tenant_cluster__provider_plugin",
    ).get(
        pk=app_environment_id,
        registered_app_id=registered_app_id,
        deleted_at__isnull=True,
    )
    cluster = environment.tenant_cluster
    if cluster is None:
        return {
            "skipped": True,
            "reason": "no tenant cluster bound to environment",
            "registered_app_id": registered_app_id,
        }
    if not cluster_owned_and_live(cluster, app.organization_id):
        raise ValueError("workload identity cluster is not live in the app organization")
    groups = _identity_services_by_environment(app, cluster)
    if not next((services for env, services in groups if env.pk == environment.pk), []):
        return {
            "skipped": True,
            "reason": "no managed services",
            "registered_app_id": registered_app_id,
        }
    services = list({service.pk: service for _, group in groups for service in group}.values())

    plugin_slug = getattr(getattr(cluster, "provider_plugin", None), "slug", "")
    bindings = [_managed_binding_for(svc) for svc in services]
    _refuse_unscoped_secret_grants(services, bindings)
    permissions = _permissions_from_bindings(bindings, plugin_slug=plugin_slug)
    # A service with no ``backend_ref`` has no binding, so it contributes no
    # grants -- and the role is then created authorised for nothing, which
    # surfaces much later as an AccessDenied from the app's own SDK rather
    # than as a provisioning problem (#1701). Say it here, where the cause
    # is still in hand.
    unprovisioned = [
        (svc.name or svc.kind) for svc, binding in zip(services, bindings, strict=True) if binding is None
    ]
    if unprovisioned:
        log.warning(
            "workload identity for app %s: %d of %d managed services have no backend yet "
            "(%s); the role will carry no grants for them",
            app.slug,
            len(unprovisioned),
            len(services),
            ", ".join(sorted(unprovisioned)),
        )
    declared_by_id = dict(
        zip(
            [service.pk for service in services],
            _permissions_by_binding(bindings, plugin_slug=plugin_slug),
            strict=True,
        )
    )

    def persist_states():
        counts: dict[str, int] = {}
        for env, group in groups:
            states = _persist_grant_state(
                environment=env,
                services=group,
                declared_by_service=[declared_by_id[service.pk] for service in group],
                driver=identity_driver,
                plugin_slug=plugin_slug,
                identity_role_name=role_name,
            )
            for state, count in states.items():
                counts[state] = counts.get(state, 0) + count
        return counts

    # Self-sufficient: the IRSA trust policy needs the cluster's OIDC issuer.
    # Discover it from EKS + cache on the cluster row when absent, so the
    # trust isn't malformed by an empty issuer (which yields a broken
    # oidc-provider/ principal + bare :sub condition).
    _ensure_cluster_oidc_issuer(cluster)

    identity_driver = _resolve_capability_driver(cluster, "identity")
    role_name = workload_identity_role_name(app)
    namespace = namespace_for_environment(environment)

    # Idempotent: create_identity_role returns the existing ARN if present;
    # bind_service_account adds this (namespace, sa) subject to the trust.
    #
    # The reconcile records what became of each grant before it raises, so the
    # per-assignment state is persisted on the failing path too — that is the
    # path an operator most needs it on.
    try:
        role_arn = identity_driver.create_identity_role(role_name, permissions)
    except Exception:
        persist_states()
        raise
    states = persist_states()
    refusals = list(getattr(identity_driver, "prune_refusals", list)())
    for refusal in refusals:
        log.warning(
            "workload identity %s: refused to prune a role assignment: %s",
            role_name,
            refusal,
        )
    # One role per app, and an environment in a namespace of its own (#1922)
    # runs its ServiceAccount there. The IRSA driver resets the trust to its
    # subject-less base on every create and binding adds one subject, so
    # binding this environment's namespace alone would drop the others' and
    # their pods would lose the role. Every namespace of the app on this
    # cluster whose render carries the ServiceAccount is bound, this one
    # last; with every environment in the app namespace that is the one
    # call it always was.
    other_namespaces = {
        namespace_for_environment(env)
        for env, group in groups
        if group and env.pk != environment.pk and namespace_for_environment(env) != namespace
    }
    for other in sorted(other_namespaces):
        identity_driver.bind_service_account(cluster.slug, other, role_name, role_name)
    annotation = identity_driver.bind_service_account(
        cluster.slug,
        namespace,
        role_name,
        role_name,
    )
    return {
        "registered_app_id": registered_app_id,
        "role": role_name,
        "role_arn": role_arn,
        "namespace": namespace,
        "cluster_slug": cluster.slug,
        "grants": len(permissions),
        # Named so the activity result carries the reason a role came out
        # empty, not only the count (#1701).
        "unprovisioned_services": sorted(unprovisioned),
        "grants_pending": states.get("pending", 0),
        "grants_failed": states.get("failed", 0),
        "prune_refusals": len(refusals),
        "annotation": annotation,
    }


def _persist_grant_state(
    *,
    environment: Any,
    services: list[Any],
    declared_by_service: list[list[dict[str, Any]]],
    driver: Any,
    plugin_slug: str,
    identity_role_name: str,
) -> dict[str, int]:
    """Write each binding's assignment state, and drop what it no longer declares.

    Only clouds that write a discrete assignment object per grant get rows.
    AWS and GCP fold the grants into the identity's own policy document in the
    same call that creates it, so there is no second object whose state could
    differ from the identity's.

    An assignment the driver reported nothing about is recorded ``pending``
    rather than assumed applied: silence is exactly what this issue exists to
    stop reading as success.
    """
    from django.utils import timezone

    from astrolift_services.models import WorkloadIdentityGrant

    counts: dict[str, int] = {}
    if plugin_slug != "azure":
        return counts

    outcomes = {
        (str(o.role_definition_id).lower(), str(o.scope).rstrip("/")): o
        for o in getattr(driver, "grant_assignments", list)()
    }
    now = timezone.now()
    for service, permissions in zip(services, declared_by_service, strict=True):
        declared: set[tuple[str, str]] = set()
        for permission in permissions:
            guid = str(permission.get("role_definition_id", "") or "").lower()
            scope = str(permission.get("scope", "") or "").rstrip("/")
            if not guid or not scope:
                continue
            declared.add((guid, scope))
            outcome = outcomes.get((guid, scope))
            state = getattr(outcome, "state", WorkloadIdentityGrant.State.PENDING.value)
            reason = getattr(
                outcome,
                "reason",
                "the identity reconcile reported nothing about this assignment",
            )
            counts[state] = counts.get(state, 0) + 1
            applied = state == WorkloadIdentityGrant.State.APPLIED.value
            WorkloadIdentityGrant.objects.update_or_create(
                managed_service=service,
                app_environment=environment,
                role_definition_id=guid,
                scope=scope,
                defaults={
                    "provider_plugin_slug": plugin_slug,
                    "identity_role_name": identity_role_name,
                    "role_name": str(permission.get("role_name", "") or ""),
                    "assignment_name": str(getattr(outcome, "assignment_name", "") or ""),
                    "state": state,
                    "reason": "" if applied else reason,
                    "last_attempted_at": now,
                    "applied_at": now if applied else None,
                },
            )
        stale = WorkloadIdentityGrant.objects.filter(
            managed_service=service,
            app_environment=environment,
        )
        for row in stale:
            if (row.role_definition_id.lower(), row.scope.rstrip("/")) not in declared:
                row.soft_delete()
    return counts


@activity.defn(name="astrolift.workload_identity.ensure")
async def ensure_workload_identity(
    registered_app_id: int,
    app_environment_id: int,
) -> dict[str, Any]:
    """Activity entry — ensure the app's IRSA role + SA binding exist."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_ensure_workload_identity_sync)(
        registered_app_id,
        app_environment_id,
    )
    log.info(
        "ensure_workload_identity app_id=%s summary=%s",
        registered_app_id,
        summary,
    )
    return summary
