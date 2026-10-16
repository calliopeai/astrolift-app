"""A bundle's tenant declarations select only its own bundle's entries (#2419).

After #2099 a tenant's sinks and log metrics could not select another bundle's
labelled entries, but they still selected every unlabelled entry in the
project: other tenants' unbound apps and the platform's own logs. Tenant
exclusions were not narrowed at all, so one bundle could drop another tenant's
entries from ``_Default``, and an alert policy's log-match condition read any
entry in the project into its notification. And the route trusted the pod
label from any GKE cluster in the project, whose admins can set any label.

Tenant sinks, log metrics, exclusions and log-match alert conditions now select
only the container entries of pods carrying the bundle's label, and every one
of them, the route included, is pinned to the bundle's cluster.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from _sdk.managed_service import ServiceHandle
from gcp.managed.observability_cloud_operations import (
    CloudOperationsConfig,
    CloudOperationsDriver,
    bundle_pod_labels,
)

from .test_bundle_log_view_2099 import LogRouter, _bucket_destination, gke_container_entry
from .test_managed_observability_cloud_operations import MSID, FakeCloudOperationsClient, _spec

_WAREHOUSE = "bigquery.googleapis.com/projects/acme-prod/datasets/tenant"


def _bound(
    config: dict[str, Any] | None = None,
    *,
    msid: str = MSID,
    cluster_name: str = "shared",
    logging: FakeCloudOperationsClient | None = None,
    monitoring: FakeCloudOperationsClient | None = None,
):
    logging = logging or FakeCloudOperationsClient()
    monitoring = monitoring or FakeCloudOperationsClient()
    driver = CloudOperationsDriver(
        config=CloudOperationsConfig(project_id="acme-prod", request_timeout_seconds=1, cluster_name=cluster_name),
        logging_client=logging,
        monitoring_client=monitoring,
    )
    cfg = {"deletion_protection": False, **(config or {})}
    result = driver.provision(replace(_spec(cfg), managed_service_id=msid))
    assert result.ok, result.message
    return driver, logging, monitoring, ServiceHandle(result.handle, managed_service_id=msid), cfg


def _platform_entry() -> dict[str, Any]:
    return {"resource": {"type": "gce_instance", "labels": {"instance_id": "1"}}, "labels": {}, "textPayload": "x"}


def test_a_tenant_sink_receives_no_unlabelled_entry() -> None:
    _, logging, _, handle, cfg = _bound({"log_sinks": [{"id": "everything", "body": {"destination": _WAREHOUSE}}]})
    router = LogRouter(logging)

    unbound_pod = gke_container_entry({"astrolift.dev/app": "someone-else"})
    own_pod = gke_container_entry(bundle_pod_labels(handle.handle, cfg))

    assert _WAREHOUSE not in router.destinations(unbound_pod)
    assert _WAREHOUSE not in router.destinations(_platform_entry())
    assert _WAREHOUSE in router.destinations(own_pod)


def test_a_tenant_exclusion_cannot_drop_another_tenants_entries_from_default() -> None:
    _, logging, _, _, _ = _bound(
        {"log_exclusions": [{"id": "drop-all", "body": {"filter": 'resource.type="k8s_container"'}}]},
    )
    router = LogRouter(logging)

    assert router.destinations(gke_container_entry({"astrolift.dev/app": "someone-else"})) == {"_Default"}
    assert router.destinations(_platform_entry()) == {"_Default"}


def test_a_tenant_log_metric_counts_only_its_own_entries() -> None:
    _, logging, _, handle, _ = _bound({"log_metrics": [{"id": "lines", "body": {}}]})
    bundle = handle.handle.rsplit("/", 1)[-1]

    metric = logging.resources["log_metrics"]["projects/acme-prod/metrics/lines"]

    assert f'="{bundle}"' in metric["filter"]
    assert 'resource.labels.cluster_name="shared"' in metric["filter"]
    assert "NOT " not in metric["filter"]


def test_the_route_ignores_a_pod_labelled_for_the_bundle_on_another_cluster() -> None:
    driver, logging, _, handle, cfg = _bound()
    router = LogRouter(logging)
    bucket = _bucket_destination(driver, handle, cfg)
    forged = gke_container_entry(bundle_pod_labels(handle.handle, cfg))
    forged["resource"]["labels"]["cluster_name"] = "other-tenant-cluster"

    assert router.destinations(gke_container_entry(bundle_pod_labels(handle.handle, cfg))) == {bucket}
    assert router.destinations(forged) == {"_Default"}


def test_a_tenant_sink_ignores_its_label_on_another_cluster() -> None:
    _, logging, _, handle, cfg = _bound({"log_sinks": [{"id": "everything", "body": {"destination": _WAREHOUSE}}]})
    forged = gke_container_entry(bundle_pod_labels(handle.handle, cfg))
    forged["resource"]["labels"]["cluster_name"] = "other-tenant-cluster"

    assert _WAREHOUSE not in LogRouter(logging).destinations(forged)


def test_a_log_match_alert_reads_only_the_bundles_entries() -> None:
    condition = {"displayName": "errors", "conditionMatchedLog": {"filter": "severity=ERROR"}}
    cfg = {"alert_policies": [{"id": "errors", "body": {"displayName": "Errors", "conditions": [condition]}}]}
    _, _, monitoring, handle, _ = _bound(cfg)
    bundle = handle.handle.rsplit("/", 1)[-1]

    alert = next(iter(monitoring.resources["alert_policies"].values()))
    confined = alert["conditions"][0]["conditionMatchedLog"]["filter"]

    assert confined.startswith("(\nseverity=ERROR\n)\nAND ")
    assert f'="{bundle}"' in confined and 'cluster_name="shared"' in confined
    # The declaration itself is left as the tenant wrote it.
    assert condition["conditionMatchedLog"]["filter"] == "severity=ERROR"


def test_a_trailing_comment_cannot_swallow_the_confinement() -> None:
    _, logging, _, _, _ = _bound(
        {"log_sinks": [{"id": "c", "body": {"destination": _WAREHOUSE, "filter": "severity=ERROR --"}}]},
    )

    sink = logging.resources["log_sinks"]["projects/acme-prod/sinks/c"]

    assert sink["filter"].startswith("(\nseverity=ERROR --\n)\nAND ")


@pytest.mark.parametrize("tenant_filter", ['a="x") OR (b="y"', 'a="x"))', '(a="x"', 'a="x")  -- ('])
def test_a_filter_that_could_close_the_wrapping_group_is_refused(tenant_filter: str) -> None:
    driver = CloudOperationsDriver(
        config=CloudOperationsConfig(project_id="acme-prod", request_timeout_seconds=1),
        logging_client=FakeCloudOperationsClient(),
        monitoring_client=FakeCloudOperationsClient(),
    )

    result = driver.provision(
        _spec({"log_sinks": [{"id": "s", "body": {"destination": _WAREHOUSE, "filter": tenant_filter}}]}),
    )

    assert result.ok is False
    assert "parentheses must balance" in result.message


def test_parentheses_inside_strings_and_comments_do_not_count() -> None:
    _, logging, _, _, _ = _bound(
        {
            "log_sinks": [
                {"id": "s", "body": {"destination": _WAREHOUSE, "filter": 'textPayload="a ) \\" ("  -- )\n'}},
            ],
        },
    )

    assert "projects/acme-prod/sinks/s" in logging.resources["log_sinks"]


def test_without_a_cluster_name_nothing_is_pinned() -> None:
    _, logging, _, _, _ = _bound(cluster_name="")

    assert all("cluster_name" not in str(sink["filter"]) for sink in logging.resources["log_sinks"].values())
