"""Provider-console deep links for managed resources.

An exact provider id gets an exact resource page. Opaque legacy handles fall
back to the provider's resource search with the handle pre-filled; an empty
string is never presented as a working link.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode


def provider_portal_url(service) -> str:
    cluster = service.effective_cluster
    if cluster is None:
        return ""
    plugin = getattr(getattr(cluster, "provider_plugin", None), "slug", "") or ""
    handle = str(service.backend_ref or "").strip()
    region = str(getattr(cluster, "region", "") or "")

    if plugin == "azure":
        if handle.lower().startswith("/subscriptions/"):
            return f"https://portal.azure.com/#resource{quote(handle, safe='/')}/overview"
        if handle:
            return "https://portal.azure.com/#view/HubsExtension/BrowseAll/" + urlencode({"search": handle})
        return "https://portal.azure.com/#view/HubsExtension/BrowseAll"
    if plugin == "aws":
        query = urlencode({"region": region, "query": handle or service.name})
        return f"https://console.aws.amazon.com/resource-explorer/home?{query}#/search"
    if plugin == "gcp":
        provider_config = dict(getattr(cluster, "provider_config", None) or {})
        query = urlencode(
            {
                "project": provider_config.get("project_id", ""),
                "query": handle or service.name,
            }
        )
        return f"https://console.cloud.google.com/asset-inventory/assets?{query}"
    return ""


__all__ = ["provider_portal_url"]
