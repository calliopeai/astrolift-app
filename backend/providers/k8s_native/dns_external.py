"""external-dns DnsDriver for k8s-native plugin (#50 + #9).

external-dns watches Ingress / Service / DNSEndpoint resources
and reconciles records into the configured DNS provider. This
driver emits DNSEndpoint CRDs (the CRD-based path) so the
underlying DNS provider stays operator-configured rather than
hard-coded into the platform.

Spec ref: spec 23-provider-plugin-k8s-native + _sdk/dns.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.dns import DnsDriver, Record


@dataclass(frozen=True)
class ExternalDnsConfig:
    """The provider behind external-dns is configured separately
    on the cluster (cloudflare / route53 / etc.). This driver is
    provider-agnostic — it just emits DNSEndpoint CRDs."""

    cluster_driver: Any | None = None


class ExternalDnsDriver(DnsDriver):
    def __init__(self, *, config: ExternalDnsConfig | None = None) -> None:
        self._config = config or ExternalDnsConfig()

    @driver_op(cloud="k8s_native", driver="dns", audit=True, sensitive_kind="dns.ensure_record")
    def ensure_record(
        self,
        zone: str,
        name: str,
        type: str,
        value: str,
        *,
        ttl: int = 300,
    ) -> Record:
        """Emit a DNSEndpoint CRD that external-dns reconciles."""
        if self._config.cluster_driver is None:
            # Render-only mode — caller applies via their own
            # ClusterDriver.
            return Record(
                zone=zone,
                name=name,
                type=type,
                value=value,
                ttl=ttl,
            )
        manifest = self._render_endpoint(
            zone=zone,
            name=name,
            type=type,
            value=value,
            ttl=ttl,
        )
        result = self._config.cluster_driver.apply_manifests(
            cluster="default",
            namespace="external-dns",
            manifests=[manifest],
        )
        if not result.ok:
            raise RuntimeError(
                f"failed to ensure record {name}: {result.summary()}",
            )
        return Record(
            zone=zone,
            name=name,
            type=type,
            value=value,
            ttl=ttl,
        )

    @driver_op(cloud="k8s_native", driver="dns", audit=True, sensitive_kind="dns.delete_record")
    def delete_record(self, zone: str, name: str, type: str) -> None:
        if self._config.cluster_driver is None:
            return  # render-only mode
        stub = {
            "apiVersion": "externaldns.k8s.io/v1alpha1",
            "kind": "DNSEndpoint",
            "metadata": {
                "name": _endpoint_name(zone=zone, name=name, type=type),
                "namespace": "external-dns",
            },
        }
        result = self._config.cluster_driver.delete_manifests(
            cluster="default",
            namespace="external-dns",
            manifests=[stub],
        )
        if result.errors:
            raise RuntimeError(
                f"failed to delete record {name}: {result.summary()}",
            )

    @driver_op(cloud="k8s_native", driver="dns")
    def list_records(self, zone: str) -> list[Record]:
        """Read-side requires querying external-dns's status — not
        part of the SDK protocol surface for this driver since the
        DNS provider's API is the authoritative source. Return
        empty list; callers wanting real listing should query
        the provider directly via that provider's DnsDriver."""
        return []

    def render_endpoint(
        self,
        *,
        zone: str,
        name: str,
        type: str,
        value: str,
        ttl: int = 300,
    ) -> dict[str, Any]:
        """Public render path for callers that don't have a
        cluster_driver bound."""
        return self._render_endpoint(
            zone=zone,
            name=name,
            type=type,
            value=value,
            ttl=ttl,
        )

    # ---- internals ------------------------------------------------

    def _render_endpoint(
        self,
        *,
        zone: str,
        name: str,
        type: str,
        value: str,
        ttl: int,
    ) -> dict[str, Any]:
        fqdn = zone if (name in ("", "@") or name == zone) else f"{name}.{zone.rstrip('.')}"
        return {
            "apiVersion": "externaldns.k8s.io/v1alpha1",
            "kind": "DNSEndpoint",
            "metadata": {
                "name": _endpoint_name(zone=zone, name=name, type=type),
                "namespace": "external-dns",
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/zone": zone,
                },
            },
            "spec": {
                "endpoints": [
                    {
                        "dnsName": fqdn,
                        "recordType": type,
                        "recordTTL": ttl,
                        "targets": [value],
                    }
                ],
            },
        }


def _endpoint_name(*, zone: str, name: str, type: str) -> str:
    """k8s name <= 253 chars, lowercase, dots/dashes only."""
    label = name if name not in ("", "@") else "apex"
    raw = f"{zone}-{label}-{type}".lower()
    clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-")[:253]
