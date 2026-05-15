"""Custom-domain DNS handshake (#397).

When an operator adds a custom hostname (``api.example.com``) to an
astrolift app, the platform needs to:

1. Generate a unique challenge token the operator pastes into a TXT
   record at ``_astrolift-challenge.<hostname>`` so we can prove they
   control the apex.
2. Compute the CNAME target the operator's main record should point
   at — typically the cluster's ingress load-balancer hostname.
3. Decide whether the parent zone is platform-managed (a row in
   ``ManagedDomain``) — in which case the platform creates the
   records itself; otherwise the operator goes self-serve.

This module is pure (no DB writes, no driver calls) so it stays
testable + the mutation layer composes it. Persistence + driver
side-effects happen in the resolver / workflow.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import asdict, dataclass, field
from typing import Any

_TOKEN_ALPHABET = string.ascii_lowercase + string.digits


def generate_challenge_token(length: int = 32) -> str:
    """Random token for the TXT challenge record.

    Lowercase alnum so it survives every authoritative-DNS quoting +
    parser variant without escaping. 32 chars is ~160 bits of entropy,
    well over Let's Encrypt's recommended 128-bit minimum.
    """
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(length))


@dataclass
class RequiredRecord:
    """One row in ``CustomDomain.required_dns_records``.

    ``propagated`` flips True once the validation workflow sees the
    record at the authoritative nameserver. ``last_checked_at`` and
    ``message`` populate during validation; the initial handshake
    creates rows with both empty.
    """

    kind: str
    """Record type. ``CNAME`` / ``TXT`` / ``A`` / ``AAAA``."""

    name: str
    """Fully-qualified record name. Includes the trailing dot in the
    operator-facing copy because some BIND-flavored zone editors get
    confused without it."""

    value: str
    """Record RDATA — the value the operator pastes into their zone
    editor's "value" / "target" / "data" field."""

    ttl: int = 300
    propagated: bool = False
    last_checked_at: str | None = None
    message: str = ""
    """Operator-facing reason this record's last propagation check
    failed (or success copy when ``propagated=True``)."""


@dataclass
class Handshake:
    """The full set of records the operator must add + the platform-
    managed flag the UI uses to decide whether to render the
    "no action needed" variant."""

    hostname: str
    txt_challenge_token: str
    expected_cname_target: str
    required_records: list[RequiredRecord] = field(default_factory=list)
    is_platform_managed_zone: bool = False

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # Re-serialize records via per-instance asdict for stability —
        # dataclasses.asdict deep-copies but produces vanilla dicts.
        out["required_records"] = [asdict(r) for r in self.required_records]
        return out


# ---- pure helpers ----------------------------------------------------


def build_handshake(
    *,
    hostname: str,
    cluster_ingress_target: str,
    is_platform_managed_zone: bool,
    validation_method: str = "dns_txt",
    challenge_token: str | None = None,
) -> Handshake:
    """Compose the handshake payload for a freshly-added custom domain.

    ``cluster_ingress_target`` is the FQDN the operator's CNAME should
    point at — captured from the cluster's ingress load-balancer at
    handshake time so the operator sees a stable target even if the
    cluster's resolver shape changes later.

    Per ``validation_method``:
      ``dns_txt`` (default): operator adds a CNAME for the hostname
        + a TXT challenge at ``_astrolift-challenge.<hostname>``.
      ``dns_01``: same TXT challenge path, additional ACME challenge
        records get added later by the cert-issuance step (#397 PR 5).
      ``http_01``: only the CNAME is added; the cert issuer does
        HTTP-01 over the resolved address. We still emit the TXT row
        for ownership proof — HTTP-01 alone proves you control the
        port-80 server, not the apex.

    The returned Handshake is the in-memory shape; persistence is the
    caller's job.
    """
    token = challenge_token or generate_challenge_token()
    apex = hostname.lstrip(".")
    records: list[RequiredRecord] = [
        RequiredRecord(
            kind="CNAME",
            name=f"{apex}.",
            value=cluster_ingress_target.rstrip(".") + ".",
            ttl=300,
        ),
        RequiredRecord(
            kind="TXT",
            name=f"_astrolift-challenge.{apex}.",
            value=token,
            ttl=300,
        ),
    ]
    return Handshake(
        hostname=apex,
        txt_challenge_token=token,
        expected_cname_target=cluster_ingress_target,
        required_records=records,
        is_platform_managed_zone=is_platform_managed_zone,
    )


def resolve_cluster_ingress_target(
    *,
    cluster_slug: str,
    managed_domain_zone: str | None,
) -> str:
    """Pick the FQDN the operator's CNAME should point at.

    Priority:
      1. The cluster's ManagedDomain zone if one is bound — operators
         get ``<app>.<zone>`` as the target, which the platform's
         own DNS already resolves to the cluster ingress.
      2. A placeholder ``<cluster-slug>.ingress.astrolift.app``
         fallback when the cluster has no managed-domain binding —
         the operator's CNAME chase resolves through the platform's
         apex which is configured to point at the cluster's LB.

    This is intentionally a string-only computation so tests can
    exercise it without spinning up models / drivers.
    """
    if managed_domain_zone:
        return f"ingress.{managed_domain_zone}".rstrip(".")
    return f"{cluster_slug}.ingress.astrolift.app"


def hostname_parent_zone(hostname: str) -> str:
    """Return the parent zone of a fully-qualified hostname.

    ``api.acme.com`` → ``acme.com``
    ``acme.com`` → ``acme.com`` (apex is its own zone)
    ``api.staging.acme.com`` → ``staging.acme.com``

    Used to match the hostname against ``ManagedDomain.zone`` rows so
    the workflow can decide whether to take the platform-managed
    shortcut.
    """
    apex = hostname.strip(".").lower()
    parts = apex.split(".")
    if len(parts) <= 2:
        return apex
    return ".".join(parts[1:])
