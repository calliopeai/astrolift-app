from __future__ import annotations

import copy
from dataclasses import asdict
from uuid import uuid4

import pytest
from nvke.client import NvkeError, NvkeResponse

from astrolift_clusters import nvke_diagnostics as diagnostics
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests import test_nvke_resource_operations as support
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_resource_operations import (
    prepared_spire,
    reserve,
    run,
)  # noqa: F401
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return support.world.__wrapped__()


@pytest.fixture
def configured(world):
    return support.configured.__wrapped__(world)


@pytest.fixture
def cluster_subresources(configured):
    yield from support.cluster_subresources.__wrapped__(configured)


def read_page(state, row, **kwargs):
    with context(state.world):
        return diagnostics.page(
            site_id=str(state.site.guid),
            operation_id=str(row.guid),
            expected_actor_id=state.world.user.pk,
            expected_version=row.version,
            **kwargs,
        )


def save_payload(row, data):
    operations.save_result(row, {"response": asdict(NvkeResponse(200, data))})
    row.save(update_fields=["result_backend_kind", "result_ciphertext", "updated_at", "version"])


def test_spire_projection_pages_all_rows_and_never_searches_private_messages(cluster_subresources):
    state, _, _, _, details, _ = cluster_subresources
    details["spire"]["nodes"] = [
        {
            "nodeID": str(uuid4()),
            "name": f"node-{index}",
            "reachable": index % 2 == 0,
            "hasValidSVID": False,
            "message": "PRIVATE_DIAGNOSTIC_MESSAGE",
            "serverAddress": "spire.test",
            "serverPort": 30440,
            "svidExpiresAt": "2026-11-07T12:00:00Z",
            "unknownSecret": "PRIVATE_NATIVE_DATA",
        }
        for index in range(205)
    ]
    row, _ = prepared_spire(cluster_subresources)
    _, summary, first, cursor, total = read_page(state, row, limit=200)
    assert summary["kind"] == "spire" and summary["requested_address"] == "spire.old.test"
    assert summary["cluster_id"] == cluster_subresources[3]
    assert len(first) == 200 and total == 205 and cursor
    _, _, last, after, total = read_page(state, row, limit=200, after=cursor)
    assert len(last) == 5 and not after and total == 205
    assert len({item["key"] for item in first + last}) == 205
    assert first[0]["reachable"] is True and first[1]["reachable"] is False
    assert first[0]["svid_expires_at"] == "2026-11-07T12:00:00+00:00"
    assert "PRIVATE" not in str(summary) + str(first)
    assert read_page(state, row, search="PRIVATE")[4] == 0
    assert read_page(state, row, search="node-204")[4] == 1


def test_control_plane_counters_are_exact_and_missing_values_are_not_zero(cluster_subresources):
    state, _, source, _, details, _ = cluster_subresources
    details["control_plane"] = {
        "config": {
            "configurationId": str(uuid4()),
            "desiredCount": 9007199254740993,
            "autoScaleDownDisabled": False,
            "machineAnnotations": {"private": "PRIVATE_NATIVE_DATA"},
        },
        "status": {
            "nodeCount": 9007199254740993,
            "kubernetesVersion": "v1.36.0",
            "controlPlaneUpgrading": {"status": "Unknown", "message": "PRIVATE_REASON"},
        },
    }
    row = reserve(state, operation_key="getClusterControlPlane", parameters={}, resource_source=source)
    assert run(row, "diagnostic-plane") == "succeeded"
    row.refresh_from_db()
    _, summary, rows, _, total = read_page(state, row)
    counts = {item["name"]: item["value"] for item in summary["counts"]}
    assert counts["desiredCount"] == counts["nodeCount"] == "9007199254740993"
    assert counts["readyNodeCount"] is None
    assert summary["conditions"][0]["value"] == "Unknown"
    assert summary["conditions"][1]["value"] is None
    assert summary["flags"][0]["value"] is False and summary["flags"][1]["value"] is None
    assert rows == [] and total == 0 and "PRIVATE" not in str(summary)


def test_history_exposes_known_field_names_without_encoded_values_or_authors(cluster_subresources):
    state, _, source, _, details, _ = cluster_subresources
    details["history"] = {
        "changes": [
            {
                "configurationID": str(uuid4()),
                "createdAt": "2026-11-07T12:00:00Z",
                "isCurrent": True,
                "createdBy": "PRIVATE_ACTOR",
                "changeReason": "PRIVATE_REASON",
                "changedFields": [
                    {"field": "kubernetesVersion", "from": "PRIVATE_OLD", "to": "PRIVATE_NEW"},
                    {"field": "unknown-secret-field", "to": "PRIVATE_VALUE"},
                ],
            }
        ]
    }
    row = reserve(
        state, operation_key="getClusterControlPlaneConfigHistory", parameters={}, resource_source=source
    )
    assert run(row, "diagnostic-history") == "succeeded"
    row.refresh_from_db()
    _, _, items, _, total = read_page(state, row)
    assert total == 1 and items[0]["is_current"] is True
    assert items[0]["changed_fields"] == ["kubernetesVersion"]
    assert items[0]["changed_field_count"] == "2" and items[0]["fields_omitted"] == "1"
    assert "PRIVATE" not in str(items)
    assert read_page(state, row, search="PRIVATE")[4] == 0


@pytest.mark.parametrize("change", ["actor", "version", "site", "status", "unbound"])
def test_diagnostics_refuse_an_unreviewed_or_incomplete_source(cluster_subresources, change):
    state, _, _, identity, _, _ = cluster_subresources
    row, _ = prepared_spire(cluster_subresources)
    kwargs = {
        "site_id": str(state.site.guid),
        "operation_id": str(row.guid),
        "expected_actor_id": state.world.user.pk,
        "expected_version": row.version,
    }
    if change == "actor":
        kwargs["expected_actor_id"] += 1
    elif change == "version":
        kwargs["expected_version"] += 1
    elif change == "site":
        kwargs["site_id"] = str(uuid4())
    elif change == "status":
        row.status = "failed"
        row.save(update_fields=["status", "updated_at", "version"])
        kwargs["expected_version"] = row.version
    else:
        row = reserve(
            state, operation_key="getClusterSpireServer", parameters={"path": {"clusterID": identity}}
        )
        assert run(row, "diagnostic-unbound") == "succeeded"
        row.refresh_from_db()
        kwargs.update(operation_id=str(row.guid), expected_version=row.version)
    with context(state.world), pytest.raises(NvkeError):
        diagnostics.page(**kwargs)


def test_diagnostic_cursors_bind_actor_source_version_search_and_page_size(cluster_subresources):
    state, _, _, _, details, _ = cluster_subresources
    details["spire"]["nodes"] *= 3
    row, _ = prepared_spire(cluster_subresources)
    cursor = read_page(state, row, limit=1)[3]
    for kwargs in ({"limit": 2}, {"limit": 1, "search": "node"}):
        with pytest.raises(NvkeError) as error:
            read_page(state, row, after=cursor, **kwargs)
        assert error.value.code == "INVALID_CURSOR"
    save_payload(row, copy.deepcopy(details["spire"]))
    with pytest.raises(NvkeError) as error:
        read_page(state, row, after=cursor, limit=1)
    assert error.value.code == "INVALID_CURSOR"


def test_diagnostic_projection_enforces_current_membership_before_decryption(
    cluster_subresources, monkeypatch
):
    state, _, _, _, _, _ = cluster_subresources
    row, _ = prepared_spire(cluster_subresources)
    state.world.member.soft_delete()
    monkeypatch.setattr(
        diagnostics.operations, "decode_result", lambda _: pytest.fail("Must authorize before decrypting")
    )
    with pytest.raises((NvkeSiteError, PermissionDenied)):
        read_page(state, row)


def test_graphql_diagnostic_page_is_typed_and_preserves_large_counters(cluster_subresources):
    state, _, source, _, details, _ = cluster_subresources
    details["control_plane"] = {"config": {"desiredCount": 9007199254740993}, "status": {}}
    row = reserve(state, operation_key="getClusterControlPlane", parameters={}, resource_source=source)
    assert run(row, "diagnostic-schema") == "succeeded"
    row.refresh_from_db()
    result = execute(
        state.world,
        """query($site:GUID!, $id:GUID!, $actor:Int!, $version:Int!) {
      astroliftNvkeDiagnosticPage(siteId:$site, operationId:$id, expectedActorId:$actor, expectedVersion:$version) {
        siteId operationId operationVersion summary {kind counts {name value}} items {key} totalCount
      }
    }""",
        {
            "site": str(state.site.guid),
            "id": str(row.guid),
            "actor": state.world.user.pk,
            "version": row.version,
        },
    )
    assert result.errors is None
    data = result.data["astroliftNvkeDiagnosticPage"]
    assert data["operationVersion"] == row.version
    assert data["summary"]["counts"][0]["value"] == "9007199254740993"


@pytest.mark.parametrize("change", ["types", "limit", "history-fields"])
def test_diagnostic_projection_handles_unreported_types_and_explicit_budgets(
    cluster_subresources, monkeypatch, change
):
    state, _, source, _, details, _ = cluster_subresources
    if change == "history-fields":
        details["history"] = {
            "changes": [
                {
                    "configurationID": str(uuid4()),
                    "createdAt": "2026-11-07T12:00:00Z",
                    "isCurrent": False,
                    "changedFields": [{"field": "release", "to": "PRIVATE_VALUE"}] * 100,
                }
            ]
        }
        row = reserve(
            state, operation_key="getClusterControlPlaneConfigHistory", parameters={}, resource_source=source
        )
        assert run(row, "diagnostic-cap") == "succeeded"
        row.refresh_from_db()
        item = read_page(state, row)[2][0]
        assert len(item["changed_fields"]) == 64 and item["fields_omitted"] == "36"
        assert item["changed_field_count"] == "100" and "PRIVATE" not in str(item)
    elif change == "limit":
        row, _ = prepared_spire(cluster_subresources)
        monkeypatch.setattr(diagnostics, "MAX_ROWS", 0)
        with pytest.raises(NvkeError) as error:
            read_page(state, row)
        assert error.value.code == "SNAPSHOT_UNAVAILABLE"
    else:
        details["spire"]["nodes"] = [
            {
                "nodeID": "PRIVATE_ID",
                "name": "PRIVATE NAME",
                "reachable": "false",
                "hasValidSVID": 0,
                "serverAddress": "https://user:PRIVATE_PASSWORD@example.test",
                "serverPort": True,
                "svidExpiresAt": "PRIVATE_DATE",
            }
        ]
        row, _ = prepared_spire(cluster_subresources)
        item = read_page(state, row)[2][0]
        assert all(
            item[key] is None
            for key in (
                "native_id",
                "name",
                "reachable",
                "has_valid_svid",
                "server_address",
                "server_port",
                "svid_expires_at",
            )
        )


def test_diagnostic_projection_masks_foreign_and_missing_sources_identically(
    cluster_subresources, monkeypatch
):
    state, _, _, _, _, _ = cluster_subresources
    row, _ = prepared_spire(cluster_subresources)
    foreign = support.configured.__wrapped__(support.world.__wrapped__())
    monkeypatch.setattr(
        diagnostics.operations, "decode_result", lambda _: pytest.fail("Must not decode a foreign source")
    )
    messages = []
    for operation_id in (str(row.guid), str(uuid4())):
        with context(foreign.world), pytest.raises(NvkeError) as error:
            diagnostics.page(
                site_id=str(state.site.guid),
                operation_id=operation_id,
                expected_actor_id=foreign.world.user.pk,
                expected_version=row.version,
            )
        messages.append((error.value.code, str(error.value)))
    assert messages[0] == messages[1]


def test_diagnostic_association_refuses_a_damaged_fixed_identity_binding(cluster_subresources):
    state, _, _, _, _, _ = cluster_subresources
    row, _ = prepared_spire(cluster_subresources)
    authority = copy.deepcopy(row.authority)
    authority["resource"]["identity_digest"] = "damaged"
    type(row).objects.filter(pk=row.pk).update(authority=authority)
    with pytest.raises(NvkeError) as error:
        read_page(state, row)
    assert error.value.code == "SNAPSHOT_UNAVAILABLE"
