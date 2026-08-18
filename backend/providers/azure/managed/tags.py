"""Canonical ARM tags for Azure managed-service resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from _sdk.azure_tags import serialize_azure_arm_tags
from _sdk.managed_service_tags import PLATFORM_MANAGED_SERVICE_ID_KEY, canonical_key, ownership_key

if TYPE_CHECKING:
    from collections.abc import Mapping

    from _sdk.managed_service import ProvisionSpec

MANAGED_BY_TAG = ownership_key("azure", "managed_by")
MANAGED_SERVICE_ID_TAG = canonical_key("azure")
BINDING_TAG = ownership_key("azure", "binding")


def arm_tags_for(
    spec: ProvisionSpec,
    *,
    platform_tags: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """Build the ownership, billing, and custom ARM tag set for a spec."""

    canonical: dict[str, object] = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/org": spec.organization_slug,
        "astrolift.io/app": spec.app_slug,
        "astrolift.io/env": spec.environment_name,
        "astrolift.io/cluster": spec.tenant_cluster_id,
        "astrolift.io/isolation": spec.isolation,
    }
    if spec.binding_id:
        canonical["astrolift.io/binding"] = spec.binding_id
    if spec.managed_service_id:
        canonical[PLATFORM_MANAGED_SERVICE_ID_KEY] = spec.managed_service_id
    for key, value in (platform_tags or {}).items():
        canonical_key = str(key)
        if not canonical_key.startswith("astrolift.io/"):
            canonical_key = f"astrolift.io/{canonical_key}"
        canonical[canonical_key] = value
    return serialize_azure_arm_tags(canonical, custom_tags=spec.tags)
