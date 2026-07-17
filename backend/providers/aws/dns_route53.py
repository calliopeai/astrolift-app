"""AWS Route53 DnsDriver (#31).

Spec ref: spec 23-provider-plugin-aws + _sdk/dns.py.

Zone resolution: callers pass ``zone`` as the DNS name (e.g.
``acme.platform.example``). This driver looks up the corresponding
HostedZoneId once + caches per-instance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError  # noqa: F401 (re-exported via raises)
from _sdk._telemetry import driver_op
from _sdk.dns import DnsDriver, DnsRecord, Record
from aws._errors import NotFoundError, map_client_error


@dataclass
class Route53Config:
    region: str = "us-east-1"
    """Route53 is a global service but boto3 still wants a region."""


def _record_scoped_to_host(record: DnsRecord, app_host: str) -> bool:
    """Whether ``record`` is part of ``app_host``'s DNS surface.

    Kept: the host itself (``pickup.zone``), its wildcard (``*.pickup.zone``),
    and any subdomain (``api.pickup.zone``). The leading-dot suffix test is
    a label-boundary match, so a sibling app that merely shares a prefix
    (``pickup-staging.zone``) is *not* pulled in. A CNAME elsewhere in the
    zone that targets the app's host (a vanity alias) counts too, since it
    resolves to the app."""
    host = app_host.rstrip(".").lower()
    name = record.name.rstrip(".").lower()
    if name == host or name == f"*.{host}" or name.endswith(f".{host}"):
        return True
    if record.type == "CNAME":
        target = record.value.rstrip(".").lower()
        if target == host or target.endswith(f".{host}"):
            return True
    return False


class Route53Driver(DnsDriver):
    def __init__(
        self,
        *,
        config: Route53Config | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config or Route53Config()
        if client is not None:
            self._r53 = client
        else:
            import boto3

            self._r53 = boto3.client(
                "route53",
                region_name=self._config.region,
            )
        self._zone_cache: dict[str, str] = {}
        self._acm: Any | None = None

    @driver_op(cloud="aws", driver="dns", audit=True, sensitive_kind="dns.ensure_record")
    def ensure_record(
        self,
        zone: str,
        name: str,
        type: str,
        value: str,
        *,
        ttl: int = 300,
    ) -> Record:
        zone_id = self._resolve_zone(zone)
        fqdn = self._fqdn(name=name, zone=zone)
        try:
            self._r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    "Comment": "managed by astrolift",
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": fqdn,
                                "Type": type,
                                "TTL": ttl,
                                "ResourceRecords": [{"Value": value}],
                            },
                        }
                    ],
                },
            )
        except Exception as exc:
            raise map_client_error(exc) from exc
        return Record(zone=zone, name=name, type=type, value=value, ttl=ttl)

    @driver_op(cloud="aws", driver="dns", audit=True, sensitive_kind="dns.delete_record")
    def delete_record(self, zone: str, name: str, type: str) -> None:
        zone_id = self._resolve_zone(zone)
        fqdn = self._fqdn(name=name, zone=zone)

        # Route53's DELETE requires the EXACT current record set;
        # fetch it first.
        try:
            response = self._r53.list_resource_record_sets(
                HostedZoneId=zone_id,
                StartRecordName=fqdn,
                StartRecordType=type,
                MaxItems="1",
            )
        except Exception as exc:
            raise map_client_error(exc) from exc

        records = response.get("ResourceRecordSets", []) or []
        match = next(
            (rs for rs in records if rs.get("Name", "").rstrip(".") == fqdn.rstrip(".") and rs.get("Type") == type),
            None,
        )
        if match is None:
            raise NotFoundError(
                f"record {name} ({type}) in zone {zone} not found",
            )

        try:
            self._r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "DELETE",
                            "ResourceRecordSet": match,
                        }
                    ],
                },
            )
        except Exception as exc:
            raise map_client_error(exc) from exc

    @driver_op(cloud="aws", driver="dns")
    def list_records(self, zone: str) -> list[Record]:
        zone_id = self._resolve_zone(zone)
        try:
            paginator = self._r53.get_paginator(
                "list_resource_record_sets",
            )
            out: list[Record] = []
            for page in paginator.paginate(HostedZoneId=zone_id):
                for rs in page.get("ResourceRecordSets", []):
                    name = rs.get("Name", "").rstrip(".")
                    rtype = rs.get("Type", "")
                    ttl = rs.get("TTL", 0)
                    for r in rs.get("ResourceRecords", []) or []:
                        out.append(
                            Record(
                                zone=zone,
                                name=self._strip_zone(fqdn=name, zone=zone),
                                type=rtype,
                                value=r.get("Value", ""),
                                ttl=ttl,
                            )
                        )
            return out
        except Exception as exc:
            raise map_client_error(exc) from exc

    # ---- zone + cert discovery for the picker (#861 / #858) -------

    @driver_op(cloud="aws", driver="dns")
    def list_zones(self) -> list[dict[str, Any]]:
        """List Route53 hosted zones for the managed-domain zone picker
        (#861).

        Rather than make the operator paste a hosted-zone id from the
        console, the UI offers the zones the platform's IAM role can
        already see. Paginates ``route53:ListHostedZones`` and returns a
        list of dicts the resolver maps to the GraphQL type:

            {"id": "Z123ABC",            # bare hosted-zone id
             "name": "example.com.",      # FQDN with trailing dot
             "private": False,            # private/internal zone
             "config_json": '{"zone_id": "Z123ABC", "certificate_arn": ""}'}

        ``config_json`` is the pre-serialized blob the operator drops
        into the dialog's DNS-config textarea — ``zone_id`` filled,
        ``certificate_arn`` left blank for the cert picker (#858) to
        populate. Raises a mapped client error on API failure; the
        resolver swallows that into an empty list so the UI degrades to
        manual entry.
        """
        import json

        out: list[dict[str, Any]] = []
        try:
            paginator = self._r53.get_paginator("list_hosted_zones")
            pages = paginator.paginate()
        except Exception as exc:
            raise map_client_error(exc) from exc
        for page in pages:
            for z in page.get("HostedZones", []) or []:
                zone_id = z.get("Id", "").rsplit("/", 1)[-1]
                if not zone_id:
                    continue
                name = z.get("Name", "")
                private = bool(z.get("Config", {}).get("PrivateZone", False))
                config_json = json.dumps(
                    {"zone_id": zone_id, "certificate_arn": ""},
                )
                out.append(
                    {
                        "id": zone_id,
                        "name": name,
                        "private": private,
                        "config_json": config_json,
                    }
                )
        return out

    @driver_op(cloud="aws", driver="dns")
    def list_certificates(self) -> list[dict[str, Any]]:
        """List ISSUED ACM certificates in the driver's region for the
        managed-domain cert picker (#858).

        The managed-domain dialog has no cluster context, so the cert
        list is resolved through the DNS driver's region-scoped ACM
        client (the same client the wildcard-cert provisioning path
        already uses). Paginates ``acm:ListCertificates`` filtered to
        ``ISSUED`` and enriches each with the primary domain name via
        ``acm:DescribeCertificate``. Returns dicts the resolver maps to
        the GraphQL cert type:

            {"arn": "...", "name": "...", "domain_name": "...", "status": "ISSUED"}

        Raises a mapped client error on ListCertificates failure; the
        resolver swallows that into an empty list so the dialog degrades
        to manual ARN entry. The per-cert describe is best-effort.
        """
        acm = self._acm_client()
        out: list[dict[str, Any]] = []
        try:
            paginator = acm.get_paginator("list_certificates")
            pages = paginator.paginate(CertificateStatuses=["ISSUED"])
        except Exception as exc:
            raise map_client_error(exc) from exc
        for page in pages:
            for summary in page.get("CertificateSummaryList", []) or []:
                arn = summary.get("CertificateArn", "")
                if not arn:
                    continue
                domain_name = summary.get("DomainName", "")
                status = summary.get("Status", "ISSUED")
                try:
                    desc = acm.describe_certificate(CertificateArn=arn)
                    cert = desc.get("Certificate", {}) or {}
                    domain_name = cert.get("DomainName", domain_name) or domain_name
                    status = cert.get("Status", status) or status
                except Exception:
                    pass
                out.append(
                    {
                        "arn": arn,
                        "name": domain_name or arn,
                        "domain_name": domain_name,
                        "status": status,
                    }
                )
        return out

    # ---- observability reads (#377) -------------------------------

    @driver_op(cloud="aws", driver="dns")
    def list_records_for_app(self, zone_or_app: str, *, app_host: str | None = None) -> list[DnsRecord]:
        """Return the operator-facing record snapshot for an app.

        Zone resolution handles both install topologies:

        * Dedicated-per-app zone: ``zone_or_app`` names the hosted zone,
          or matches the ``astrolift.io/app-slug=<slug>`` tag on it. The
          whole zone is the app's, so the ``app_host`` filter is a no-op.
        * Shared zone: many apps live in one zone (``astrolift.smdinfra.net``
          holds ``pickup``, ``faasprobe``, ...), so a slug names no zone and
          no per-app tag exists. We resolve the zone *containing* the app's
          FQDN and then scope records to that host (#1114) so one app's
          records don't leak into another app's DNS card.

        When ``app_host`` is given, only records at or under it are
        returned (see :func:`_record_scoped_to_host`); ``None`` returns the
        whole zone (dedicated-zone behavior, unchanged).

        Propagation status is reported as ``"propagated"`` for any
        record whose change-set status is ``INSYNC`` per
        ``GetChange``; we don't poll per-record (that would burn
        Route53 quota) — instead we use the most recent change-set
        for the zone as a proxy. Records older than the last change
        are inherently propagated; the freshly-changed one carries
        the actual state. Anything we can't resolve is ``"unknown"``,
        not ``"pending"``, so the operator UI doesn't dot it amber
        for a zone that's actually fine."""
        zone = self._resolve_zone_for_records(zone_or_app, app_host)
        try:
            paginator = self._r53.get_paginator(
                "list_resource_record_sets",
            )
            zone_id = self._zone_cache.get(zone.rstrip(".") + ".")
            if zone_id is None:
                # zone resolution already populated the cache; if not,
                # resolve again to seed it.
                zone_id = self._resolve_zone(zone)
            propagation = self._latest_change_propagation(zone_id)
            out: list[DnsRecord] = []
            for page in paginator.paginate(HostedZoneId=zone_id):
                for rs in page.get("ResourceRecordSets", []):
                    name = rs.get("Name", "").rstrip(".")
                    rtype = rs.get("Type", "")
                    ttl = rs.get("TTL", 0)
                    for r in rs.get("ResourceRecords", []) or []:
                        record = DnsRecord(
                            name=name,
                            type=rtype,
                            value=r.get("Value", ""),
                            ttl=ttl,
                            propagation_status=propagation,
                        )
                        # Shared-zone installs hold many apps in one zone;
                        # keep only this app's host + subdomains (#1114).
                        if app_host and not _record_scoped_to_host(record, app_host):
                            continue
                        out.append(record)
            return out
        except NotFoundError:
            raise
        except Exception as exc:
            raise map_client_error(exc) from exc

    # ---- zone + cert provisioning (#781) -------------------------

    @driver_op(cloud="aws", driver="dns", audit=True, sensitive_kind="dns.provision_zone")
    def provision_zone(self, zone: str) -> dict[str, Any]:
        caller_ref = f"astrolift-{zone}-{int(time.time())}"
        try:
            response = self._r53.create_hosted_zone(
                Name=zone,
                CallerReference=caller_ref,
            )
        except Exception as exc:
            raise map_client_error(exc) from exc
        raw_id: str = response["HostedZone"]["Id"]
        zone_id = raw_id.rsplit("/", 1)[-1]
        nameservers: list[str] = response.get("DelegationSet", {}).get("NameServers", [])
        # Seed the zone cache so subsequent calls skip the lookup.
        canonical = zone.rstrip(".") + "."
        self._zone_cache[canonical] = zone_id
        return {"zone_id": zone_id, "nameservers": nameservers}

    @driver_op(cloud="aws", driver="dns", audit=True, sensitive_kind="dns.request_wildcard_cert")
    def request_wildcard_cert(self, zone: str, zone_id: str) -> dict[str, Any]:
        acm = self._acm_client()
        try:
            cert_response = acm.request_certificate(
                DomainName=f"*.{zone}",
                ValidationMethod="DNS",
                SubjectAlternativeNames=[zone, f"*.{zone}"],
            )
        except Exception as exc:
            raise map_client_error(exc) from exc
        cert_arn: str = cert_response["CertificateArn"]
        # ACM needs a moment to populate DomainValidationOptions.
        time.sleep(1)
        try:
            desc = acm.describe_certificate(CertificateArn=cert_arn)
        except Exception as exc:
            raise map_client_error(exc) from exc
        options = desc.get("Certificate", {}).get("DomainValidationOptions", []) or []
        validation_records = [
            {
                "name": r["ResourceRecord"]["Name"],
                "type": r["ResourceRecord"]["Type"],
                "value": r["ResourceRecord"]["Value"],
            }
            for r in options
            if "ResourceRecord" in r
        ]
        return {"cert_id": cert_arn, "validation_records": validation_records}

    @driver_op(cloud="aws", driver="dns", audit=True, sensitive_kind="dns.revoke_cert")
    def revoke_cert(self, zone: str, cert_id: str) -> None:
        del zone  # not needed for ACM delete by ARN
        acm = self._acm_client()
        try:
            acm.delete_certificate(CertificateArn=cert_id)
        except Exception as exc:
            raise map_client_error(exc) from exc

    @driver_op(cloud="aws", driver="dns")
    def poll_cert_status(self, zone: str, cert_id: str) -> dict[str, Any]:
        del zone  # not needed for ACM lookup by ARN
        acm = self._acm_client()
        try:
            desc = acm.describe_certificate(CertificateArn=cert_id)
        except Exception as exc:
            raise map_client_error(exc) from exc
        raw_status: str = desc.get("Certificate", {}).get("Status", "")
        if raw_status == "ISSUED":
            status = "issued"
        elif raw_status == "PENDING_VALIDATION":
            status = "pending"
        else:
            # FAILED, EXPIRED, REVOKED, INACTIVE, VALIDATION_TIMED_OUT, …
            status = "failed"
        return {
            "status": status,
            "cert_arn": cert_id if status == "issued" else None,
        }

    # ---- internals ------------------------------------------------

    def _acm_client(self) -> Any:
        if self._acm is None:
            import boto3

            # ACM certs for CloudFront + global ALBs must be in us-east-1;
            # the driver defaults to us-east-1 via Route53Config, which is
            # also the correct region for ACM DNS-validated wildcard certs.
            self._acm = boto3.client(
                "acm",
                region_name=self._config.region,
            )
        return self._acm

    def _resolve_zone_or_app(self, zone_or_app: str) -> str:
        """Resolve a zone name OR app-slug tag-lookup to a zone name.

        Tries the input as a DNS zone first (the common case from the
        observability resolver, which already knows the zone for the
        app). On NotFoundError, falls back to scanning hosted zones
        for one tagged ``astrolift.io/app-slug=<input>``."""
        try:
            self._resolve_zone(zone_or_app)
            return zone_or_app
        except NotFoundError:
            pass

        try:
            response = self._r53.list_hosted_zones()
        except Exception as exc:
            raise map_client_error(exc) from exc

        for z in response.get("HostedZones", []) or []:
            zone_id = z["Id"].rsplit("/", 1)[-1]
            try:
                tags_resp = self._r53.list_tags_for_resource(
                    ResourceType="hostedzone",
                    ResourceId=zone_id,
                )
            except Exception:
                # Tag lookups are best-effort; a permissions hiccup on
                # one zone shouldn't kill the whole scan.
                continue
            for t in tags_resp.get("ResourceTagSet", {}).get("Tags", []) or []:
                if t.get("Key") == "astrolift.io/app-slug" and t.get("Value") == zone_or_app:
                    name = str(z.get("Name", "")).rstrip(".")
                    self._zone_cache[name + "."] = zone_id
                    return name
        raise NotFoundError(
            f"no hosted zone matches {zone_or_app!r} by name or app-slug tag",
        )

    def _resolve_zone_for_records(self, zone_or_app: str, app_host: str | None) -> str:
        """Resolve the hosted zone whose records the app-DNS card reads.

        Dedicated-zone installs resolve through ``_resolve_zone_or_app``
        (zone name or ``app-slug`` tag). Shared-zone installs have neither
        (the slug names no zone, and one zone can't carry a per-app tag),
        so fall back to the zone *containing* the app's FQDN. Without an
        ``app_host`` there's nothing to fall back to, so the original
        NotFoundError propagates (unchanged behavior)."""
        try:
            return self._resolve_zone_or_app(zone_or_app)
        except NotFoundError:
            if app_host:
                return self._resolve_containing_zone(app_host)
            raise

    def _resolve_containing_zone(self, host: str) -> str:
        """Return the hosted-zone name that contains ``host`` by walking up
        its parent domains.

        ``api.pickup.astrolift.smdinfra.net`` tries itself, then
        ``pickup.astrolift.smdinfra.net``, ``astrolift.smdinfra.net``, ...
        and returns the first that resolves to a hosted zone. Reuses
        ``_resolve_zone`` so the zone-id cache the record read relies on is
        seeded as a side effect. The TLD is never a hosted zone we manage,
        so the walk stops before the last label."""
        labels = host.rstrip(".").split(".")
        for i in range(len(labels) - 1):
            candidate = ".".join(labels[i:])
            try:
                self._resolve_zone(candidate)
                return candidate
            except NotFoundError:
                continue
        raise NotFoundError(f"no hosted zone contains host {host!r}")

    def _latest_change_propagation(self, zone_id: str) -> str:
        """Return ``"propagated"`` / ``"pending"`` / ``"unknown"`` based
        on the most recent change-set for the hosted zone.

        Route53 only exposes change status via GetChange + a change id
        we don't store. The driver attempts the cheapest path (look up
        the last change with ``change_resource_record_sets`` history
        equivalent) but Route53 has no such endpoint — so this returns
        ``"unknown"`` when we can't observe the last change, and the
        UI dots it muted. AWS users who need per-record propagation
        rely on dig from a separate vantage point."""
        # Route53 doesn't have a "get last change for zone" API; the
        # operator-facing UI is fine with "unknown" here. We keep this
        # method as a hook so a future enhancement can pass a remembered
        # change id from ensure_record through.
        del zone_id
        return "unknown"

    def _resolve_zone(self, zone: str) -> str:
        canonical = zone.rstrip(".") + "."
        if canonical in self._zone_cache:
            return self._zone_cache[canonical]
        try:
            response = self._r53.list_hosted_zones_by_name(
                DNSName=canonical,
                MaxItems="1",
            )
        except Exception as exc:
            raise map_client_error(exc) from exc

        zones = response.get("HostedZones", []) or []
        match = next(
            (z for z in zones if z.get("Name") == canonical),
            None,
        )
        if match is None:
            raise NotFoundError(f"hosted zone {zone} not found")
        zone_id = match["Id"].rsplit("/", 1)[-1]
        self._zone_cache[canonical] = zone_id
        return zone_id

    def _fqdn(self, *, name: str, zone: str) -> str:
        zone = zone.rstrip(".")
        if name == "" or name == "@":
            return f"{zone}."
        if name.endswith(zone):
            return name.rstrip(".") + "."
        return f"{name}.{zone}."

    def _strip_zone(self, *, fqdn: str, zone: str) -> str:
        zone = zone.rstrip(".")
        fqdn = fqdn.rstrip(".")
        if fqdn == zone:
            return "@"
        if fqdn.endswith("." + zone):
            return fqdn[: -(len(zone) + 1)]
        return fqdn
