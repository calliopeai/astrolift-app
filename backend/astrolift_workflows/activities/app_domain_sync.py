"""Activities for ``SyncAppDomainWorkflow`` (#143, spec 06 §4.17).

Three activities the domain-sync workflow needs and no other flow has:

* ``plan_app_domain_sync`` — read side. Per live environment, compute the
  hostnames the app served under its previous subdomain and the ones it
  serves under the current one, plus the wildcard SANs the zone's cert
  already covers, so the workflow can diff them and decide whether a
  per-hostname cert is needed at all.
* ``verify_app_hostnames`` — the VERIFY_E2E step: resolve each new
  hostname and complete a TLS handshake against it, so "renamed" means
  the new name actually answers.
* ``revert_app_subdomain`` — the rollback anchor. The renderers derive
  the hostname from ``RegisteredApp.subdomain``, so putting the old value
  back is what makes a re-apply restore the old routing.

The DNS write, ingress patch, cert request and propagation wait all reuse
activities that already exist (``ensure_static_dns``, ``apply_manifests``,
``request_wildcard_cert_for_zone``, ``wait_dns``).
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.app_domain_sync")

# TLS handshake budget per hostname inside ``verify_app_hostnames``. The
# workflow's own deadline for the step bounds the retry loop; this bounds
# one attempt so a black-holed address can't eat the whole window.
_HANDSHAKE_TIMEOUT_SECONDS = 5.0
_VERIFY_POLL_SECONDS = 5.0


def _hostnames_for(app: Any, manifest: Any, *, zone: str, subdomain: str) -> list[str]:
    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames

    org_slug = app.organization.slug if app.organization_id else "none"
    return [
        wh.hostname
        for wh in compute_hostnames(
            manifest,
            HostnameInputs(
                app_slug=app.slug,
                org_slug=org_slug,
                base_zone=zone,
                subdomain_override=subdomain,
            ),
        )
    ]


def _plan_app_domain_sync_sync(
    registered_app_id: int,
    previous_subdomain: str,
) -> dict[str, Any]:
    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.select_related("organization").get(pk=registered_app_id)
    if not (app.manifest_raw or "").strip():
        return {"app_slug": app.slug, "plans": []}
    manifest = normalize(parse_raw(app.manifest_raw), defaults=NormalizationDefaults())

    new_subdomain = (app.subdomain or app.slug).strip().lower()
    old_subdomain = (previous_subdomain or app.slug).strip().lower()

    plans: list[dict[str, Any]] = []
    envs = (
        AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True)
        .select_related("managed_domain", "tenant_cluster")
        .order_by("pk")
    )
    for env in envs:
        managed_domain = env.managed_domain
        if managed_domain is None:
            # No platform hostname in this env — nothing to re-point.
            continue
        # Only a live rollout can be re-applied: the ingress patch is a
        # re-render of an existing Deployment row, and an env that never
        # deployed has no ingress to patch in the first place.
        deployment = (
            Deployment.objects.filter(
                registered_app=app,
                app_environment=env,
                status=Deployment.Status.RUNNING,
                deleted_at__isnull=True,
            )
            .order_by("-created_at", "-pk")
            .first()
        )
        if deployment is None:
            continue
        dns_config = dict(managed_domain.dns_config or {})
        # A zone wildcard is the platform's cert unit for a managed zone.
        # When one exists every one-label-deep hostname under the zone is
        # already covered, which is what lets the workflow skip a cert
        # request on a plain rename.
        has_wildcard = bool(
            managed_domain.is_wildcard_managed
            or dns_config.get("certificate_arn")
            or managed_domain.provision_cert_id
        )
        plans.append(
            {
                "app_environment_id": env.pk,
                "deployment_id": deployment.pk,
                "cluster_id": env.tenant_cluster_id,
                "zone": managed_domain.zone,
                "zone_id": str(dns_config.get("zone_id", "")),
                "old_hostnames": _hostnames_for(
                    app, manifest, zone=managed_domain.zone, subdomain=old_subdomain
                ),
                "new_hostnames": _hostnames_for(
                    app, manifest, zone=managed_domain.zone, subdomain=new_subdomain
                ),
                "wildcard_sans": [f"*.{managed_domain.zone}"] if has_wildcard else [],
            }
        )

    return {"app_slug": app.slug, "plans": plans}


@activity.defn(name="astrolift.app_domain_sync.plan")
async def plan_app_domain_sync(
    registered_app_id: int,
    previous_subdomain: str,
) -> dict[str, Any]:
    """Per live environment: old vs new hostnames + the zone's wildcard SANs.

    Returns ``{"app_slug": str, "plans": [...]}``. An empty ``plans`` means
    there is nothing to reconcile (no managed domain, no live rollout, or no
    saved manifest) and the workflow exits successfully.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_plan_app_domain_sync_sync)(
        registered_app_id,
        previous_subdomain,
    )
    log.info(
        "plan_app_domain_sync app=%s envs=%d",
        result.get("app_slug"),
        len(result.get("plans", [])),
        extra={"registered_app_id": registered_app_id},
    )
    return result


def _revert_app_subdomain_sync(registered_app_id: int, previous_subdomain: str) -> str:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    if app.subdomain == previous_subdomain:
        return app.subdomain
    app.subdomain = previous_subdomain
    app.save(update_fields=["subdomain", "updated_at", "version"])
    return app.subdomain


@activity.defn(name="astrolift.app_domain_sync.revert_subdomain")
async def revert_app_subdomain(registered_app_id: int, previous_subdomain: str) -> str:
    """Put the pre-rename subdomain back on the row.

    Rollback's first move. Every renderer derives the hostname from this
    field, so restoring it is what makes the re-applied ingress + DNS point
    at the old name again instead of leaving the app stranded between two.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    restored = await sync_to_async(_revert_app_subdomain_sync)(
        registered_app_id,
        previous_subdomain,
    )
    log.info(
        "revert_app_subdomain restored=%s",
        restored,
        extra={"registered_app_id": registered_app_id},
    )
    return restored


def _tls_handshake(hostname: str) -> None:
    """Connect to ``hostname:443`` and complete a verified TLS handshake.

    ``check_hostname`` is on, so a cert that doesn't cover the new name
    fails here rather than being reported as a successful rename.
    """
    import socket
    import ssl

    context = ssl.create_default_context()
    with socket.create_connection((hostname, 443), timeout=_HANDSHAKE_TIMEOUT_SECONDS) as raw:
        with context.wrap_socket(raw, server_hostname=hostname):
            return


def _verify_app_hostnames_sync(hostnames: list[str], timeout_seconds: int) -> dict[str, Any]:
    import time

    deadline = time.monotonic() + max(timeout_seconds, 1)
    pending = list(dict.fromkeys(h for h in hostnames if h))
    verified: list[str] = []
    errors: dict[str, str] = {}
    while pending:
        still: list[str] = []
        for host in pending:
            try:
                _tls_handshake(host)
            except Exception as exc:  # noqa: BLE001 — reported, not raised
                errors[host] = f"{type(exc).__name__}: {exc}"[:256]
                still.append(host)
                continue
            errors.pop(host, None)
            verified.append(host)
        pending = still
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(_VERIFY_POLL_SECONDS)
    return {
        "verified": verified,
        "unverified": pending,
        "errors": errors,
    }


@activity.defn(name="astrolift.app_domain_sync.verify_hostnames")
async def verify_app_hostnames(
    hostnames: list[str],
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """VERIFY_E2E: every new hostname resolves and terminates TLS.

    Raises when any hostname is still unverified at the deadline so the
    workflow rolls the rename back instead of reporting a name that does
    not answer.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_verify_app_hostnames_sync)(list(hostnames), timeout_seconds)
    activity.heartbeat()
    if result["unverified"]:
        raise RuntimeError(
            "hostname verification failed for "
            + ", ".join(f"{h} ({result['errors'].get(h, 'unknown')})" for h in result["unverified"])
        )
    log.info("verify_app_hostnames verified=%s", result["verified"])
    return result
