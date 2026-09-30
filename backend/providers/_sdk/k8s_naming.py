"""Deterministic, collision-resistant Kubernetes DNS-label naming.

Short canonical names remain byte-for-byte compatible with the historical
``"-".join(parts)`` convention. Names that require normalization or truncation
receive a stable hash suffix so two distinct long/unsafe inputs cannot collapse
onto the same Kubernetes object.
"""

from __future__ import annotations

import hashlib
import re

_UNSAFE = re.compile(r"[^a-z0-9-]+")
_DASH_RUN = re.compile(r"-+")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


def dns_label(*parts: object, max_length: int = 63, hash_length: int = 10) -> str:
    """Compose an RFC 1123 DNS label from ``parts``.

    A suffix is added only when normalization changes identity or the result is
    too long. This preserves deployed short names while making truncation and
    unsafe-character folding collision resistant.
    """
    if max_length < hash_length + 2 or max_length > 63:
        raise ValueError("max_length must leave room for a label and hash and cannot exceed 63")
    rendered_parts: list[str] = []
    for part in parts:
        if part is None:
            continue
        rendered = str(part).strip()
        if rendered:
            rendered_parts.append(rendered)
    raw = "-".join(rendered_parts)
    normalized = _DASH_RUN.sub("-", _UNSAFE.sub("-", raw.lower())).strip("-")
    if not normalized:
        raise ValueError("could not derive a Kubernetes DNS label")
    canonical_raw = raw.strip("-")
    changed_identity = normalized != canonical_raw
    if not changed_identity and len(normalized) <= max_length:
        return normalized

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:hash_length]
    prefix = normalized[: max_length - hash_length - 1].rstrip("-")
    if not prefix:
        prefix = "x"
    value = f"{prefix}-{digest}"
    if len(value) > max_length or _DNS_LABEL.fullmatch(value) is None:
        raise ValueError("could not derive a valid Kubernetes DNS label")
    return value


def app_namespace(*, organization_slug: str, app_slug: str) -> str:
    """Canonical per-app/project namespace, safe for maximum-length slugs."""
    return dns_label(organization_slug, app_slug)


def cluster_model_namespace(*, organization_id: str, cluster_id: str, managed_service_id: str) -> str:
    """One shared model's namespace, independent of app/project display names."""
    if not all((organization_id, cluster_id, managed_service_id)):
        raise ValueError("cluster model placement requires organization, cluster and managed service identities")
    return dns_label("astrolift-model", organization_id, cluster_id, managed_service_id)


def cluster_model_resource_name(managed_service_id: str) -> str:
    """Stable model resource identity; display name changes cannot retarget it."""
    if not managed_service_id:
        raise ValueError("cluster model resource requires a managed service identity")
    return dns_label("vllm", managed_service_id)


AGENT_NAMESPACE_PREFIX = "astrolift-agents"


def agent_namespace(organization_slug: str) -> str:
    """Canonical per-organization namespace for agent workloads.

    Organization slugs allow 200 characters and a Kubernetes namespace allows
    63, so the f-string this replaces produced an invalid namespace for a valid
    slug, breaking agent dispatch and any NetworkPolicy selecting on it (#1379).

    Every caller must derive the name the same way. The two that matter most
    are not the ones creating the namespace but the managed-service drivers
    building NetworkPolicy namespace selectors: a selector computed differently
    from the namespace it is meant to match silently selects nothing, which
    fails as a connectivity problem rather than a naming one.

    Byte-for-byte identical to the old ``f"astrolift-agents-{slug}"`` for every
    slug short enough to have produced a valid namespace, so nothing deployed
    needs migrating. Names only change where the old form could not have
    worked.
    """
    return dns_label(AGENT_NAMESPACE_PREFIX, organization_slug)
