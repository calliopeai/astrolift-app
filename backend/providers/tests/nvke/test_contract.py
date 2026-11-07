from __future__ import annotations

import re

import pytest

from nvke.contract import (
    _contract,
    controller_spec,
    operation,
    operations,
    resource_schema,
    resources,
    upstream_revision,
)
from scripts.nvke_coverage import OUTPUT, render


def test_catalog_includes_every_controller_operation_and_public_resource_verb() -> None:
    spec = controller_spec()
    expected = {
        value["operationId"]
        for item in spec["paths"].values()
        for method, value in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    controller = [op for op in operations() if op.surface == "controller"]
    assert {op.id for op in controller} == expected
    assert len(controller) == 21
    for surface, kinds in {
        "tenant": {"NVKECluster"},
        "operator": {"MachineImage", "ClusterRelease", "ClusterReleaseChannel", "ControlPlaneClass", "NodePoolRelease"},
    }.items():
        actual = [op for op in operations() if op.surface == surface]
        assert {op.resource for op in actual if op.resource} == kinds
        for kind in kinds:
            assert {op.method for op in actual if op.resource == kind} == {"GET", "POST", "PUT", "DELETE"}
            assert sum(op.resource == kind for op in actual) == 5
        assert {op.path for op in actual if not op.resource} == {"/healthz", "/readyz"}
    assert len({op.id for op in operations()}) == len(operations())
    assert len({(op.surface, op.method, op.path) for op in operations()}) == len(operations())


def test_controller_snapshot_is_not_mutable_shared_state() -> None:
    spec = controller_spec()
    spec["paths"].clear()
    assert controller_spec()["paths"]
    schema = resource_schema("NVKECluster")
    schema["properties"].clear()
    assert resource_schema("NVKECluster")["properties"]


def test_pinned_schema_references_cannot_fetch_external_documents() -> None:
    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(controller_spec())
    for resource in resources():
        walk(resource_schema(resource.kind))


def test_revision_and_source_evidence_are_pinned_and_cover_source_only_cli_commands() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", upstream_revision())
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in _contract()["sources"].values())
    declarations = _contract()["cli_source_declarations"]
    assert any(item["use"].startswith("deploy-path ") for item in declarations)
    assert any(item["use"] == "stack-config FILE" for item in declarations)
    assert any(item["use"] == "values FILE" for item in declarations)


def test_namespaced_resources_are_not_misclassified_as_site_wide() -> None:
    assert {r.kind for r in resources() if r.namespaced} == {"NVKECluster"}
    assert len(resources()) == 6
    assert all(r.group == "internal.nvke.nvidia.com" and r.version == "v1" for r in resources())


def test_compiled_cli_inventory_covers_commands_missing_from_upstream_reference() -> None:
    commands = {item["path"]: item for item in _contract()["cli_runtime"]}
    for path in ("nvkectl render deploy-path", "nvkectl inspect values", "nvkectl inspect nvkr-release"):
        assert commands[path]["executable"]
        assert f"`{path}`" in render()
    flags = {flag["name"]: flag for flag in commands["nvkectl bootstrap site"]["flags"]}
    assert {"sitespec", "disable-mtls", "timeout", "release", "otlp-endpoint", "site"} <= flags.keys()
    assert flags["sitespec"]["type"] == "string"
    assert flags["timeout"]["default"] == "30m0s"


def test_unknown_operation_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown NVKE operation"):
        operation("operator.rawProxy")


def test_coverage_is_current_and_does_not_claim_unverified_delivery() -> None:
    assert OUTPUT.read_text() == render()
    for op in operations():
        assert f"`{op.id}`" in OUTPUT.read_text()
    assert "This is a coverage inventory, not a support claim." in render()
