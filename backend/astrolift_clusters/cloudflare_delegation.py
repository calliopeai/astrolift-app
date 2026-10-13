"""Delegate a managed child zone from a Cloudflare parent (#1787).

A managed domain such as ``astro.conflict.cloud`` is created as its own Route
53 zone. When the parent, ``conflict.cloud``, is in Cloudflare, someone has to
add the NS records that point the child at Route 53's nameservers. This
module does that, narrowly:

* **Grant.** A connection gains write only from a separate token the operator
  pastes for one parent zone: verified live (``/user/tokens/verify``) and able
  to read that zone, then sealed. The connection's read token never writes.
* **Binding.** A managed domain names the connection whose grant covers its
  parent zone; the parent must be a strict parent of the domain.
* **Write.** Only NS records, only at the exact child name. Already in place
  is a no-op; any other record at that name, or NS records pointing elsewhere,
  are reported and left alone. Nothing is ever updated or deleted.

The provisioning workflow calls :func:`delegate` right after creating the
child zone, before the certificate request, whose validation needs the
delegation. Without a binding the operator adds the records by hand, as
before.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from astrolift_clusters.cloudflare_dns_transport import CloudflareReadClient, DnsConnectionError, hostname
from astrolift_clusters.dns_provider_connections import admitted, connection_row, guid, rate_limit
from astrolift_clusters.models import DnsProviderConnection, ManagedDomain
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

MAX_NAMESERVERS = 13


def _grant_fingerprint(row: DnsProviderConnection) -> tuple:
    return (
        row.pk,
        row.organization_id,
        row.state,
        row.delegation_backend_kind,
        bytes(row.delegation_ciphertext),
        row.delegation_zone_id,
        row.delegation_zone_name,
    )


def grant(request, connection_id, connection_version, zone_id, zone_name, token) -> DnsProviderConnection:
    """Verify ``token`` against the one parent zone and seal it on the connection."""
    zone_name = hostname(zone_name)
    with admitted(request, write=True) as (org, actor):
        rate_limit(org, actor)
        connection_row(org, connection_id, connection_version)
        original = (org.pk, actor.pk)

    def current():
        with admitted(request, write=True) as (fresh_org, fresh_actor):
            if (fresh_org.pk, fresh_actor.pk) != original:
                raise DnsConnectionError("PERMISSION_DENIED")
            connection_row(fresh_org, connection_id, connection_version)

    client = CloudflareReadClient(token, current, writes=True)
    if not client.token_active():
        raise DnsConnectionError("TOKEN_INACTIVE")
    zone = client.zone(zone_id, zone_name)
    sealed = encrypt_at_rest(token.encode())
    with transaction.atomic():
        row = connection_row(org, connection_id, connection_version, locked=True)
        current()
        row.delegation_backend_kind = sealed.backend_kind
        row.delegation_ciphertext = sealed.backend_ref
        row.delegation_zone_id = zone["id"]
        row.delegation_zone_name = zone["name"]
        row.delegation_verified_at = timezone.now()
        row.save(
            update_fields=[
                "delegation_backend_kind",
                "delegation_ciphertext",
                "delegation_zone_id",
                "delegation_zone_name",
                "delegation_verified_at",
                "version",
                "updated_at",
            ]
        )
    return row


def bind(request, domain_id, domain_version, connection_id, connection_version) -> ManagedDomain:
    """Name the connection whose delegation grant covers ``domain``'s parent zone."""
    with admitted(request, write=True) as (org, _actor):
        with transaction.atomic():
            domain = (
                ManagedDomain.objects.select_for_update()
                .filter(guid=guid(domain_id), organization=org)
                .first()
            )
            if domain is None:
                raise DnsConnectionError("NOT_FOUND")
            if domain.version != domain_version:
                raise DnsConnectionError("DOMAIN_CHANGED")
            connection = connection_row(org, connection_id, connection_version, locked=True)
            if not connection.delegation_zone_id:
                raise DnsConnectionError("WRITE_NOT_GRANTED")
            child = hostname(domain.zone)
            if not child.endswith("." + connection.delegation_zone_name):
                raise DnsConnectionError("NOT_A_PARENT_ZONE")
            domain.delegation_connection = connection
            domain.delegation_zone_id = connection.delegation_zone_id
            domain.save(
                update_fields=["delegation_connection", "delegation_zone_id", "version", "updated_at"]
            )
    return domain


def plan(existing: list[dict[str, Any]], child: str, nameservers: list[str]) -> dict[str, Any]:
    """What to do at ``child`` in the parent zone. Pure.

    ``in_place`` when the NS set already matches, ``create`` with the missing
    nameservers when none of ours or anyone else's are there, ``conflict``
    otherwise (a different NS set, or any non-NS record at the child name).
    """
    wanted = sorted({hostname(ns) for ns in nameservers})
    at_child = [r for r in existing if r["name"] == child]
    ns = sorted({hostname(r["content"]) for r in at_child if r["type"] == "NS"})
    others = sorted({r["type"] for r in at_child if r["type"] != "NS"})
    if others:
        return {"action": "conflict", "reason": f"{child} already has {', '.join(others)} records"}
    if ns == wanted:
        return {"action": "in_place"}
    if ns:
        return {"action": "conflict", "reason": f"{child} is delegated to {', '.join(ns)}"}
    return {"action": "create", "nameservers": wanted}


def delegate(domain_id: int, nameservers: list[str]) -> dict[str, Any]:
    """Write the delegating NS set for a bound domain. System path (workflow).

    Returns ``{"action": "unbound" | "in_place" | "created" | "conflict" | "failed", ...}``
    and records it on the domain's ``dns_config["delegation_write"]``. Never raises.
    """
    domain = ManagedDomain.objects.filter(pk=domain_id).select_related("delegation_connection").first()
    connection = domain.delegation_connection if domain else None
    if domain is None or connection is None or not domain.delegation_zone_id:
        return {"action": "unbound"}
    snapshot = _grant_fingerprint(connection)

    def current():
        fresh = DnsProviderConnection.objects.filter(pk=connection.pk).first()
        if fresh is None or fresh.state != "ACTIVE" or _grant_fingerprint(fresh) != snapshot:
            raise DnsConnectionError("CONNECTION_CHANGED")
        if fresh.delegation_zone_id != domain.delegation_zone_id:
            raise DnsConnectionError("CONNECTION_CHANGED")

    try:
        wanted = [hostname(ns) for ns in nameservers][:MAX_NAMESERVERS]
        if not wanted:
            raise DnsConnectionError("NO_NAMESERVERS")
        token = decrypt(
            EncryptedSecret(connection.delegation_backend_kind, bytes(connection.delegation_ciphertext))
        ).decode()
        client = CloudflareReadClient(token, current, writes=True)
        child = hostname(domain.zone)
        existing = client.records(connection.delegation_zone_id, connection.delegation_zone_name)
        decision = plan(existing, child, wanted)
        if decision["action"] == "create":
            for nameserver in decision["nameservers"]:
                client.create_ns(
                    connection.delegation_zone_id, connection.delegation_zone_name, child, nameserver
                )
            outcome = {"action": "created", "nameservers": decision["nameservers"]}
        else:
            outcome = decision
    except DnsConnectionError as exc:
        outcome = {"action": "failed", "reason": exc.reason}
    except Exception:  # noqa: BLE001 — recorded, never raised into the workflow
        outcome = {"action": "failed", "reason": "UNEXPECTED"}
    _record(domain, outcome)
    return outcome


def _record(domain: ManagedDomain, outcome: dict[str, Any]) -> None:
    with transaction.atomic():
        fresh = ManagedDomain.objects.select_for_update().get(pk=domain.pk)
        config = dict(fresh.dns_config or {})
        config["delegation_write"] = {**outcome, "at": timezone.now().isoformat()}
        fresh.dns_config = config
        fresh.save(update_fields=["dns_config", "version", "updated_at"])
