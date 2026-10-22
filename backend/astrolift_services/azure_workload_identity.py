"""The platform's own Azure workload identities, wired into production (#2279).

The provider ports in ``azure.identity_owned`` observe, create, reconcile and
remove a user-assigned managed identity through fixed native ARM clients. They
leave three things to their caller, and this module is that caller:

* the owner and placement, from the platform's own rows and the cluster's
  configuration, never from anything Azure returned;
* the record of the native IDs observed when the identity was created, which
  the render reads and every later operation holds the identity to;
* a checkpoint that re-reads the cluster, the app and the record before and
  after every native request, so an identity is never changed on facts that
  stopped being true while a request was in flight.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from azure.identity_owned import (
        AzureIdentityContext,
        AzureIdentityPlacement,
        AzureIdentityReconcileResult,
        AzureIdentityRemovalResult,
    )

    from astrolift_clusters.models import TenantCluster
    from astrolift_registry.models import RegisteredApp
    from astrolift_services.models import AzureWorkloadIdentity

# Azure accepts at most 20 federated identity credentials on one identity.
MAX_FEDERATIONS = 20
# The owned port reconciles at most this many assignments in one union.
MAX_GRANTS = 64

_WORKLOAD_IDENTITY_USE_LABEL = "azure.workload.identity/use"


class AzureWorkloadIdentityError(ValueError):
    """The platform will not act on this identity as things stand.

    The message names what an operator has to change. It never carries
    anything Azure returned.
    """


def placement_for(app: RegisteredApp, cluster: TenantCluster) -> AzureIdentityPlacement:
    """Where ``app``'s identity on ``cluster`` lives, from the platform's own rows.

    Read through the identity capability's config builder, so the placement
    and the config the provider factory checks it against come from one place.
    """
    from azure.identity_owned import AzureIdentityPlacement, AzureOwnedIdentityError

    from core.app_deploy import _config_for_capability, workload_identity_role_name

    config = _config_for_capability("azure", cluster, "identity")
    for key in ("tenant_id", "subscription_id"):
        value = getattr(config, key)
        try:
            canonical = str(uuid.UUID(value))
        except ValueError:
            canonical = ""
        if value != canonical:
            raise AzureWorkloadIdentityError(
                f"cluster {cluster.slug}: Azure workload identity needs {key} as a lowercase GUID, not {value!r}",
            )
    pc = cluster.provider_config or {}
    try:
        return AzureIdentityPlacement(
            organization_id=str(app.organization.guid),
            app_id=str(app.guid),
            cluster_id=str(cluster.guid),
            tenant_id=config.tenant_id,
            subscription_id=config.subscription_id,
            resource_group=config.resource_group,
            identity_name=workload_identity_role_name(app),
            location=config.location,
            cluster_name=str(pc.get("cluster_name") or cluster.slug),
            issuer=config.cluster_oidc_issuer,
        )
    except AzureOwnedIdentityError as exc:
        raise AzureWorkloadIdentityError(f"cluster {cluster.slug}: {exc}") from None


def recorded_identity(
    app: RegisteredApp, cluster: TenantCluster, *, lock: bool = False
) -> AzureWorkloadIdentity | None:
    """The live record of ``app``'s identity on ``cluster``, in the app's organization."""
    from astrolift_services.models import AzureWorkloadIdentity

    rows = AzureWorkloadIdentity.objects.filter(
        organization_id=app.organization_id,
        registered_app=app,
        tenant_cluster=cluster,
    )
    if lock:
        rows = rows.select_for_update()
    return rows.first()


def render_identity(app: RegisteredApp, cluster: TenantCluster) -> tuple[dict[str, str], dict[str, str]]:
    """ServiceAccount annotations and pod-template labels for ``app`` on ``cluster``.

    Both empty until the workload identity step has recorded the identity:
    the client ID exists only once Azure minted it, and a guessed one would
    point pods at an identity that is not theirs. AKS's webhook injects the
    token volume and ``AZURE_*`` environment only into pods carrying the
    label, and reads the client ID from the ServiceAccount.
    """
    record = recorded_identity(app, cluster)
    if record is None:
        return {}, {}
    return (
        {
            "azure.workload.identity/client-id": record.client_id,
            "azure.workload.identity/tenant-id": record.tenant_id,
        },
        {_WORKLOAD_IDENTITY_USE_LABEL: "true"},
    )


def _identity_driver(cluster: TenantCluster, **owned: Any) -> Any:
    """The cluster's identity capability driver, with the owned port's inputs.

    The same admission and config ``driver_for_capability`` applies, so the
    credential the ports authenticate with is the one the cluster declares.
    """
    from astrolift_drivers.registry import plugins
    from core.app_deploy import _config_for_capability
    from core.cluster_credentials import assert_credential_supported

    assert_credential_supported(cluster, capability="identity")
    config = dataclasses.replace(_config_for_capability("azure", cluster, "identity"), **owned)
    return plugins.get("azure", "identity")(config=config)


def _checkpoint(
    app: RegisteredApp,
    cluster: TenantCluster,
    placement: AzureIdentityPlacement,
    *,
    record_guid: Any = None,
    app_must_be_live: bool = True,
) -> Callable[[], None]:
    """Re-read what the identity operation stands on, before and after each request."""
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.visibility import cluster_owned_and_live, live_app_rows
    from astrolift_registry.models import RegisteredApp
    from astrolift_services.models import AzureWorkloadIdentity

    organization_id = app.organization_id

    def check() -> None:
        current = TenantCluster.all_objects.select_related("provider_plugin").filter(pk=cluster.pk).first()
        if not cluster_owned_and_live(current, organization_id) or current.provider_plugin.slug != "azure":
            raise AzureWorkloadIdentityError(
                f"cluster {cluster.slug} is no longer a live Azure cluster of the app"
            )
        if (
            app_must_be_live
            and not live_app_rows(RegisteredApp.objects.filter(pk=app.pk), org_id=organization_id).exists()
        ):
            raise AzureWorkloadIdentityError(f"app {app.slug} is no longer live")
        if placement_for(app, current) != placement:
            raise AzureWorkloadIdentityError(f"cluster {cluster.slug}'s Azure identity configuration changed")
        if (
            record_guid is not None
            and not AzureWorkloadIdentity.objects.filter(
                guid=record_guid, organization_id=organization_id
            ).exists()
        ):
            raise AzureWorkloadIdentityError(f"app {app.slug}'s workload identity record was removed")

    return check


def _recorded_native(record: AzureWorkloadIdentity) -> Any:
    from azure.identity_federated import AzureManagedIdentity

    return AzureManagedIdentity(
        record.identity_name, record.resource_id, record.client_id, record.principal_id, record.tenant_id
    )


def ensure_identity(
    app: RegisteredApp,
    environment: Any,
    cluster: TenantCluster,
    *,
    namespaces: list[str],
    permissions: list[dict[str, Any]],
) -> tuple[AzureWorkloadIdentity, AzureIdentityContext, AzureIdentityReconcileResult]:
    """Observe or create the app's identity on ``cluster``, record it, and
    reconcile its complete federation and grant union.

    ``namespaces`` is every namespace of the app on this cluster whose pods
    run as the app's ServiceAccount; ``permissions`` the union of what its
    bound services declare. A federation or grant this owner added that is in
    neither is removed; anything another party added is left alone.
    """
    from azure.identity_owned import AzureFederation, AzureIdentityGrant, AzureOwnedIdentityError
    from azure.role_catalog import subscription_of

    from astrolift_services.models import AzureWorkloadIdentity
    from core.app_deploy import workload_identity_role_name

    placement = placement_for(app, cluster)
    if len(namespaces) > MAX_FEDERATIONS:
        raise AzureWorkloadIdentityError(
            f"app {app.slug} runs in {len(namespaces)} namespaces on cluster {cluster.slug}; an Azure managed "
            f"identity accepts at most {MAX_FEDERATIONS} federated credentials",
        )
    if len(permissions) > MAX_GRANTS:
        raise AzureWorkloadIdentityError(
            f"app {app.slug} needs {len(permissions)} role assignments on cluster {cluster.slug}; the workload "
            f"identity reconciles at most {MAX_GRANTS}",
        )
    try:
        sa_name = workload_identity_role_name(app)
        federations = tuple(AzureFederation(namespace, sa_name) for namespace in sorted(set(namespaces)))
        grants = tuple(
            AzureIdentityGrant(str(permission["role_definition_id"]), str(permission["scope"]))
            for permission in permissions
        )
    except AzureOwnedIdentityError as exc:
        raise AzureWorkloadIdentityError(f"app {app.slug}: {exc}") from None
    outside = sorted(
        {grant.scope for grant in grants if subscription_of(grant.scope).lower() != placement.subscription_id}
    )
    if outside:
        # The port refuses these too, but only after the identity exists, and as
        # an unconfirmed reconcile: a binding would read pending for good.
        raise AzureWorkloadIdentityError(
            f"app {app.slug}: grant scope {', '.join(outside)} is outside subscription "
            f"{placement.subscription_id} of cluster {cluster.slug}'s workload identity; the declaring service "
            "must name a resource in that subscription",
        )

    record = recorded_identity(app, cluster, lock=True)
    if record is not None:
        if record.issuer != placement.issuer:
            raise AzureWorkloadIdentityError(
                f"cluster {cluster.slug} now names OIDC issuer {placement.issuer}, but app {app.slug}'s workload "
                f"identity was federated with {record.issuer}. The platform does not move a federation to a new "
                "issuer: remove the app's workload identity, then deploy to federate a new one.",
            )
        if (record.tenant_id, record.subscription_id, record.resource_group, record.resource_id) != (
            placement.tenant_id,
            placement.subscription_id,
            placement.resource_group,
            placement.resource_id,
        ):
            raise AzureWorkloadIdentityError(
                f"cluster {cluster.slug}'s Azure placement no longer matches app {app.slug}'s recorded identity "
                f"{record.resource_id}: remove the app's workload identity, then deploy to create a new one.",
            )

    checkpoint = _checkpoint(app, cluster, placement, record_guid=record.guid if record else None)
    provisioner = _identity_driver(
        cluster, owned_placement=placement, native_checkpoint=checkpoint
    ).owned_provisioner()
    try:
        identity = provisioner.ensure(recorded=_recorded_native(record) if record else None)
    finally:
        provisioner.close()
    if record is None:
        # Recorded before any federation or grant is written, and committed even
        # if the reconcile below fails: the next attempt must hold the identity
        # to these IDs rather than meet it as an unrecorded stranger.
        record = AzureWorkloadIdentity.objects.create(
            organization_id=app.organization_id,
            registered_app=app,
            tenant_cluster=cluster,
            tenant_id=identity.tenant_id,
            subscription_id=placement.subscription_id,
            resource_group=placement.resource_group,
            identity_name=identity.name,
            resource_id=identity.resource_id,
            client_id=identity.client_id,
            principal_id=identity.principal_id,
            issuer=placement.issuer,
        )
        checkpoint = _checkpoint(app, cluster, placement, record_guid=record.guid)

    context = placement.context(environment_id=str(environment.guid), identity=identity)
    reconciler = _identity_driver(
        cluster, owned_context=context, native_checkpoint=checkpoint
    ).owned_reconciler()
    try:
        result = reconciler.reconcile(federations=federations, grants=grants)
    finally:
        reconciler.close()
    return record, context, result


def remove_identities(app: RegisteredApp) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove every Azure identity recorded for ``app``, one cluster at a time.

    Returns a report per identity it reached and the failures it could not
    get past. Each record is soft-deleted once its identity is deleted or found
    already gone. An identity that still carries another party's federation
    or role assignment keeps everything but what this owner added, and its
    record stays so a later removal can finish the job; the report says which.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.visibility import cluster_owned_and_live
    from astrolift_services.models import AzureWorkloadIdentity
    from core.app_deploy import AppDeployError

    reports: list[dict[str, Any]] = []
    failures: list[str] = []
    records = (
        AzureWorkloadIdentity.objects.select_for_update()
        .filter(organization_id=app.organization_id, registered_app=app)
        .order_by("pk")
    )
    for record in records:
        cluster = (
            TenantCluster.objects.select_related("provider_plugin")
            .filter(pk=record.tenant_cluster_id)
            .first()
        )
        if (
            not cluster_owned_and_live(cluster, app.organization_id)
            or cluster.provider_plugin.slug != "azure"
        ):
            # Nothing can reach that cluster's identity any more, so a kept
            # record would fail every later removal. Say where it is, once.
            failures.append(
                f"{record.resource_id} is on a cluster that is no longer a live Azure cluster of the app's "
                "organization; it was left in place, delete it in Azure",
            )
            record.soft_delete()
            continue
        try:
            reports.append(_remove_one(app, cluster, record))
        except (ValueError, RuntimeError, AppDeployError) as exc:
            # The ports' errors are sanitized and the rest are the platform's
            # own refusals, so each can name its identity in the report.
            failures.append(f"{record.resource_id}: {exc}")
    return reports, failures


def _remove_one(app: RegisteredApp, cluster: TenantCluster, record: AzureWorkloadIdentity) -> dict[str, Any]:
    from azure.identity_owned import AzureIdentityContext

    from astrolift_lifecycle.models import AppEnvironment

    # The context's environment names nothing native. A migration moves the
    # environment's binding off this cluster and leaves the identity here, so
    # any of the app's environments stands in once none is bound to it.
    environment = (
        AppEnvironment.all_objects.filter(registered_app=app, tenant_cluster=cluster).order_by("pk").first()
        or AppEnvironment.all_objects.filter(registered_app=app).order_by("pk").first()
    )
    if environment is None:
        raise AzureWorkloadIdentityError(f"app {app.slug} has no environment")
    placement = placement_for(app, cluster)
    # Removal federates nothing new, so it holds the identity to its record,
    # including the issuer it was federated with, not to the current config.
    context = AzureIdentityContext(
        organization_id=placement.organization_id,
        app_id=placement.app_id,
        environment_id=str(environment.guid),
        cluster_id=placement.cluster_id,
        tenant_id=record.tenant_id,
        subscription_id=record.subscription_id,
        resource_group=record.resource_group,
        identity_name=record.identity_name,
        resource_id=record.resource_id,
        client_id=record.client_id,
        principal_id=record.principal_id,
        issuer=record.issuer,
    )
    checkpoint = _checkpoint(app, cluster, placement, record_guid=record.guid, app_must_be_live=False)
    driver = _identity_driver(
        cluster,
        tenant_id=record.tenant_id,
        subscription_id=record.subscription_id,
        resource_group=record.resource_group,
        cluster_oidc_issuer=record.issuer,
        owned_context=context,
        native_checkpoint=checkpoint,
    ).owned_reconciler()
    try:
        result: AzureIdentityRemovalResult = driver.remove()
    finally:
        driver.close()
    if result.identity_deleted or result.identity_absent:
        record.soft_delete()
    return {
        "cluster_slug": cluster.slug,
        "identity": record.resource_id,
        "deleted": result.identity_deleted,
        "absent": result.identity_absent,
        "replaced": result.identity_replaced,
        "removed_federations": list(result.removed_federation_names),
        "removed_assignments": list(result.removed_assignment_names),
        "foreign_federations": list(result.foreign_federation_names),
        "foreign_assignments": list(result.foreign_assignment_names),
        # Tokens issued before the removal stay valid until they expire.
        "propagation_verified": result.propagation_verified,
    }
