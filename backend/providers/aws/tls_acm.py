"""AWS ACM TlsDriver (#31).

Spec ref: spec 23-provider-plugin-aws + _sdk/tls.py.

ACM-issued certs are DNS-01 validated. The driver requests the
cert + returns the validation CNAMEs the operator (or DnsDriver)
must publish to complete validation. ACM automatically renews
certs once they're issued and the validation records remain in
place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.tls import Certificate, CertificateInfo, TlsDriver
from aws._errors import NotFoundError, map_client_error


@dataclass(frozen=True)
class ACMConfig:
    region: str
    """ACM region. For ALB integration, this MUST match the ALB's
    region. CloudFront uses us-east-1 regardless."""


class ACMDriver(TlsDriver):
    def __init__(
        self,
        *,
        config: ACMConfig,
        client: Any | None = None,
    ) -> None:
        self._config = config
        if client is not None:
            self._acm = client
        else:
            import boto3

            self._acm = boto3.client("acm", region_name=config.region)

    @driver_op(cloud="aws", driver="tls", audit=True, sensitive_kind="tls.mint")
    def ensure_certificate(
        self,
        domain: str,
        *,
        sans: list[str] | None = None,
        strategy: str = "letsencrypt",
    ) -> Certificate:
        """For ACM, the strategy parameter is informational —
        ACM is always its own validation method (DNS-01 from
        ACM-controlled records).

        The 'letsencrypt' strategy default in the SDK protocol
        applies when the operator wants cert-manager + LE; on
        AWS this driver is used when they want ACM directly
        (e.g. for ALB termination)."""
        if strategy not in ("acm_dns_validated", "letsencrypt", "provided"):
            raise ValueError(
                f"unknown strategy {strategy!r} for ACM driver",
            )
        sans = sans or []
        try:
            kwargs: dict[str, Any] = {
                "DomainName": domain,
                "ValidationMethod": "DNS",
                "Tags": [
                    {
                        "Key": "astrolift.io/managed-by",
                        "Value": "platform",
                    }
                ],
            }
            if sans:
                kwargs["SubjectAlternativeNames"] = sans
            response = self._acm.request_certificate(**kwargs)
        except Exception as exc:
            raise map_client_error(exc) from exc
        return self.get_certificate(response["CertificateArn"])

    @driver_op(cloud="aws", driver="tls")
    def get_certificate(self, certificate_id: str) -> Certificate:
        try:
            response = self._acm.describe_certificate(
                CertificateArn=certificate_id,
            )
        except self._acm.exceptions.ResourceNotFoundException as exc:
            raise NotFoundError(f"certificate {certificate_id} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

        cert = response["Certificate"]
        return Certificate(
            id=cert["CertificateArn"],
            domain=cert.get("DomainName", ""),
            sans=cert.get("SubjectAlternativeNames", []) or [],
            strategy="acm_dns_validated",
            status=cert.get("Status", ""),
            not_before=_iso_or_none(cert.get("NotBefore")),
            not_after=_iso_or_none(cert.get("NotAfter")),
        )

    @driver_op(cloud="aws", driver="tls", audit=True, sensitive_kind="tls.revoke")
    def revoke_certificate(self, certificate_id: str) -> None:
        """ACM doesn't revoke — it deletes. The next renewal cycle
        is implicit. If the cert is in use by another resource,
        ACM blocks delete (returns ResourceInUseException)."""
        try:
            self._acm.delete_certificate(CertificateArn=certificate_id)
        except self._acm.exceptions.ResourceNotFoundException as exc:
            raise NotFoundError(f"certificate {certificate_id} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    # ---- observability reads (#377) -------------------------------

    @driver_op(cloud="aws", driver="tls")
    def list_certificates(
        self,
        filter_hostname: str | None = None,
    ) -> list[CertificateInfo]:
        """Return ACM cert snapshots for the operator-facing card.

        ACM's ``list_certificates`` returns minimal headers; we have to
        ``describe_certificate`` per id to pick up ``NotAfter`` +
        ``Issuer`` + ``RenewalSummary``. ``filter_hostname`` is matched
        against ``DomainName`` and every ``SubjectAlternativeNames``
        entry with DNS semantics (:func:`_cert_covers_host`): an exact
        host match, or a wildcard cert (``*.zone``) covering the host as
        a direct subdomain. That means an app on ``api.zone`` is matched
        by a ``*.zone`` wildcard or a SAN cert that lists ``api.zone``,
        which the previous ``filter_hostname in name`` substring test
        missed (wrong direction — the query host is longer than the
        wildcard name). ``days_until_expiry`` is clamped to 0 when
        ``NotAfter`` is in the past — the UI flags anything < 30 days as
        red so an expired cert renders as "0d red" rather than a
        confusing negative number."""
        try:
            paginator = self._acm.get_paginator("list_certificates")
            ids: list[str] = []
            for page in paginator.paginate():
                for summary in page.get("CertificateSummaryList", []) or []:
                    ids.append(summary["CertificateArn"])
        except Exception as exc:
            raise map_client_error(exc) from exc

        out: list[CertificateInfo] = []
        for cert_arn in ids:
            try:
                response = self._acm.describe_certificate(
                    CertificateArn=cert_arn,
                )
            except self._acm.exceptions.ResourceNotFoundException:
                # Cert was deleted between list + describe; skip rather
                # than abort the whole listing.
                continue
            except Exception as exc:
                raise map_client_error(exc) from exc

            cert = response["Certificate"]
            domain = cert.get("DomainName", "")
            sans = cert.get("SubjectAlternativeNames", []) or []
            if filter_hostname is not None and not _cert_covers_host(filter_hostname, [domain, *sans]):
                continue

            not_after_dt = cert.get("NotAfter")
            not_after_iso = _iso_or_none(not_after_dt) or ""
            days_until = _days_until_expiry(not_after_dt)
            renewal_status = _renewal_status(cert)
            issuer = cert.get("Issuer") or ""

            out.append(
                CertificateInfo(
                    id=cert_arn,
                    hostname=domain,
                    issuer=issuer,
                    not_after=not_after_iso,
                    days_until_expiry=days_until,
                    renewal_status=renewal_status,
                )
            )
        return out

    @driver_op(cloud="aws", driver="tls")
    def get_validation_cnames(
        self,
        certificate_id: str,
    ) -> list[tuple[str, str]]:
        """Return the (Name, Value) pairs the DnsDriver must publish
        to complete DNS-01 validation. Not part of the SDK protocol
        but a Route53/ACM-specific bridge — call this after
        ensure_certificate, hand off to Route53Driver.ensure_record."""
        try:
            response = self._acm.describe_certificate(
                CertificateArn=certificate_id,
            )
        except Exception as exc:
            raise map_client_error(exc) from exc

        out: list[tuple[str, str]] = []
        cert = response["Certificate"]
        for opt in cert.get("DomainValidationOptions", []) or []:
            record = opt.get("ResourceRecord")
            if record:
                out.append((record["Name"], record["Value"]))
        return out


def _cert_covers_host(host: str, cert_names: list[str]) -> bool:
    """Return True when any of ``cert_names`` covers ``host`` under DNS
    cert-matching semantics (RFC 6125 wildcard rules, simplified).

    ``cert_names`` is the cert's ``DomainName`` plus its
    ``SubjectAlternativeNames``. A name matches when it equals ``host``
    exactly, or when it is a left-most wildcard (``*.parent``) and
    ``host`` is a *direct* subdomain of ``parent`` (exactly one extra
    label — ``*.foo.net`` matches ``bar.foo.net`` but not
    ``a.bar.foo.net`` or ``foo.net`` itself). Comparison is
    case-insensitive and tolerant of a trailing dot."""
    h = host.strip().lower().rstrip(".")
    if not h:
        return False
    for raw in cert_names:
        name = (raw or "").strip().lower().rstrip(".")
        if not name:
            continue
        if name == h:
            return True
        if name.startswith("*."):
            parent = name[2:]
            if parent and h.endswith("." + parent):
                label = h[: -(len(parent) + 1)]
                if label and "." not in label:
                    return True
    return False


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _days_until_expiry(not_after: Any) -> int:
    """Return ``max(0, days_until(not_after))`` from now (UTC).

    Returns 0 when ``not_after`` is None or unparseable so the UI's
    ``< 30`` red threshold trips on missing data — an absent
    expiration is just as concerning as an imminent one. Negative
    deltas (already-expired) clamp to 0 because the operator-facing
    chip "0d" + red is clearer than "-3d"."""
    if not_after is None:
        return 0
    now = datetime.now(UTC)
    if hasattr(not_after, "tzinfo"):
        when = not_after if not_after.tzinfo else not_after.replace(tzinfo=UTC)
    else:
        try:
            when = datetime.fromisoformat(str(not_after))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        except ValueError:
            return 0
    delta = when - now
    return max(0, int(delta.days))


def _renewal_status(cert: dict[str, Any]) -> str:
    """Map ACM's ``RenewalSummary.RenewalStatus`` (+ cert ``Type``)
    onto the SDK's renewal-status enum.

    ``Type=AMAZON_ISSUED`` + ``RenewalStatus=SUCCESS|PENDING_AUTO_RENEWAL``
    is ``"auto"``. ``RenewalStatus=FAILED`` is ``"failed"``.
    ``Type=IMPORTED`` is ``"manual"`` (operator brought their own).
    Anything we don't recognise is ``"unknown"``."""
    cert_type = cert.get("Type", "")
    if cert_type == "IMPORTED":
        return "manual"
    summary = cert.get("RenewalSummary") or {}
    status = summary.get("RenewalStatus", "")
    if status in ("SUCCESS", "PENDING_AUTO_RENEWAL"):
        return "auto"
    if status == "FAILED":
        return "failed"
    if cert_type == "AMAZON_ISSUED":
        # ACM cert with no renewal summary yet (freshly issued) — auto
        # by default; ACM will renew unless the validation records get
        # removed. Reporting "unknown" here would dot a healthy cert
        # muted on day one of issuance.
        return "auto"
    return "unknown"
