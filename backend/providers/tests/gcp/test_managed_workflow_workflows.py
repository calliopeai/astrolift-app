from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.workflow_workflows import (
    WorkflowsConfig,
    WorkflowsDriver,
    WorkflowsError,
    WorkflowsNotFound,
    WorkflowsRestClient,
)


class FakeWorkflows:
    def __init__(self) -> None:
        self.workflows: dict[str, dict[str, Any]] = {}
        self.revisions: dict[str, list[dict[str, Any]]] = {}
        self.executions: dict[str, list[dict[str, Any]]] = {}
        self.created: list[tuple[str, str, dict[str, Any]]] = []
        self.patched: list[tuple[str, dict[str, Any], list[str]]] = []
        self.deleted: list[str] = []
        self.cancelled: list[str] = []
        self.started: list[tuple[str, dict[str, Any]]] = []

    def get_workflow(self, name: str, *, revision_id: str = "") -> dict[str, Any]:
        if revision_id:
            for revision in self.revisions.get(name, []):
                if revision.get("revisionId") == revision_id:
                    return deepcopy(revision)
            raise WorkflowsNotFound(f"{name}@{revision_id}")
        if name not in self.workflows:
            raise WorkflowsNotFound(name)
        return deepcopy(self.workflows[name])

    def create_workflow(self, parent: str, workflow_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = f"{parent}/workflows/{workflow_id}"
        self.created.append((parent, workflow_id, deepcopy(body)))
        current = {
            **deepcopy(body),
            "name": name,
            "state": "ACTIVE",
            "revisionId": "000001-a4d",
            "revisionCreateTime": "2026-08-14T12:00:00Z",
        }
        self.workflows[name] = current
        self.revisions[name] = [deepcopy(current)]
        return {"name": f"{parent}/operations/create", "done": True, "response": current}

    def patch_workflow(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self.patched.append((name, deepcopy(body), list(update_mask)))
        current = self.workflows[name]
        current.update(deepcopy(body))
        if {"sourceContents", "serviceAccount"}.intersection(update_mask):
            revisions = self.revisions.setdefault(name, [])
            ordinal = len(revisions) + 1
            current["revisionId"] = f"{ordinal:06d}-abc"
            current["revisionCreateTime"] = f"2026-08-14T12:0{ordinal}:00Z"
            revisions.append(deepcopy(current))
        return {"name": f"{name}/operations/patch", "done": True, "response": deepcopy(current)}

    def delete_workflow(self, name: str) -> dict[str, Any]:
        if name not in self.workflows:
            raise WorkflowsNotFound(name)
        self.deleted.append(name)
        del self.workflows[name]
        return {"name": f"{name}/operations/delete", "done": True}

    def list_revisions(self, name: str) -> list[dict[str, Any]]:
        return deepcopy(self.revisions.get(name, []))

    def list_executions(
        self,
        workflow_name: str,
        *,
        filter_expression: str = "",
        view: str = "BASIC",
    ) -> list[dict[str, Any]]:
        del filter_expression, view
        return deepcopy(self.executions.get(workflow_name, []))

    def create_execution(self, workflow_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.started.append((workflow_name, deepcopy(body)))
        return {
            **deepcopy(body),
            "name": f"{workflow_name}/executions/exec-1",
            "state": "ACTIVE",
        }

    def cancel_execution(self, name: str) -> dict[str, Any]:
        self.cancelled.append(name)
        return {"name": name, "state": "CANCELLED"}

    def get_operation(self, name: str) -> dict[str, Any]:
        return {"name": name, "done": True}


@pytest.fixture
def fake() -> FakeWorkflows:
    return FakeWorkflows()


@pytest.fixture
def driver(fake: FakeWorkflows) -> WorkflowsDriver:
    return WorkflowsDriver(
        config=WorkflowsConfig(
            project_id="acme-prod",
            region="us-central1",
            poll_interval_seconds=0,
        ),
        client=fake,
    )


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="payments",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="settle",
        size="small",
        config={"definition": {"main": {"steps": [{"done": {"return": "ok"}}]}}, **config},
        tags={"cost.center": "finance"},
        binding_id="binding-id",
        managed_service_id="service-id",
    )


def _name(workflow_id: str = "workflow-one") -> str:
    return f"projects/acme-prod/locations/us-central1/workflows/{workflow_id}"


def _owned_workflow(workflow_id: str = "workflow-one", **overrides: Any) -> dict[str, Any]:
    return {
        "name": _name(workflow_id),
        "sourceContents": '{"main":{"return":"ok"}}',
        "description": "",
        "serviceAccount": "",
        "callLogLevel": "LOG_ERRORS_ONLY",
        "executionHistoryLevel": "EXECUTION_HISTORY_BASIC",
        "userEnvVars": {},
        "cryptoKeyName": "",
        "labels": {
            "astrolift_io_managed_by": "platform",
            "astrolift_io_organization": "acme",
            "astrolift_io_app": "payments",
            "astrolift_io_environment": "prod",
            "astrolift_io_cluster": "cluster-id",
            "astrolift_io_resource_hint": "settle",
        },
        "state": "ACTIVE",
        "revisionId": "000001-a4d",
        **overrides,
    }


def test_provision_creates_workflow_with_defaults_and_ownership(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    result = driver.provision(
        spec(
            workflow_id="workflow-one",
            service_account="workflow@acme-prod.iam.gserviceaccount.com",
            user_env_vars={"MODE": "strict"},
            crypto_key_name=("projects/acme-prod/locations/us-central1/keyRings/platform/cryptoKeys/workflows"),
            resource_tags={"123/environment": "456/production"},
        ),
    )

    assert result.ok and result.ready
    assert result.handle == f"workflow_engine/{_name()}"
    parent, workflow_id, body = fake.created[0]
    assert parent == "projects/acme-prod/locations/us-central1"
    assert workflow_id == "workflow-one"
    assert body["callLogLevel"] == "LOG_ERRORS_ONLY"
    assert body["executionHistoryLevel"] == "EXECUTION_HISTORY_BASIC"
    assert body["serviceAccount"] == "workflow@acme-prod.iam.gserviceaccount.com"
    assert body["userEnvVars"] == {"MODE": "strict"}
    assert body["tags"] == {"123/environment": "456/production"}
    assert body["labels"]["astrolift_io_managed_by"] == "platform"
    assert body["labels"]["cost_center"] == "finance"
    assert body["labels"]["astrolift_io_binding"] == "binding-id"


def test_provision_is_idempotent_when_provider_state_matches(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    request = spec(workflow_id="workflow-one")
    first = driver.provision(request)
    fake.patched.clear()
    second = driver.provision(request)

    assert first.ok and second.ok
    assert fake.patched == []


def test_provision_patches_changed_revision_and_runtime_fields(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    original = spec(workflow_id="workflow-one")
    assert driver.provision(original).ok
    result = driver.provision(
        spec(
            workflow_id="workflow-one",
            definition={"main": {"return": "changed"}},
            description="Changed",
            call_log_level="LOG_NONE",
        ),
    )

    assert result.ok
    _, body, mask = fake.patched[-1]
    assert body["description"] == "Changed"
    assert body["callLogLevel"] == "LOG_NONE"
    assert "sourceContents" in mask
    assert fake.workflows[_name()]["revisionId"] == "000002-abc"


def test_existing_unowned_workflow_requires_explicit_adoption(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow(labels={"team": "legacy"})

    denied = driver.provision(spec(workflow_id="workflow-one"))
    adopted = driver.provision(spec(workflow_id="workflow-one", adopt_existing=True))

    assert not denied.ok and "adopt_existing=true" in denied.message
    assert adopted.ok
    assert fake.workflows[_name()]["labels"]["astrolift_io_adopted"] == "true"


def test_existing_managed_workflow_cannot_cross_boundary_without_reassignment(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    workflow = _owned_workflow()
    workflow["labels"]["astrolift_io_app"] = "another-app"
    fake.workflows[_name()] = workflow

    denied = driver.provision(spec(workflow_id="workflow-one"))
    reassigned = driver.provision(spec(workflow_id="workflow-one", allow_reassignment=True))

    assert not denied.ok and "another Astrolift boundary" in denied.message
    assert reassigned.ok
    labels = fake.workflows[_name()]["labels"]
    assert labels["astrolift_io_app"] == "payments"
    assert labels["astrolift_io_reassigned"] == "true"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"source_contents": "main:\n  return: ok"}, "exactly one"),
        ({"definition": ""}, "cannot be empty"),
        ({"definition": "x" * (128 * 1024 + 1)}, "128 KiB"),
        ({"workflow_id": "1bad"}, "workflow_id"),
        ({"call_log_level": "VERBOSE"}, "call_log_level"),
        ({"execution_history_level": "EVERYTHING"}, "execution_history_level"),
        ({"user_env_vars": {"GOOGLE_TOKEN": "no"}}, "reserved"),
        ({"labels": {"astrolift_io_app": "hijack"}}, "reserved"),
        ({"workflow": {"labels": {}}}, "cannot override"),
        ({"workflow": {"revisionId": "fake"}}, "cannot override"),
        ({"access_mode": "owner"}, "access_mode"),
    ],
)
def test_config_validation_rejects_unsafe_or_invalid_shapes(
    driver: WorkflowsDriver,
    config: dict[str, Any],
    message: str,
) -> None:
    base = {"definition": {"main": {"return": "ok"}}}
    if "source_contents" in config:
        base["source_contents"] = config["source_contents"]
    base.update(config)
    result = driver.provision(spec(**base))
    assert not result.ok
    assert message in result.message


def test_update_requires_owned_in_scope_workflow(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow(labels={})
    config = {"definition": {"main": {"return": "ok"}}}

    unowned = driver.update(UpdateSpec(handle=f"workflow_engine/{_name()}", config=config))
    wrong_project = driver.update(
        UpdateSpec(
            handle=("workflow_engine/projects/other/locations/us-central1/workflows/workflow-one"),
            config=config,
        ),
    )

    assert not unowned.ok and unowned.errors == ["resource_not_owned"]
    assert not wrong_project.ok and wrong_project.errors == ["invalid_handle"]


def test_update_preserves_ownership_and_replaces_user_labels(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    current = _owned_workflow(labels={**_owned_workflow()["labels"], "old": "label"})
    fake.workflows[_name()] = current
    fake.revisions[_name()] = [deepcopy(current)]

    result = driver.update(
        UpdateSpec(
            handle=f"workflow_engine/{_name()}",
            config={
                "definition": {"main": {"return": "ok"}},
                "labels": {"new": "label"},
            },
        ),
    )

    assert result.ok
    labels = fake.workflows[_name()]["labels"]
    assert labels["astrolift_io_app"] == "payments"
    assert labels["new"] == "label"
    assert "old" not in labels


def test_deprovision_enforces_protection_and_data_consent(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow()
    handle = f"workflow_engine/{_name()}"

    protected = driver.deprovision(DeprovisionSpec(handle, {}), delete_data=True)
    no_data_consent = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
    )
    deleted = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
        delete_data=True,
    )

    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert not no_data_consent.ok and no_data_consent.errors == ["delete_data_required"]
    assert deleted.ok and fake.deleted == [_name()]


def test_deprovision_blocks_active_executions_without_force(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow()
    fake.executions[_name()] = [
        {"name": f"{_name()}/executions/a", "state": "ACTIVE"},
        {"name": f"{_name()}/executions/b", "state": "QUEUED"},
        {"name": f"{_name()}/executions/c", "state": "SUCCEEDED"},
    ]
    handle = f"workflow_engine/{_name()}"

    blocked = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
        delete_data=True,
    )
    forced = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": True}),
        force_destroy=True,
    )

    assert not blocked.ok and blocked.errors == ["running_executions"]
    assert forced.ok
    assert fake.cancelled == [
        f"{_name()}/executions/a",
        f"{_name()}/executions/b",
    ]


def test_adopted_workflow_deletion_requires_second_opt_in(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    workflow = _owned_workflow()
    workflow["labels"]["astrolift_io_adopted"] = "true"
    fake.workflows[_name()] = workflow
    handle = f"workflow_engine/{_name()}"

    denied = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
        delete_data=True,
    )
    accepted = driver.deprovision(
        DeprovisionSpec(
            handle,
            {"deletion_protection": False, "delete_adopted_workflow": True},
        ),
        delete_data=True,
    )

    assert not denied.ok and denied.errors == ["adopted_resource_delete_requires_opt_in"]
    assert accepted.ok


@pytest.mark.parametrize(
    ("provider_state", "state"),
    [("ACTIVE", "available"), ("UNAVAILABLE", "error"), ("STATE_UNSPECIFIED", "updating")],
)
def test_status_maps_provider_states(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
    provider_state: str,
    state: str,
) -> None:
    fake.workflows[_name()] = _owned_workflow(state=provider_state)
    status = driver.status(ServiceHandle(f"workflow_engine/{_name()}"))
    assert status.state == state


def test_status_not_found_is_deprovisioned(driver: WorkflowsDriver) -> None:
    status = driver.status(ServiceHandle(f"workflow_engine/{_name()}"))
    assert status.state == "deprovisioned"


@pytest.mark.parametrize(
    ("access_mode", "role"),
    [
        ("invoke", "roles/workflows.invoker"),
        ("observe", "roles/workflows.viewer"),
        ("manage", "roles/workflows.editor"),
    ],
)
def test_binding_emits_portable_and_native_contract(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
    access_mode: str,
    role: str,
) -> None:
    fake.workflows[_name()] = _owned_workflow()
    binding = driver.binding(
        ServiceHandle(f"workflow_engine/{_name()}"),
        {"access_mode": access_mode},
    )

    assert binding.env_vars["WORKFLOW_ENGINE_ID"].literal == "workflow-one"
    assert binding.env_vars["WORKFLOW_ENGINE_ARN"].literal == _name()
    assert binding.env_vars["WORKFLOW_ENGINE_REGION"].literal == "us-central1"
    assert binding.env_vars["GCP_WORKFLOWS_EXECUTIONS_URL"].literal == (
        f"https://workflowexecutions.googleapis.com/v1/{_name()}/executions"
    )
    assert binding.iam_grants[0].actions == [role]


def test_binding_refuses_unowned_resource(driver: WorkflowsDriver, fake: FakeWorkflows) -> None:
    fake.workflows[_name()] = _owned_workflow(labels={})
    with pytest.raises(WorkflowsError, match="not owned"):
        driver.binding(ServiceHandle(f"workflow_engine/{_name()}"))


def test_snapshot_chooses_latest_numeric_revision(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow()
    fake.revisions[_name()] = [
        {"revisionId": "000002-fff", "revisionCreateTime": "2026-08-14T12:02:00Z"},
        {"revisionId": "000010-aaa", "revisionCreateTime": "2026-08-14T12:10:00Z"},
        {"revisionId": "000009-fff", "revisionCreateTime": "2026-08-14T12:09:00Z"},
    ]
    snapshot = driver.snapshot(ServiceHandle(f"workflow_engine/{_name()}"))
    assert snapshot.snapshot_id == f"{_name()}@000010-aaa"
    assert snapshot.created_at == "2026-08-14T12:10:00Z"


def test_snapshot_refuses_unowned_workflow(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow(labels={})
    with pytest.raises(WorkflowsError, match="not owned"):
        driver.snapshot(ServiceHandle(f"workflow_engine/{_name()}"))


def test_restore_reads_exact_revision_and_allows_target_security_overrides(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    source = _owned_workflow(
        sourceContents="main:\n  steps:\n    - done:\n        return: old",
        serviceAccount="source@acme-prod.iam.gserviceaccount.com",
        callLogLevel="LOG_ALL_CALLS",
        userEnvVars={"SOURCE": "true"},
        revisionId="000007-abc",
    )
    fake.revisions[_name()] = [source]

    target = spec(
        workflow_id="workflow-restored",
        service_account="target@acme-prod.iam.gserviceaccount.com",
    )
    result = driver.restore(
        snapshot=type(
            "Snapshot",
            (),
            {
                "snapshot_id": f"{_name()}@000007-abc",
                "handle": f"workflow_engine/{_name()}",
                "created_at": "2026-08-14T12:00:00Z",
            },
        )(),
        target=target,
    )

    assert result.ok
    body = fake.created[-1][2]
    assert body["sourceContents"] == source["sourceContents"]
    assert body["serviceAccount"] == "target@acme-prod.iam.gserviceaccount.com"
    assert body["callLogLevel"] == "LOG_ALL_CALLS"
    assert body["userEnvVars"] == {"SOURCE": "true"}


def test_restore_requires_opt_in_for_cross_project_revision(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    source_name = "projects/archive/locations/us-east1/workflows/source"
    fake.revisions[source_name] = [
        {
            "name": source_name,
            "sourceContents": "main:\n  return: restored",
            "revisionId": "000007-abc",
            "labels": {"astrolift_io_managed_by": "platform"},
        },
    ]
    snapshot = type(
        "Snapshot",
        (),
        {
            "snapshot_id": f"{source_name}@000007-abc",
            "handle": f"workflow_engine/{source_name}",
            "created_at": "2026-08-14T12:00:00Z",
        },
    )()

    denied = driver.restore(snapshot, spec(workflow_id="restored"))
    accepted = driver.restore(
        snapshot,
        spec(workflow_id="restored", allow_cross_project_snapshot=True),
    )

    assert not denied.ok and denied.errors == ["cross_project_snapshot_requires_opt_in"]
    assert accepted.ok


def test_restore_requires_opt_in_for_unowned_revision(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.revisions[_name()] = [
        {
            "name": _name(),
            "sourceContents": "main:\n  return: imported",
            "revisionId": "000008-def",
            "labels": {"team": "legacy"},
        },
    ]
    snapshot = type(
        "Snapshot",
        (),
        {
            "snapshot_id": f"{_name()}@000008-def",
            "handle": f"workflow_engine/{_name()}",
            "created_at": "2026-08-14T12:00:00Z",
        },
    )()

    denied = driver.restore(snapshot, spec(workflow_id="imported"))
    accepted = driver.restore(
        snapshot,
        spec(workflow_id="imported", allow_unowned_snapshot=True),
    )

    assert not denied.ok and denied.errors == ["unowned_snapshot_requires_opt_in"]
    assert accepted.ok


def test_start_execution_serializes_arguments_and_runtime_options(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow()
    result = driver.start_execution(
        ServiceHandle(f"workflow_engine/{_name()}"),
        argument={"ticket": "EMR-123"},
        labels={"request": "emr_123"},
        call_log_level="LOG_ERRORS_ONLY",
        execution_history_level="EXECUTION_HISTORY_DETAILED",
        disable_concurrency_buffering=True,
    )

    assert result["state"] == "ACTIVE"
    body = fake.started[0][1]
    assert body["argument"] == '{"ticket":"EMR-123"}'
    assert body["labels"] == {"request": "emr_123"}
    assert body["executionHistoryLevel"] == "EXECUTION_HISTORY_DETAILED"
    assert body["disableConcurrencyQuotaOverflowBuffering"] is True


def test_start_execution_rejects_large_argument_and_reserved_label(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow()
    handle = ServiceHandle(f"workflow_engine/{_name()}")
    with pytest.raises(WorkflowsError, match="32 KiB"):
        driver.start_execution(handle, argument="x" * (32 * 1024 + 1))
    with pytest.raises(WorkflowsError, match="reserved"):
        driver.start_execution(handle, labels={"astrolift_io_app": "hijack"})


def test_start_execution_refuses_unowned_workflow(
    driver: WorkflowsDriver,
    fake: FakeWorkflows,
) -> None:
    fake.workflows[_name()] = _owned_workflow(labels={})
    with pytest.raises(WorkflowsError, match="not owned"):
        driver.start_execution(ServiceHandle(f"workflow_engine/{_name()}"))


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}" if payload is not None else b""
        self.text = "provider error"

    def json(self) -> dict[str, Any]:
        return deepcopy(self._payload)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, deepcopy(kwargs)))
        return self.responses.pop(0)


def test_rest_client_uses_distinct_definition_and_execution_endpoints() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": _name()}),
            FakeResponse(200, {"executions": [{"name": f"{_name()}/executions/a"}]}),
        ],
    )
    client = WorkflowsRestClient(session=session)

    client.get_workflow(_name(), revision_id="000001-a4d")
    client.list_executions(_name(), filter_expression='state="ACTIVE"')

    assert session.calls[0] == (
        "GET",
        f"https://workflows.googleapis.com/v1/{_name()}",
        {"params": {"revisionId": "000001-a4d"}, "json": None, "timeout": 30},
    )
    assert session.calls[1][1] == (f"https://workflowexecutions.googleapis.com/v1/{_name()}/executions")
    assert session.calls[1][2]["params"]["filter"] == 'state="ACTIVE"'


def test_rest_client_paginates_and_maps_provider_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"workflows": [{"revisionId": "000001-aaa"}], "nextPageToken": "next"}),
            FakeResponse(200, {"workflows": [{"revisionId": "000002-bbb"}]}),
            FakeResponse(404, {"error": {"message": "gone"}}),
        ],
    )
    client = WorkflowsRestClient(session=session)

    assert [item["revisionId"] for item in client.list_revisions(_name())] == [
        "000001-aaa",
        "000002-bbb",
    ]
    with pytest.raises(WorkflowsNotFound):
        client.get_workflow(_name())
    assert session.calls[1][2]["params"]["pageToken"] == "next"
