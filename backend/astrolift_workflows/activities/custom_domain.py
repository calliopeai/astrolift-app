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

    # Step 1: find the apex's authoritative nameservers.
    try:
        ns_answer = dns.resolver.resolve(apex, "NS", lifetime=5.0)
    except Exception as exc:  # noqa: BLE001
        return [], (f"NS lookup for {apex!r} failed: {type(exc).__name__}: {exc}")
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
        from core.cluster_observability import _driver_for_capability  # type: ignore[attr-defined]

        dns_driver = _driver_for_capability(cluster, "dns")
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
