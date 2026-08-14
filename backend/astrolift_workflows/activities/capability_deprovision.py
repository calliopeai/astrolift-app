"""Standalone deprovision activities for non-managed-service capabilities (#368).

Each capability already has a real ``delete`` / ``revoke`` impl in the
provider driver (``astrolift-providers/aws/{dns_route53,tls_acm,
identity_irsa,registry_ecr,ingress_alb}.py``); this module wires the
single-resource teardown path that mutations call so operators can
drop one DNS record / cert / role / repo / ingress without tearing
the whole app down.

Five activities, one per capability:

* ``deprovision_app_dns_record`` — DnsDriver.delete_record
* ``deprovision_app_certificate`` — TlsDriver.revoke_certificate
* ``deprovision_app_identity_role`` — WorkloadIdentityDriver.delete_identity_role
* ``deprovision_app_registry_repo`` — ImageRegistryDriver.delete_repo (archive=True)
* ``deprovision_app_ingress`` — IngressDriver.delete_ingress (one or all)

Each activity is short — direct driver call, returns a summary dict
the mutation surfaces back. No workflow needed; mutations call the
``_sync`` helper directly. The ``@activity.defn`` wrappers exist so
durable workflows in the future (operator-initiated cascade
teardowns, scheduled GC sweeps) can reuse the same primitive without
re-implementing the driver dispatch.

If a driver doesn't support the requested operation (some impls
raise ``NotImplementedError`` on certain operations), the activity
re-raises ``CapabilityDeprovisionError`` with a clear operator-
facing message — the mutation translates that into a
``PRECONDITION`` failure envelope.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.capability_deprovision")


class CapabilityDeprovisionError(Exception):
    """Raised when a deprovision call can't reach the driver, the
    driver doesn't support the operation, or the driver's call fails.

    Mutations catch this and surface it via ``gql_failure(PRECONDITION)``
    rather than letting the exception bubble through Strawberry.
    """


def _resolve_capability_driver(cluster, capability: str):
    """Resolve a non-cluster driver for ``cluster``'s provider plugin.

    Mirrors the pattern other activities use (secrets rotation, custom
    domain). Raises ``CapabilityDeprovisionError`` with the operator-
    facing message when the plugin doesn't register the capability —
    keeps the activity layer decoupled from the underlying error type
    (``AppDeployError`` is deploy-pipeline-flavoured terminology).
    """
    from core.app_deploy import AppDeployError, driver_for_capability

    try:
        return driver_for_capability(cluster, capability)
    except AppDeployError as exc:
        raise CapabilityDeprovisionError(str(exc)) from exc


def _cluster_for_app(app) -> Any:
    """Pick the cluster a per-app capability call lands on.

    The capabilities here (DNS, TLS, Identity, Registry, Ingress) are
    all tenant-runtime concerns; the app's ``default_tenant_cluster``
    is the canonical binding. Ingress is the one that may want a
    per-environment cluster, but those activities take a cluster
    explicitly resolved by the mutation, so this helper covers the
    common case.
    """
    cluster = getattr(app, "default_tenant_cluster", None)
    if cluster is None:
        raise CapabilityDeprovisionError(
            f"app {app.slug!r} has no default_tenant_cluster bound — "
            "bind the app to a cluster before deprovisioning per-app "
            "capabilities",
        )
    return cluster


# ---- DNS record ----------------------------------------------------


def _deprovision_dns_record_sync(
    *,
    registered_app_id: int,
    hostname: str,
    record_type: str = "CNAME",
) -> dict[str, Any]:
    """Delete one DNS record on the app's bound cluster's DnsDriver.

    The hostname is split into ``(name, parent zone)``; the driver
    speaks records relative to the zone. Returns a summary dict the
    mutation surfaces back to the operator.
    """
    from aws._errors import NotFoundError

    from astrolift_lifecycle.custom_domain_handshake import hostname_parent_zone
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.select_related(
        "default_tenant_cluster__provider_plugin",
        "organization",
    ).get(pk=registered_app_id)
    cluster = _cluster_for_app(app)
    parent_zone = hostname_parent_zone(hostname)
    name = hostname.removesuffix("." + parent_zone) if hostname != parent_zone else hostname
    dns_driver = _resolve_capability_driver(cluster, "dns")
    delete_record = getattr(dns_driver, "delete_record", None)
    if not callable(delete_record):
        raise CapabilityDeprovisionError(
            f"dns driver for cluster {cluster.slug!r} does not support delete_record",
        )
    skipped = False
    try:
        delete_record(parent_zone, name, record_type)
    except NotImplementedError as exc:
        raise CapabilityDeprovisionError(
            f"dns driver delete_record not implemented for cluster {cluster.slug!r}: {exc}",
        ) from exc
    except NotFoundError:
        # Idempotent teardown (#1100): the record is already absent — the
        # desired end state of a delete. A resumed / partial teardown must
        # converge, so treat not-found as success instead of retrying to
        # exhaustion and wedging DeregisterAppWorkflow at ``tearing_down``.
        # Genuine driver errors (perms, throttling) still propagate → retry.
        skipped = True
        log.info(
            "deprovision dns_record already absent cluster=%s zone=%s name=%s type=%s",
            cluster.slug,
            parent_zone,
            name,
            record_type,
        )
    return {
        "cluster_slug": cluster.slug,
        "zone": parent_zone,
        "name": name,
        "type": record_type,
        "skipped": skipped,
    }


@activity.defn(name="astrolift.capability_deprovision.dns_record")
async def deprovision_app_dns_record(
    registered_app_id: int,
    hostname: str,
    record_type: str = "CNAME",
) -> dict[str, Any]:
    """Activity entry — delete one DNS record."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_deprovision_dns_record_sync)(
        registered_app_id=registered_app_id,
        hostname=hostname,
        record_type=record_type,
    )
    log.info(
        "deprovision_app_dns_record cluster=%s zone=%s name=%s type=%s",
        summary["cluster_slug"],
        summary["zone"],
        summary["name"],
        summary["type"],
    )
    return summary


# ---- TLS certificate ----------------------------------------------


def _deprovision_certificate_sync(*, custom_domain_id: int) -> dict[str, Any]:
    """Revoke the auto-issued cert for a CustomDomain + reset cert
    state so the renderer stops emitting the Ingress.

    The CustomDomain row itself is preserved — operators may want to
    re-issue or BYO without re-adding the hostname. Only the cert
    state is reset.
    """
    from aws._errors import NotFoundError

    from astrolift_lifecycle.models import CustomDomain

    domain = CustomDomain.all_objects.select_related(
        "registered_app__default_tenant_cluster__provider_plugin",
        "registered_app__organization",
    ).get(pk=custom_domain_id)
    cluster = _cluster_for_app(domain.registered_app)
    tls_driver = _resolve_capability_driver(cluster, "tls")
    revoke = getattr(tls_driver, "revoke_certificate", None)
    if not callable(revoke):
        raise CapabilityDeprovisionError(
            f"tls driver for cluster {cluster.slug!r} does not support revoke_certificate",
        )
    cert_id = (domain.certificate_id or "").strip()
    revoked = False
    skipped = False
    if cert_id:
        try:
            revoke(cert_id)
            revoked = True
        except NotImplementedError as exc:
            raise CapabilityDeprovisionError(
                f"tls driver revoke_certificate not implemented for cluster {cluster.slug!r}: {exc}",
            ) from exc
        except NotFoundError:
            # Idempotent teardown (#1100): the cert is already gone (revoked
            # out-of-band, or a prior partial teardown removed it). Treat as
            # success and fall through to reset the row's cert state so the
            # workflow converges instead of retrying to exhaustion.
            skipped = True
            log.info(
                "deprovision certificate already absent cluster=%s domain_id=%s cert_id=%s",
                cluster.slug,
                custom_domain_id,
                cert_id,
            )
    # Reset cert state regardless — even if there was no auto-issued
    # cert (e.g., BYO path), the operator is asking us to clear the
    # cert binding so the renderer stops emitting the Ingress.
    domain.certificate_id = ""
    domain.byo_certificate_pem = ""
    domain.byo_certificate_uploaded_at = None
    domain.certificate_state = CustomDomain.CertificateState.NOT_REQUESTED
    domain.last_certificate_error = ""
    domain.save(
        update_fields=[
            "certificate_id",
            "byo_certificate_pem",
            "byo_certificate_uploaded_at",
            "certificate_state",
            "last_certificate_error",
            "updated_at",
            "version",
        ],
    )
    return {
        "cluster_slug": cluster.slug,
        "custom_domain_id": custom_domain_id,
        "certificate_id": cert_id,
        "revoked": revoked,
        "skipped": skipped,
    }


@activity.defn(name="astrolift.capability_deprovision.certificate")
async def deprovision_app_certificate(custom_domain_id: int) -> dict[str, Any]:
    """Activity entry — revoke cert + reset cert state on the row."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_deprovision_certificate_sync)(
        custom_domain_id=custom_domain_id,
    )
    log.info(
        "deprovision_app_certificate cluster=%s domain_id=%s cert_id=%s revoked=%s",
        summary["cluster_slug"],
        summary["custom_domain_id"],
        summary["certificate_id"],
        summary["revoked"],
    )
    return summary


# ---- Identity role -------------------------------------------------


def _identity_role_name_for(app) -> str:
    """Canonical IAM/identity role name the platform issues per app.

    Delegates to ``core.app_deploy.workload_identity_role_name`` — the single
    source of truth shared by provision (``ensure_workload_identity``), the
    deploy render (annotated ServiceAccount) and this deprovision path, so all
    three operate on the same role/SA name.
    """
    from core.app_deploy import workload_identity_role_name

    return workload_identity_role_name(app)


def _deprovision_identity_role_sync(*, registered_app_id: int) -> dict[str, Any]:
    """Delete the cloud IAM/identity role bound to the app's
    ServiceAccount via the cluster's WorkloadIdentityDriver."""
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.select_related(
        "default_tenant_cluster__provider_plugin",
        "organization",
    ).get(pk=registered_app_id)
    cluster = _cluster_for_app(app)
    identity_driver = _resolve_capability_driver(cluster, "identity")
    delete_role = getattr(identity_driver, "delete_identity_role", None)
    if not callable(delete_role):
        raise CapabilityDeprovisionError(
            f"identity driver for cluster {cluster.slug!r} does not support delete_identity_role",
        )
    from core.app_deploy import build_identity_role_name, ci_push_role_name, static_build_role_name

    # Delete every per-app IAM role the platform mints: the runtime
    # workload-identity role, the platform-build role (#978), the
    # GitHub-OIDC CI push role (#994/#1026), and the static-asset
    # build/sync role (#1010). The AWS driver already swallows NoSuchEntity
    # (#998), but a driver that surfaces a typed NotFoundError instead must
    # not halt the loop — a role the app never created is a safe no-op —
    # otherwise it orphans on teardown (the CI push role isn't even tagged
    # for the #995 scan to catch) and wedges the workflow (#1100).
    role = _identity_role_name_for(app)
    deleted: list[str] = []
    skipped: list[str] = []
    for name in (
        role,
        build_identity_role_name(app),
        ci_push_role_name(app),
        static_build_role_name(app),
    ):
        try:
            delete_role(name)
            deleted.append(name)
        except NotImplementedError as exc:
            raise CapabilityDeprovisionError(
                f"identity driver delete_identity_role not implemented for cluster {cluster.slug!r}: {exc}",
            ) from exc
        except Exception as exc:
            if type(exc).__name__ != "NotFoundError":
                raise
            # Idempotent (#1100): role already absent — desired end state.
            skipped.append(name)
            log.info(
                "deprovision identity_role already absent cluster=%s role=%s",
                cluster.slug,
                name,
            )
    return {
        "cluster_slug": cluster.slug,
        "registered_app_id": registered_app_id,
        "role": role,
        "roles_deleted": deleted,
        "roles_skipped": skipped,
    }


@activity.defn(name="astrolift.capability_deprovision.identity_role")
async def deprovision_app_identity_role(registered_app_id: int) -> dict[str, Any]:
    """Activity entry — delete the app's cloud identity role."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_deprovision_identity_role_sync)(
        registered_app_id=registered_app_id,
    )
    log.info(
        "deprovision_app_identity_role cluster=%s app_id=%s role=%s",
        summary["cluster_slug"],
        summary["registered_app_id"],
        summary["role"],
    )
    return summary


# ---- Registry repo ------------------------------------------------


def _deprovision_registry_repo_sync(
    *,
    registered_app_id: int,
    archive: bool = True,
) -> dict[str, Any]:
    """Archive (soft-delete on the registry side) the app's image repo
    + clear the platform's stored registry URI so a future provision
    starts cleanly."""
    from aws._errors import NotFoundError

    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.select_related(
        "default_tenant_cluster__provider_plugin",
        "organization",
    ).get(pk=registered_app_id)
    cluster = _cluster_for_app(app)
    registry_driver = _resolve_capability_driver(cluster, "registry")
    delete_repo = getattr(registry_driver, "delete_repo", None)
    if not callable(delete_repo):
        raise CapabilityDeprovisionError(
            f"registry driver for cluster {cluster.slug!r} does not support delete_repo",
        )
    repo_uri = (app.registry_repo_uri or "").strip()
    # The driver speaks repo *names*, not full URIs — the URI is the
    # registry endpoint + repo path (e.g.,
    # ``123.dkr.ecr.us-west-2.amazonaws.com/astrolift/acme-hello``).
    # Strip the registry host if present; fall back to the app slug
    # when no URI is recorded (matches the canonical name
    # ``ensure_repo`` would have created).
    if "/" in repo_uri:
        repo_name = repo_uri.split("/", 1)[1]
    else:
        repo_name = repo_uri or app.slug
    skipped = False
    try:
        delete_repo(repo_name, archive=archive)
    except NotImplementedError as exc:
        raise CapabilityDeprovisionError(
            f"registry driver delete_repo not implemented for cluster {cluster.slug!r}: {exc}",
        ) from exc
    except NotFoundError:
        # Idempotent (#1100): repo already absent — desired end state. Still
        # clear the URI below so a future provision starts clean.
        skipped = True
        log.info(
            "deprovision registry_repo already absent cluster=%s repo=%s",
            cluster.slug,
            repo_name,
        )
    except TypeError:
        # Older driver impls may not accept the ``archive`` kwarg —
        # fall back to positional / no-kwarg call so the deprovision
        # still goes through.
        try:
            delete_repo(repo_name)
        except NotFoundError:
            skipped = True
            log.info(
                "deprovision registry_repo already absent cluster=%s repo=%s",
                cluster.slug,
                repo_name,
            )
    # Clear the URI so a future ``provision_registry_repo`` re-runs
    # cleanly rather than skipping on the cached URI.
    app.registry_repo_uri = ""
    app.save(update_fields=["registry_repo_uri", "updated_at", "version"])
    return {
        "cluster_slug": cluster.slug,
        "registered_app_id": registered_app_id,
        "repo": repo_name,
        "archived": bool(archive),
        "skipped": skipped,
    }


@activity.defn(name="astrolift.capability_deprovision.registry_repo")
async def deprovision_app_registry_repo(
    registered_app_id: int,
    archive: bool = True,
) -> dict[str, Any]:
    """Activity entry — archive/delete the app's image repo + clear
    the platform's stored URI."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_deprovision_registry_repo_sync)(
        registered_app_id=registered_app_id,
        archive=archive,
    )
    log.info(
        "deprovision_app_registry_repo cluster=%s app_id=%s repo=%s archived=%s",
        summary["cluster_slug"],
        summary["registered_app_id"],
        summary["repo"],
        summary["archived"],
    )
    return summary


# ---- Ingress -------------------------------------------------------


def _deprovision_ingress_sync(
    *,
    registered_app_id: int,
    hostname: str | None = None,
) -> dict[str, Any]:
    """Delete one or every Ingress on the app's namespace.

    * ``hostname`` given → soft-delete the matching ``CustomDomain``
      row; the renderer drops its Ingress on the next deploy and the
      cluster GC reaps it. We don't call the IngressDriver in this
      branch — the renderer + cluster controller already handle the
      single-domain teardown via the soft-delete signal.
    * ``hostname`` None → call ``IngressDriver.delete_ingress`` for
      every CustomDomain attached to the app on its bound cluster.
    """
    from aws._errors import NotFoundError

    from astrolift_lifecycle.models import CustomDomain
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import namespace_for_app

    app = RegisteredApp.all_objects.select_related(
        "default_tenant_cluster__provider_plugin",
        "organization",
    ).get(pk=registered_app_id)
    cluster = _cluster_for_app(app)
    namespace = namespace_for_app(app)

    if hostname:
        domain = CustomDomain.objects.filter(
            registered_app=app,
            hostname=hostname,
            deleted_at__isnull=True,
        ).first()
        if domain is None:
            raise CapabilityDeprovisionError(
                f"no active custom domain {hostname!r} on app {app.slug!r}",
            )
        domain.soft_delete()
        return {
            "cluster_slug": cluster.slug,
            "namespace": namespace,
            "deleted_hostnames": [hostname],
            "driver_called": False,
        }

    # No hostname → delete every Ingress on the namespace via the
    # driver. Use the IngressDriver from the cluster's plugin; treat
    # missing impls and per-host failures as separate errors.
    ingress_driver = _resolve_capability_driver(cluster, "ingress")
    delete_ingress = getattr(ingress_driver, "delete_ingress", None)
    if not callable(delete_ingress):
        raise CapabilityDeprovisionError(
            f"ingress driver for cluster {cluster.slug!r} does not support delete_ingress",
        )
    domains = list(
        CustomDomain.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        ),
    )
    deleted_hostnames: list[str] = []
    errors: list[str] = []
    # The driver speaks (cluster, namespace, app, workload) — the
    # renderer pairs each Ingress with the app's primary public
    # workload, so the workload arg is the app's slug for single-
    # workload apps. Multi-workload apps are still served by the
    # primary deployment; passing the app slug matches what the
    # renderer emits.
    for d in domains:
        try:
            delete_ingress(cluster.slug, namespace, app.slug, app.slug)
            deleted_hostnames.append(d.hostname)
            d.soft_delete()
        except NotImplementedError as exc:
            raise CapabilityDeprovisionError(
                f"ingress driver delete_ingress not implemented for cluster {cluster.slug!r}: {exc}",
            ) from exc
        except NotFoundError:
            # Idempotent (#1100): the Ingress is already gone — the desired
            # end state. Still soft-delete the row so the binding is revoked,
            # and count it as deleted rather than an error so a resumed
            # teardown converges instead of surfacing a spurious failure.
            log.info(
                "deprovision ingress already absent cluster=%s host=%s",
                cluster.slug,
                d.hostname,
            )
            deleted_hostnames.append(d.hostname)
            d.soft_delete()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{d.hostname}: {exc}")
    return {
        "cluster_slug": cluster.slug,
        "namespace": namespace,
        "deleted_hostnames": deleted_hostnames,
        "errors": errors,
        "driver_called": True,
    }


@activity.defn(name="astrolift.capability_deprovision.ingress")
async def deprovision_app_ingress(
    registered_app_id: int,
    hostname: str | None = None,
) -> dict[str, Any]:
    """Activity entry — delete one or every Ingress on the app's
    namespace."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_deprovision_ingress_sync)(
        registered_app_id=registered_app_id,
        hostname=hostname,
    )
    log.info(
        "deprovision_app_ingress cluster=%s ns=%s hosts=%d driver_called=%s",
        summary["cluster_slug"],
        summary["namespace"],
        len(summary["deleted_hostnames"]),
        summary["driver_called"],
    )
    return summary


__all__ = [
    "CapabilityDeprovisionError",
    "_deprovision_certificate_sync",
    "_deprovision_dns_record_sync",
    "_deprovision_identity_role_sync",
    "_deprovision_ingress_sync",
    "_deprovision_registry_repo_sync",
    "_identity_role_name_for",
    "deprovision_app_certificate",
    "deprovision_app_dns_record",
    "deprovision_app_identity_role",
    "deprovision_app_ingress",
    "deprovision_app_registry_repo",
]
