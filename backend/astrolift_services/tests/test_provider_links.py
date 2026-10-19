from __future__ import annotations

from types import SimpleNamespace

from astrolift_services.provider_links import provider_portal_url


def _service(*, plugin="azure", backend_ref=""):
    cluster = SimpleNamespace(
        provider_plugin=SimpleNamespace(slug=plugin),
        provider_config={"subscription_id": "sub-123", "project_id": "gcp-project"},
        region="eastus",
    )
    return SimpleNamespace(
        guid="service-guid",
        kind="mssql",
        variant="azure_sql_database",
        name="records",
        config={"size": "small"},
        backend_ref=backend_ref,
        effective_cluster=cluster,
    )


def test_azure_arm_id_gets_an_exact_portal_resource_link():
    resource_id = "/subscriptions/sub-123/resourceGroups/rg/providers/Microsoft.Sql/servers/db"

    url = provider_portal_url(_service(backend_ref=resource_id))

    assert url.startswith("https://portal.azure.com/#resource/subscriptions/sub-123/")
    assert url.endswith("/overview")
