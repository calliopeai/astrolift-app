"""Actual worker bridge keeps private delivery outside persisted model state."""

import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from astrolift_identity.models import ApiToken, Member
from astrolift_services import local_model_artifacts as artifacts
from astrolift_services.tests.test_local_model_artifacts import (
    caller,
    configured,  # noqa: F401 -- actual private TLS SDK boundary
    import_uploaded,
    model_s3_wire,  # noqa: F401 -- fixture dependency
)
from astrolift_services.tests.test_shared_model_binding_runtime_2213 import runtime_world  # noqa: F401
from astrolift_workflows.activities import shared_model_reconcile as reconcile
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def local_worker(runtime_world, configured, monkeypatch):  # noqa: F811
    w = runtime_world
    user = make_user("delivery-workflow2266")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        user, permissions=[Permission.ORG_UPDATE], kind="ORG", scope_id=w.org.pk, slug="delivery-import2266"
    )
    token = ApiToken.objects.create(
        user=user, organization=w.org, token_hash=uuid4().hex, scopes=["admin"], name="Owned fixture"
    )
    owner = SimpleNamespace(user=user, org=w.org, token=token)
    with caller(owner):
        row = import_uploaded(owner, configured)
        row = artifacts.finalize_artifact(row.guid, row.version)
        local = artifacts.validate_artifact_request(w.org.pk, row.guid, row.version)
    w.model.config = {**w.model.config, **local}
    w.model.config.pop("model_revision")
    w.model.backend_ref = ""
    w.model.save()
    w.artifact, w.wire = row, configured
    monkeypatch.setattr(reconcile, "resolved_driver", lambda *_: (w.vllm, w.cfg))
    monkeypatch.setattr(
        "astrolift_workflows.activities.managed_service_lifecycle._run_managed_service_preflight",
        lambda *_: None,
    )
    return w


@pytest.mark.parametrize("action", ["provision", "update"])
def test_actual_apply_worker_hydrates_private_port_only_and_preserves_pending_readiness(
    local_worker, action, caplog
):
    w = local_worker
    old_config = dict(w.model.config)
    if action == "update":
        assert reconcile._apply_sync(w.model.pk, 1, "apply", False) == "applied"
    assert reconcile._apply_sync(w.model.pk, 1, "apply", False) == "applied"
    w.model.refresh_from_db()
    assert w.model.config == old_config
    assert w.model.applied_config is None and w.model.model_ready_observed_at is None
    assert w.model.status == "updating" and w.model.applied_subscription_revision == 0
    assert all(row.subscription_status == "pending" for row in w.model.attachments.all())
    assert not w.cfg.local_model_delivery  # Original resolved driver/config was not mutated.
    secret = next(
        row
        for (kind, _, _), row in w.driver.objects.items()
        if kind == "v1/Secret" and "delivery.json" in row.get("stringData", {})
    )
    plan = json.loads(secret["stringData"]["delivery.json"])
    assert plan["artifact_id"] == str(w.artifact.guid)
    assert plan["managed_service_id"] == str(w.model.guid)
    assert plan["organization_id"] == str(w.org.guid)
    assert all("versionId=" in file["url"] for file in plan["files"])
    public_state = json.dumps(
        [
            w.model.config,
            w.model.applied_config,
            w.model.status_error,
            w.secrets.data,
            repr(w.cfg),
            [record.__dict__ for record in caplog.records],
        ],
        default=str,
    )
    for file in plan["files"]:
        assert file["url"] not in public_state
    assert not any(row.subscription_status == "active" for row in w.model.attachments.all())


def test_actual_apply_worker_failed_delivery_rolls_back_handle_and_never_reports_readiness(
    local_worker, monkeypatch
):
    w = local_worker
    original = w.driver.apply_manifests

    def refused(cluster, namespace, manifests, **kwargs):
        # Exercise the actual rendering/private-plan bridge, then refuse the provider result.
        assert any("delivery.json" in row.get("stringData", {}) for row in manifests)
        original(cluster, namespace, manifests, **kwargs)
        return SimpleNamespace(ok=False, summary=lambda: ["https://private.invalid/synthetic-grant"])

    monkeypatch.setattr(w.driver, "apply_manifests", refused)
    with pytest.raises(ValueError, match="^Shared model apply was not confirmed.$"):
        reconcile._apply_sync(w.model.pk, 1, "apply", False)
    w.model.refresh_from_db()
    assert not w.model.backend_ref and w.model.applied_config is None
    assert w.model.model_ready_observed_at is None and w.model.status == "updating"
    assert not any(row.subscription_status == "active" for row in w.model.attachments.all())


async def test_actual_temporal_history_does_not_receive_private_delivery(temporal_env, local_worker):
    from asgiref.sync import sync_to_async
    from google.protobuf.json_format import Parse
    from temporalio.api.history.v1 import History

    from astrolift_workflows.tests.test_shared_model_reconcile_runtime_2213 import run_actual_workflow

    w = local_worker
    w.driver.converge = True
    result = await run_actual_workflow(temporal_env, w)
    assert result.ok
    await sync_to_async(w.model.refresh_from_db)()
    secret = next(
        row
        for (kind, _, _), row in w.driver.objects.items()
        if kind == "v1/Secret" and "delivery.json" in row.get("stringData", {})
    )
    plan = json.loads(secret["stringData"]["delivery.json"])
    # Inspect decoded protobuf Payload.data, rather than only base64 JSON rendering.
    raw_history = Parse(w.temporal_history, History()).SerializeToString()
    for file in plan["files"]:
        assert file["url"].encode() not in raw_history
    assert b"X-Amz-Signature" not in raw_history
    assert "url" not in json.dumps(w.model.config) + json.dumps(w.model.applied_config)


async def test_actual_temporal_failure_diagnostics_do_not_receive_private_delivery(
    temporal_env, local_worker, monkeypatch, caplog, capsys
):
    from asgiref.sync import sync_to_async
    from google.protobuf.json_format import Parse
    from temporalio.api.history.v1 import History

    from astrolift_workflows.tests.test_shared_model_reconcile_runtime_2213 import run_actual_workflow

    w = local_worker
    original = w.driver.apply_manifests
    rejected_urls = []

    def refused(cluster, namespace, manifests, **kwargs):
        secret = next(row for row in manifests if "delivery.json" in row.get("stringData", {}))
        urls = [file["url"] for file in json.loads(secret["stringData"]["delivery.json"])["files"]]
        rejected_urls.extend(urls)
        original(cluster, namespace, manifests, **kwargs)
        return SimpleNamespace(ok=False, summary=lambda: urls)

    monkeypatch.setattr(w.driver, "apply_manifests", refused)
    result = await run_actual_workflow(temporal_env, w)
    assert not result.ok and rejected_urls
    history = Parse(w.temporal_history, History())
    failures = [
        event.activity_task_failed_event_attributes
        for event in history.events
        if event.HasField("activity_task_failed_event_attributes")
    ]
    assert failures and failures[-1].failure.message
    raw_history = history.SerializeToString()
    output = capsys.readouterr()
    diagnostics = json.dumps(
        [
            result.message,
            [record.__dict__ for record in caplog.records],
            [logging.Formatter().format(record) for record in caplog.records],
            output.out,
            output.err,
        ],
        default=str,
    )
    for url in rejected_urls:
        assert url.encode() not in raw_history and url not in diagnostics
    assert b"X-Amz-Signature" not in raw_history and "X-Amz-Signature" not in diagnostics
    await sync_to_async(w.model.refresh_from_db)()
    assert w.model.status == "failed" and not w.model.backend_ref
    assert w.model.applied_config is None and w.model.model_ready_observed_at is None
    assert w.model.applied_subscription_revision == 0
    statuses = await sync_to_async(
        lambda: list(w.model.attachments.values_list("subscription_status", flat=True))
    )()
    assert statuses and set(statuses) == {"failed"}
