"""GCP label ownership check shared by the drivers (#1961)."""

from __future__ import annotations

import dataclasses

import pytest

from _sdk.managed_service import ProvisionSpec
from gcp.managed._ownership import label_adoption_refusal

_SPEC = ProvisionSpec(
    organization_id="1",
    organization_slug="acme-x",
    app_id="1",
    app_slug="api",
    environment_id="1",
    environment_name="prod",
    tenant_cluster_id="c",
    service_handle_hint="db",
    size="small",
)


@pytest.mark.parametrize(
    "labels",
    [
        {"astrolift-organization": "acme-x", "astrolift-app": "api"},
        {"astrolift-io-organization": "acme-x", "astrolift-io-app": "api"},
        {"astrolift_io_organization": "acme_x", "astrolift_io_app": "api"},
    ],
)
def test_every_label_spelling_of_this_org_and_app_is_adopted(labels):
    assert label_adoption_refusal(labels, _SPEC, resource="r") is None


def test_a_different_org_is_refused_even_when_separators_are_dropped():
    assert label_adoption_refusal({"astrolift-organization": "acmex", "astrolift-app": "api"}, _SPEC, resource="r")
    assert label_adoption_refusal({"astrolift-managed-by": "platform"}, _SPEC, resource="r")


@pytest.mark.parametrize(
    "key", ["astrolift_io_managed_service_id", "astrolift-managed-service-id", "astrolift-service"]
)
def test_the_service_id_label_decides_when_both_sides_have_one(key):
    spec = dataclasses.replace(_SPEC, managed_service_id="svc-a")
    assert label_adoption_refusal({key: "svc-a"}, spec, resource="r") is None
    assert "another managed service" in label_adoption_refusal({key: "svc-b"}, spec, resource="r")
