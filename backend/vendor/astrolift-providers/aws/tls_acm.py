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
from typing import Any

from _sdk.tls import Certificate, TlsDriver

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
                "Tags": [{
                    "Key": "astrolift.io/managed-by", "Value": "platform",
                }],
            }
            if sans:
                kwargs["SubjectAlternativeNames"] = sans
            response = self._acm.request_certificate(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        return self.get_certificate(response["CertificateArn"])

    def get_certificate(self, certificate_id: str) -> Certificate:
        try:
            response = self._acm.describe_certificate(
                CertificateArn=certificate_id,
            )
        except self._acm.exceptions.ResourceNotFoundException as exc:
            raise NotFoundError(f"certificate {certificate_id} not found") from exc
        except Exception as exc:  # noqa: BLE001
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

    def revoke_certificate(self, certificate_id: str) -> None:
        """ACM doesn't revoke — it deletes. The next renewal cycle
        is implicit. If the cert is in use by another resource,
        ACM blocks delete (returns ResourceInUseException)."""
        try:
            self._acm.delete_certificate(CertificateArn=certificate_id)
        except self._acm.exceptions.ResourceNotFoundException as exc:
            raise NotFoundError(f"certificate {certificate_id} not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    def get_validation_cnames(
        self, certificate_id: str,
    ) -> list[tuple[str, str]]:
        """Return the (Name, Value) pairs the DnsDriver must publish
        to complete DNS-01 validation. Not part of the SDK protocol
        but a Route53/ACM-specific bridge — call this after
        ensure_certificate, hand off to Route53Driver.ensure_record."""
        try:
            response = self._acm.describe_certificate(
                CertificateArn=certificate_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

        out: list[tuple[str, str]] = []
        cert = response["Certificate"]
        for opt in cert.get("DomainValidationOptions", []) or []:
            record = opt.get("ResourceRecord")
            if record:
                out.append((record["Name"], record["Value"]))
        return out


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
