"""A Cloud Operations binding reads and writes only its own bundle's logs (#2099).

The binding granted ``roles/logging.viewer``, ``logging.logWriter``,
``monitoring.viewer`` and ``monitoring.metricWriter`` on the whole project, so in
a project several orgs share a bound workload read every tenant's logs and
metrics. Reads are now ``roles/logging.viewAccessor`` on the bundle bucket's
view, the bundle's entries route only into its bucket, and the workload
identity withdraws the project-wide roles an earlier binding left behind.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from astrolift_workflows.activities.workload_identity import _permissions_from_bindings

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from gcp.identity_wi import GCPWIConfig, GCPWorkloadIdentityDriver

from .test_identity_wi import FakeIAMClient, FakeProjectIAMClient, _AlreadyExists
from .test_managed_observability_cloud_operations import MSID, _driver, _spec

_PROJECT_WIDE = {
    "roles/logging.viewer",
    "roles/logging.logWriter",
    "roles/monitoring.viewer",
    "roles/monitoring.metricWriter",
    "roles/logging.viewAccessor",
}
_SA = "serviceAccount:app-identity@acme-prod.iam.gserviceaccount.com"


class FakeIAMWithViews(FakeProjectIAMClient):
    def __init__(self, bindings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(policy={"version": 1, "etag": "etag-1", "bindings": bindings or []})
        self.views: dict[str, dict[str, Any]] = {}
        self.view_set_calls: list[str] = []

    def get_log_view_iam_policy(self, *, view: str) -> dict[str, Any]:
        policy = self.views.get(view, {"etag": "view-etag", "bindings": []})
        return {**policy, "bindings": [dict(item) for item in policy["bindings"]]}

    def set_log_view_iam_policy(self, *, view: str, policy: dict[str, Any]) -> dict[str, Any]:
        self.views[view] = policy
        self.view_set_calls.append(view)
        return policy


def _identity(project_iam: FakeIAMWithViews) -> GCPWorkloadIdentityDriver:
    # The driver recognises an existing account by the exception's name.
    _AlreadyExists.__name__ = "AlreadyExists"
    return GCPWorkloadIdentityDriver(
        config=GCPWIConfig(project_id="acme-prod", iam_client=FakeIAMClient(), project_iam_client=project_iam),
    )


def _bound(config: dict[str, Any] | None = None, *, msid: str = MSID):
    driver, logging, _ = _driver()
    cfg = {"deletion_protection": False, **(config or {})}
    result = driver.provision(replace(_spec(cfg), managed_service_id=msid))
    assert result.ok, result.message
    handle = ServiceHandle(result.handle, managed_service_id=msid)
    return driver, logging, handle, cfg


def _project_roles_for(member: str, policy: dict[str, Any]) -> set[str]:
    return {binding["role"] for binding in policy["bindings"] if member in binding.get("members", [])}


@pytest.mark.parametrize("mode", ["read", "write", "both", "logs", "metrics", "none"])
def test_no_bundle_binding_grants_anything_on_the_project(mode: str) -> None:
    driver, _, handle, cfg = _bound({"access_mode": mode})

    binding = driver.binding(handle, {**cfg, "access_mode": mode})

    assert not [grant for grant in binding.iam_grants if grant.resource == "projects/acme-prod"]
    assert not {action for grant in binding.iam_grants for action in grant.actions} & (
        _PROJECT_WIDE - {"roles/logging.viewAccessor"}
    )
    bucket = binding.env_vars["GCP_LOG_BUCKET"].literal
    expected = (
        [(f"{bucket}/views/_AllLogs", ["roles/logging.viewAccessor"])] if mode in {"read", "both", "logs"} else []
    )
    assert [(grant.resource, grant.actions) for grant in binding.iam_grants] == expected


def test_two_bundles_in_one_project_read_only_their_own_view() -> None:
    first_driver, _, first, cfg = _bound()
    second_driver, _, second, _ = _bound(msid="managed-2")

    first_views = {grant.resource for grant in first_driver.binding(first, cfg).iam_grants}
    second_views = {grant.resource for grant in second_driver.binding(second, cfg).iam_grants}

    assert len(first_views) == len(second_views) == 1
    assert first_views != second_views


def test_a_bundle_without_a_bucket_is_granted_nothing() -> None:
    driver, logging, handle, cfg = _bound({"log_bucket": False})

    assert driver.binding(handle, cfg).iam_grants == []
    assert not logging.resources.get("log_sinks") and not logging.resources.get("log_exclusions")


def test_the_bundles_entries_route_only_into_its_bucket() -> None:
    _, logging, handle, _ = _bound()
    bundle = handle.handle.rsplit("/", 1)[-1]
    bucket = next(iter(logging.resources["log_bucket"]))
    entries = f'labels."astrolift_io_observability_bundle"="{bundle}"'

    sink = logging.resources["log_sinks"][f"projects/acme-prod/sinks/{bundle}.route"]
    exclusion = logging.resources["log_exclusions"][f"projects/acme-prod/exclusions/{bundle}.route"]

    assert sink["destination"] == f"logging.googleapis.com/{bucket}"
    assert sink["filter"] == exclusion["filter"] == entries
    create = next(call for call in logging.calls if call[0:2] == ("create", "log_sinks"))
    assert create[5] == {"uniqueWriterIdentity": "false"}


def test_a_bundle_from_before_2099_gains_its_route_on_the_next_reconcile() -> None:
    driver, logging, handle, cfg = _bound()
    logging.resources["log_sinks"].clear()
    logging.resources["log_exclusions"].clear()

    assert driver.update(UpdateSpec(handle=handle.handle, config=cfg, managed_service_id=MSID)).ok

    assert len(logging.resources["log_sinks"]) == len(logging.resources["log_exclusions"]) == 1


def test_a_tenant_cannot_declare_the_route_id() -> None:
    driver, _, handle, _ = _bound()
    bundle = handle.handle.rsplit("/", 1)[-1]

    result = driver.provision(
        _spec({"log_sinks": [{"id": f"{bundle}.route", "body": {"destination": "storage.googleapis.com/x"}}]}),
    )

    assert result.ok is False
    assert result.errors == ["invalid_cloud_operations_config"]


def test_deprovision_removes_the_route() -> None:
    driver, logging, handle, cfg = _bound()

    assert driver.deprovision(DeprovisionSpec(handle.handle, cfg, managed_service_id=MSID)).ok

    assert not logging.resources["log_sinks"] and not logging.resources["log_exclusions"]


def test_an_existing_binding_migrates_off_the_project_on_its_next_reconcile() -> None:
    """End to end: the bundle binding's grants, translated as the activity does,
    leave the identity holding no project-level logging or monitoring role."""
    driver, _, handle, cfg = _bound()
    other = "serviceAccount:someone-else@acme-prod.iam.gserviceaccount.com"
    project_iam = FakeIAMWithViews(
        [
            {"role": "roles/logging.viewer", "members": [_SA, other]},
            {"role": "roles/logging.logWriter", "members": [_SA]},
            {"role": "roles/monitoring.viewer", "members": [_SA]},
            {"role": "roles/monitoring.metricWriter", "members": [_SA]},
            {"role": "roles/storage.objectViewer", "members": [_SA]},
            {"role": "roles/logging.viewer", "members": [_SA], "condition": {"title": "operator"}},
        ],
    )
    identity = _identity(project_iam)
    permissions = _permissions_from_bindings([driver.binding(handle, cfg)], plugin_slug="gcp")

    identity.create_identity_role("app-identity", permissions)

    unconditional = {
        **project_iam.policy,
        "bindings": [item for item in project_iam.policy["bindings"] if not item.get("condition")],
    }
    assert not _project_roles_for(_SA, unconditional) & _PROJECT_WIDE
    assert _project_roles_for(_SA, unconditional) == {"roles/storage.objectViewer"}
    assert _project_roles_for(other, project_iam.policy) == {"roles/logging.viewer"}
    # A conditional binding is an operator's, not one this driver wrote.
    assert any(item.get("condition") for item in project_iam.policy["bindings"])
    view = permissions[0]["resource"]
    assert project_iam.views[view]["bindings"] == [{"role": "roles/logging.viewAccessor", "members": [_SA]}]

    sets = (len(project_iam.set_calls), len(project_iam.view_set_calls))
    identity.create_identity_role("app-identity", permissions)
    assert (len(project_iam.set_calls), len(project_iam.view_set_calls)) == sets


def test_a_role_another_binding_still_asks_for_is_kept() -> None:
    project_iam = FakeIAMWithViews([{"role": "roles/monitoring.viewer", "members": [_SA]}])

    _identity(project_iam).create_identity_role(
        "app-identity",
        [{"role": "roles/monitoring.viewer", "resource": "projects/acme-prod"}],
    )

    assert _project_roles_for(_SA, project_iam.policy) == {"roles/monitoring.viewer"}


@pytest.mark.parametrize(
    ("permission", "message"),
    [
        ({"role": "roles/logging.viewAccessor", "resource": "projects/acme-prod"}, "never on the project"),
        ({"role": "roles/logging.viewAccessor"}, "never on the project"),
        (
            {"role": "roles/logging.viewer", "resource": "projects/acme-prod/locations/global/buckets/b/views/v"},
            "grants only roles/logging.viewAccessor",
        ),
        (
            {"role": "roles/logging.viewAccessor", "resource": "projects/other/locations/global/buckets/b/views/v"},
            "not in project",
        ),
    ],
)
def test_a_log_view_reader_is_never_widened(permission: dict[str, Any], message: str) -> None:
    project_iam = FakeIAMWithViews()

    with pytest.raises(ValueError, match=message):
        _identity(project_iam).create_identity_role("app-identity", [permission])

    assert project_iam.set_calls == [] and project_iam.view_set_calls == []


def test_a_tenant_sink_or_log_metric_cannot_select_another_bundles_entries() -> None:
    _, logging, handle, _ = _bound(
        {
            "log_sinks": [
                {"id": "export", "body": {"destination": "bigquery.googleapis.com/projects/acme-prod/datasets/logs"}},
            ],
            "log_metrics": [{"id": "errors", "body": {"filter": "severity>=ERROR"}}],
        },
    )
    bundle = handle.handle.rsplit("/", 1)[-1]
    own = f'(NOT labels."astrolift_io_observability_bundle":* OR labels."astrolift_io_observability_bundle"="{bundle}")'

    assert logging.resources["log_sinks"]["projects/acme-prod/sinks/export"]["filter"] == own
    metric = logging.resources["log_metrics"]["projects/acme-prod/metrics/errors"]
    assert metric["filter"] == f"(severity>=ERROR) AND {own}"


def test_a_tenant_sink_cannot_write_into_another_bundles_bucket() -> None:
    _, logging, _, _ = _bound(msid="managed-2")
    other_bucket = next(iter(logging.resources["log_bucket"]))
    driver, _, _ = _driver()

    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "log_sinks": [
                    {
                        "id": "inject",
                        "unique_writer_identity": False,
                        "body": {"destination": f"logging.googleapis.com/{other_bucket}"},
                    },
                ],
            },
        ),
    )

    assert result.ok is False
    assert "bundle's own" in result.message
