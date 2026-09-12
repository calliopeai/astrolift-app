"""Activities for ``ValidateCustomDomainWorkflow`` (#397).

The validation flow has three logical steps the activity layer
exposes:

1. ``ensure_platform_managed_records`` — when the domain's parent
   zone matches a ``ManagedDomain`` row, write the required records
   into the platform-owned zone via the bound cluster's
   ``DnsDriver.ensure_record``. No-op for operator-self-serve zones.
2. ``probe_required_records`` — query the authoritative nameservers
   for the apex and check each row in ``required_dns_records``
   actually resolves to the expected value. Marks ``propagated``
   per row + persists ``last_checked_at`` / ``message``.
3. ``transition_domain_status`` — flip the parent row to
   ``validated`` (all rows propagated) or ``failed`` (any row failed
   past its budget) or leave at ``validating`` (still propagating —
   workflow retries).

dnspython is the resolver. It speaks the apex's authoritative NS
directly so cached resolvers can't return stale negative responses
during the propagation window.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.custom_domain")


def _resolve_authoritative_records(
    *,
    apex: str,
    name: str,
    record_type: str,
) -> tuple[list[str], str]:
    """Query the authoritative nameserver(s) for ``apex`` and read
    ``name`` of ``record_type``. Returns ``(values, error)`` —
    ``error`` is empty on success.

    Why authoritative vs the system resolver: we're validating that
    the operator updated their zone. The system resolver (or any
    cached resolver in between) may serve a stale negative response
    for several minutes after they make the change. Hitting the
    apex's NS records directly bypasses that.
    """
    try:
        import dns.rdatatype
        import dns.resolver
    except Exception as exc:  # noqa: BLE001
        return [], f"dnspython unavailable: {exc}"

    # Step 1: find the closest authoritative zone. A hostname can be
    # nested below a plain subdomain (for example
    # ``api.apps.example.com`` while the delegated zone is
    # ``example.com``), and querying the hostname's immediate parent for
    # NS records returns NoAnswer in that case. Walk up one label at a
    # time until an NS RRset is found.
    candidate = apex.strip(".").lower()
    ns_answer = None
    last_error: Exception | None = None
    while candidate and "." in candidate:
        try:
            ns_answer = dns.resolver.resolve(candidate, "NS", lifetime=5.0)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            candidate = candidate.split(".", 1)[1]
    if ns_answer is None:
        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "no NS answer"
        return [], f"NS lookup for {apex!r} failed: {detail}"
    nameservers: list[str] = []
    for rdata in ns_answer:
        host = str(rdata.target).rstrip(".")
        try:
            ip_answer = dns.resolver.resolve(host, "A", lifetime=5.0)
            for ip in ip_answer:
                nameservers.append(str(ip))
        except Exception:  # noqa: BLE001
            continue
    if not nameservers:
        return [], (f"could not resolve any IP for {apex!r}'s authoritative " f"nameservers")

    # Step 2: query each authoritative server directly for the record.
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = nameservers
    resolver.lifetime = 5.0
    try:
        answer = resolver.resolve(
            name.rstrip("."),
            record_type,
            lifetime=5.0,
        )
    except dns.resolver.NXDOMAIN:
        return [], (f"record {name!r} not found at the authoritative NS " f"(NXDOMAIN — still propagating?)")
    except dns.resolver.NoAnswer:
        return [], (f"no {record_type} record at {name!r} at the authoritative NS")
    except Exception as exc:  # noqa: BLE001
        return [], f"{record_type} lookup failed: {exc}"

    values: list[str] = []
    for rdata in answer:
        if record_type == "TXT":
            # dnspython returns TXT as list of bytes-quoted strings;
            # strip and decode for substring matching.
            text = "".join(s.decode("utf-8") if isinstance(s, bytes) else str(s) for s in rdata.strings)
            values.append(text)
        else:
            values.append(str(rdata.target).rstrip("."))
    return values, ""


def _probe_one_record(record: dict[str, Any], apex: str) -> dict[str, Any]:
    """Return a fresh row dict reflecting the probe result.

    ``propagated=True`` when the authoritative NS returns the
    expected value (substring match for TXT — operators sometimes
    end up with quoted/concatenated values from their zone editor;
    exact match after lowercase strip for everything else).
    """
    out = dict(record)
    name = record.get("name", "")
    kind = record.get("kind", "")
    expected = record.get("value", "")
    values, error = _resolve_authoritative_records(
        apex=apex,
        name=name,
        record_type=kind,
    )
    out["last_checked_at"] = datetime.now(UTC).isoformat()
    if error:
        out["propagated"] = False
        out["message"] = error
        return out
    if kind == "TXT":
        propagated = any(expected in v for v in values)
    else:
        # CNAME / A / AAAA — exact (case-insensitive, trailing-dot-
        # stripped) match against the configured target.
        propagated = any(v.lower().rstrip(".") == expected.lower().rstrip(".") for v in values)
    out["propagated"] = propagated
    if propagated:
        out["message"] = f"{kind} record propagated"
    else:
        out["message"] = f"{kind} resolves to {values!r}, expected {expected!r}"
    return out


def _ensure_records_sync(custom_domain_id: int) -> dict[str, Any]:
    """When the domain is platform-managed, write the records via
    the bound cluster's ``DnsDriver.ensure_record``. Otherwise no-op.

    Returns a dict ``{"ensured": list[str], "skipped": list[str],
    "errors": list[str]}``.
    """
    from astrolift_clusters.models import ManagedDomain
    from astrolift_lifecycle.custom_domain_handshake import (
        hostname_parent_zone,
    )
    from astrolift_lifecycle.models import CustomDomain

    d = CustomDomain.all_objects.select_related("registered_app").get(
        pk=custom_domain_id,
    )
    if not d.is_platform_managed_zone:
        return {
            "ensured": [],
            "skipped": [r.get("name", "") for r in d.required_dns_records or []],
            "errors": [],
            "reason": "operator-self-serve zone",
        }
    parent_zone = hostname_parent_zone(d.hostname)
    managed = ManagedDomain.objects.filter(
        zone=parent_zone,
        deleted_at__isnull=True,
    ).first()
    if managed is None:
        return {
            "ensured": [],
            "skipped": [],
            "errors": [],
            "reason": f"no ManagedDomain row for parent zone {parent_zone!r}",
        }
    # Resolve the cluster + DnsDriver. The cluster is the app's
    # default tenant cluster (matches the choice the handshake
    # builder made at add-domain time).
    cluster = getattr(d.registered_app, "default_tenant_cluster", None)
    if cluster is None:
        return {
            "ensured": [],
            "skipped": [],
            "errors": [],
            "reason": "app has no default_tenant_cluster bound",
        }
    try:
        from core.app_deploy import driver_for_capability

        dns_driver = driver_for_capability(cluster, "dns")
    except Exception as exc:  # noqa: BLE001
        return {
            "ensured": [],
            "skipped": [],
            "errors": [str(exc)],
            "reason": "dns driver unresolvable",
        }

    ensured: list[str] = []
    errors: list[str] = []
    for r in d.required_dns_records or []:
        try:
            dns_driver.ensure_record(
                zone=parent_zone,
                name=r.get("name", "").rstrip("."),
                type=r.get("kind", ""),
                value=r.get("value", ""),
                ttl=int(r.get("ttl", 300)),
            )
            ensured.append(r.get("name", ""))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{r.get('name', '')}: {exc}")
    return {"ensured": ensured, "errors": errors, "skipped": []}


@activity.defn(name="astrolift.custom_domain.ensure_platform_records")
async def ensure_platform_managed_records(
    custom_domain_id: int,
) -> dict[str, Any]:
    """For platform-managed-zone domains, write the required records
    via the bound cluster's ``DnsDriver``. No-op for self-serve."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_ensure_records_sync)(custom_domain_id)
    log.info(
        "ensure_platform_managed_records ensured=%s errors=%s",
        result.get("ensured"),
        result.get("errors"),
        extra={"custom_domain_id": custom_domain_id},
    )
    return result


def _probe_sync(custom_domain_id: int) -> dict[str, Any]:
    from astrolift_lifecycle.custom_domain_handshake import (
        hostname_parent_zone,
    )
    from astrolift_lifecycle.models import CustomDomain

    d = CustomDomain.all_objects.get(pk=custom_domain_id)
    apex = hostname_parent_zone(d.hostname)
    new_rows: list[dict[str, Any]] = []
    all_propagated = True
    for r in d.required_dns_records or []:
        probed = _probe_one_record(r, apex)
        new_rows.append(probed)
        if not probed.get("propagated", False):
            all_propagated = False
    d.required_dns_records = new_rows
    d.last_checked_at = datetime.now(UTC)
    d.save(
        update_fields=[
            "required_dns_records",
            "last_checked_at",
            "updated_at",
            "version",
        ],
    )
    return {
        "all_propagated": all_propagated,
        "records": new_rows,
    }


@activity.defn(name="astrolift.custom_domain.probe_required_records")
async def probe_required_records(
    custom_domain_id: int,
) -> dict[str, Any]:
    """Probe each required record at the authoritative NS + update
    the per-row propagation state on the CustomDomain row."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_probe_sync)(custom_domain_id)
    log.info(
        "probe_required_records all_propagated=%s",
        result.get("all_propagated"),
        extra={"custom_domain_id": custom_domain_id},
    )
    return result


def _transition_sync(
    custom_domain_id: int,
    success: bool,
    error_message: str = "",
) -> str:
    from astrolift_lifecycle.models import CustomDomain

    d = CustomDomain.all_objects.get(pk=custom_domain_id)
    if success:
        d.validation_status = CustomDomain.ValidationStatus.VALIDATED
        d.last_validation_error = ""
    else:
        d.validation_status = CustomDomain.ValidationStatus.FAILED
        d.last_validation_error = error_message[:512]
    d.save(
        update_fields=[
            "validation_status",
            "last_validation_error",
            "updated_at",
            "version",
        ],
    )
    return d.validation_status


@activity.defn(name="astrolift.custom_domain.transition_status")
async def transition_domain_status(
    custom_domain_id: int,
    success: bool,
    error_message: str = "",
) -> str:
    """Flip ``validation_status`` to validated / failed + capture the
    operator-facing error message on failure."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    final = await sync_to_async(_transition_sync)(
        custom_domain_id,
        success,
        error_message,
    )
    log.info(
        "transition_domain_status final=%s",
        final,
        extra={"custom_domain_id": custom_domain_id},
    )
    return final


def _issue_cert_sync(custom_domain_id: int) -> dict[str, Any]:
    """Call the bound cluster's ``TlsDriver.ensure_certificate`` for
    the domain. Persists the returned cert id onto
    ``CustomDomain.certificate_id`` so the UI can show "cert active"
    + the ingress renderer can wire it to the workload's listener.

    Strategy picks itself by zone-management mode:
      ``is_platform_managed_zone=True``: the platform owns the zone
        (subdomain delegated to astrolift OR managed-domain row), so
        DNS-01 validation can write challenge records via the
        DnsDriver. Strategy is the driver's preferred DNS-validated
        path — ``acm_dns_validated`` on AWS, ``gcp_managed_cert`` on
        GCP, ``azure_managed_cert`` on Azure, ``letsencrypt`` (DNS-01)
        on k8s_native.

      ``is_platform_managed_zone=False``: operator owns the zone
        (Cloudflare / self-hosted / wherever). Platform can't write
        challenge records, so cert issuance must work over the
        already-propagated CNAME — HTTP-01 on k8s_native (cert-manager
        does the http-01 dance over port-80), ``letsencrypt`` http-01
        on the managed clouds. AWS clusters fall back to operator-
        supplied cert (``provided``) since ACM can't HTTP-01 — the UI
        surfaces a callout pointing the operator at the BYO-cert flow.

    The driver impl owns the strategy default; the activity passes
    the right key based on zone mode so the driver doesn't have to
    re-derive it.
    """
    from astrolift_lifecycle.models import CustomDomain

    d = CustomDomain.all_objects.select_related("registered_app").get(
        pk=custom_domain_id,
    )
    if d.certificate_state == CustomDomain.CertificateState.BYO:
        # Operator opted into BYO via uploadCustomDomainCertificate —
        # don't auto-issue or overwrite. Renderer reads the uploaded
        # PEM directly.
        return {
            "ok": True,
            "certificate_id": d.certificate_id,
            "state": d.certificate_state,
            "message": "BYO certificate — auto-issuance skipped",
        }
    if d.certificate_id and d.certificate_state == CustomDomain.CertificateState.ACTIVE:
        return {
            "ok": True,
            "certificate_id": d.certificate_id,
            "state": d.certificate_state,
            "message": "certificate already issued",
        }

    def _persist_fail(message: str) -> dict[str, Any]:
        d.certificate_state = CustomDomain.CertificateState.FAILED
        d.last_certificate_error = message[:512]
        d.save(
            update_fields=[
                "certificate_state",
                "last_certificate_error",
                "updated_at",
                "version",
            ],
        )
        return {
            "ok": False,
            "certificate_id": "",
            "state": d.certificate_state,
            "message": message,
        }

    cluster = getattr(d.registered_app, "default_tenant_cluster", None)
    if cluster is None:
        return _persist_fail("app has no default_tenant_cluster — no TLS driver to call")
    try:
        from core.app_deploy import driver_for_capability

        tls_driver = driver_for_capability(cluster, "tls")
    except Exception as exc:  # noqa: BLE001
        return _persist_fail(f"tls driver unresolvable: {exc}")

    strategy = _pick_tls_strategy(
        plugin_slug=getattr(getattr(cluster, "provider_plugin", None), "slug", "") or "",
        is_platform_managed_zone=bool(d.is_platform_managed_zone),
    )
    # Flip to ISSUING so the UI shows a spinner while the driver call
    # is in flight. Clear last_certificate_error from any prior fail.
    d.certificate_state = CustomDomain.CertificateState.ISSUING
    d.last_certificate_error = ""
    d.save(
        update_fields=[
            "certificate_state",
            "last_certificate_error",
            "updated_at",
            "version",
        ],
    )

    try:
        cert = tls_driver.ensure_certificate(d.hostname, strategy=strategy)
    except TypeError:
        try:
            cert = tls_driver.ensure_certificate(d.hostname)
        except Exception as exc:  # noqa: BLE001
            return _persist_fail(f"ensure_certificate (no-strategy fallback): {exc}")
    except Exception as exc:  # noqa: BLE001
        return _persist_fail(f"ensure_certificate strategy={strategy!r}: {exc}")
    cert_id = getattr(cert, "id", "") or ""
    if not cert_id:
        return _persist_fail(
            f"driver returned no certificate id (strategy={strategy!r})",
        )
    d.certificate_id = cert_id
    d.certificate_state = CustomDomain.CertificateState.ACTIVE
    d.last_certificate_error = ""
    d.save(
        update_fields=[
            "certificate_id",
            "certificate_state",
            "last_certificate_error",
            "updated_at",
            "version",
        ],
    )
    return {
        "ok": True,
        "certificate_id": cert_id,
        "state": d.certificate_state,
        "message": f"certificate id={cert_id} (strategy={strategy})",
    }


def _pick_tls_strategy(
    *,
    plugin_slug: str,
    is_platform_managed_zone: bool,
) -> str:
    """Map (cloud, zone-mode) → ``TlsDriver`` strategy key.

    Platform-managed zones use the DNS-validated path because we can
    write challenge records ourselves. Externally-managed zones fall
    back to HTTP-01 (where supported) — the operator already CNAMEd
    the hostname to the cluster ingress so port-80 is reachable.

    AWS HTTP-01 isn't covered by ACM, so externally-managed AWS
    domains land on ``provided`` (operator imports a cert into ACM)
    — the renderer's TLS block reads the cert id verbatim.
    """
    cloud = plugin_slug.lower()
    if is_platform_managed_zone:
        if cloud == "aws":
            return "acm_dns_validated"
        if cloud == "gcp":
            return "gcp_managed_cert"
        if cloud == "azure":
            return "azure_managed_cert"
        return "letsencrypt"  # k8s_native via cert-manager DNS-01
    # External-zone path
    if cloud == "aws":
        return "provided"
    return "letsencrypt_http01"


@activity.defn(name="astrolift.custom_domain.issue_certificate")
async def issue_custom_domain_certificate(
    custom_domain_id: int,
) -> dict[str, Any]:
    """Fire ``TlsDriver.ensure_certificate`` for a validated domain
    + persist the returned cert id on the row."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_issue_cert_sync)(custom_domain_id)
    log.info(
        "issue_custom_domain_certificate ok=%s certificate_id=%s",
        result.get("ok"),
        result.get("certificate_id"),
        extra={"custom_domain_id": custom_domain_id},
    )
    return result
