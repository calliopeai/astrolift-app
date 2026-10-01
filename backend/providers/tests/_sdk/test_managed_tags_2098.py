"""Tenant custom tags stay outside provider ownership namespaces (#2098)."""

from __future__ import annotations

import pytest

from _sdk.managed_service import ProvisionSpec
from _sdk.managed_service_tags import read_managed_service_id
from aws.managed._base import tags_for
from azure.managed.filesystem_files import _tags as files_tags
from azure.managed.object_store_blob import _tags_for as blob_tags
from azure.managed.tags import arm_tags_for


@pytest.mark.parametrize(
    "key",
    [
        "astrolift.io/managed_service_id",
        "astrolift-managed-service-id",
        "astrolift_io_managed_service_id",
        "Astrolift.IO/Managed_Service_ID",
        "astrolift-service",
        "astrolift-managed-by",
        "astrolift-resource-parent",
    ],
)
def test_custom_ownership_spellings_cannot_replace_platform_tags(key: str) -> None:
    spec = ProvisionSpec(
        organization_id="org-guid",
        organization_slug="acme",
        app_id="app-guid",
        app_slug="api",
        environment_id="env-guid",
        environment_name="prod",
        tenant_cluster_id="cluster-guid",
        service_handle_hint="events",
        size="small",
        managed_service_id="service-guid",
        binding_id="binding-guid",
        tags={key: "foreign"},
    )
    aws = {item["Key"]: item["Value"] for item in tags_for(spec)}
    assert read_managed_service_id(aws, "aws") == "service-guid"
    assert aws["astrolift.io/managed-by"] == "platform"
    assert aws["astrolift.io/binding"] == "binding-guid"
    assert aws[f"astrolift.io/extra/{key}"] == "foreign"
    for tags in [arm_tags_for(spec), files_tags(spec), blob_tags(spec)]:
        assert read_managed_service_id(tags, "azure") == "service-guid"
        custom = {
            name: value for name, value in tags.items() if name.startswith(("astrolift-extra-", "astrolift_io_extra_"))
        }
        assert list(custom.values()) == ["foreign"]
        assert tags.get("astrolift-managed-by", tags.get("astrolift_io_managed_by")) == "platform"
