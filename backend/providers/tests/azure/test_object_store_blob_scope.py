"""The v1 blob driver scopes its grant to a real container (#1470).

`BlobStorageDriver.binding()` emitted this, literally:

    /subscriptions/SUB_ID/resourceGroups/RG/providers/Microsoft.Storage/...

A placeholder, because the v1 config carried no `subscription_id` or
`resource_group` to build a real id from — while `AzureBlobConfig`, the v2
config in the same file, has always had both. So every binding this driver
produced reported ready and carried a role assignment that could not be
applied, failing at assignment time far from the config that caused it.
"""

from __future__ import annotations

import pytest

from azure.managed.object_store_blob import BlobStorageConfig, BlobStorageDriver

SUBSCRIPTION = "00000000-1111-2222-3333-444444444444"


class _FakeClient:
    """Enough of a BlobServiceClient for the driver to construct."""

    def get_container_client(self, *_a, **_k):  # pragma: no cover - unused here
        raise AssertionError("binding() must not touch the network")


def _driver(**over):
    fields = {
        "storage_account": "astroliftprod",
        "subscription_id": SUBSCRIPTION,
        "resource_group": "rg-platform-prod",
        "blob_service_client": _FakeClient(),
    }
    fields.update(over)
    return BlobStorageDriver(config=BlobStorageConfig(**fields))


class _Handle:
    def __init__(self, handle: str) -> None:
        self.handle = handle


def test_the_grant_names_the_real_container():
    binding = _driver().binding(_Handle("astroliftprod/orders"))

    assert len(binding.iam_grants) == 1
    scope = binding.iam_grants[0].resource
    assert scope == (
        f"/subscriptions/{SUBSCRIPTION}"
        "/resourceGroups/rg-platform-prod"
        "/providers/Microsoft.Storage"
        "/storageAccounts/astroliftprod"
        "/blobServices/default/containers/orders"
    )
    assert "SUB_ID" not in scope and "/RG/" not in scope


@pytest.mark.parametrize("missing", ["subscription_id", "resource_group"])
def test_a_missing_half_refuses_rather_than_placeholding(missing):
    # The failure this replaces was silent: a ready binding whose grant
    # named a subscription that does not exist.
    driver = _driver(**{missing: ""})

    with pytest.raises(ValueError, match="scope its Storage Blob Data"):
        driver.binding(_Handle("astroliftprod/orders"))
