from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters import nvke_operator_jobs as jobs
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeOperation, NvkeSite
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_clusters.tests.test_nvke_sites import world as _world
from astrolift_identity.models import RoleBinding
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return _world.__wrapped__()


@pytest.fixture
def agent(tmp_path):
    path = Path(__file__).resolve().parents[2] / "providers/tests/nvke/test_operator_agent_client.py"
    spec = importlib.util.spec_from_file_location("nvke_operator_sdk_test_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield from module.real_operator_agent.__wrapped__(tmp_path)


def review(state):
    return {
        "site_id": state.site.guid,
        "expected_actor_id": state.world.user.pk,
        "expected_site_version": state.site.version,
        "expected_version": state.authority.version,
    }


@pytest.fixture
def configured(world, agent):
    site = NvkeSite.objects.create(
        guid=agent.site,
        upstream_revision=upstream_revision(),
        organization=world.org,
        name="Operator fixture site",
        slug="operator-site",
        organization_namespace="org-owned",
    )
    state = SimpleNamespace(world=world, site=site, agent=agent)
    credentials = {
        key: (agent.root / name).read_text()
        for key, name in (
            ("ca_pem", "ca.pem"),
            ("client_certificate_pem", "client.pem"),
            ("client_key_pem", "client.key"),
        )
    }
    with context(world):
        state.authority = authority.configure(
            site_id=site.guid,
            expected_actor_id=world.user.pk,
            expected_site_version=site.version,
            expected_version=0,
            endpoint=str(agent.client._http.base_url).rstrip("/"),
            instance_id=agent.instance,
            bundle_manifest_sha256=agent.digest,
            credentials=credentials,
        )
        site.refresh_from_db()
        state.authority = authority.verify(**review(state))
    return state


def request(state, **changes):
    values = {
        "format": 1,
        "revision": state.site.upstream_revision,
        "siteId": str(state.site.guid),
        "command": "generate.sitespec",
        "positionals": ["private-operator-site"],
        "options": {"nodes": "input:nodes.csv"},
        "files": {"nodes.csv": "name,ip\ncp-1,192.0.2.10\n"},
    }
    values.update(changes)
    return json.dumps(values)


def reserve(state, **changes):
    values = {
        "site_id": state.site.guid,
        "expected_actor_id": state.world.user.pk,
        "expected_site_version": state.site.version,
        "expected_authority_version": state.authority.version,
        "request_id": uuid4(),
        "request_text": request(state),
    }
    values.update(changes)
    with context(state.world):
        return jobs.reserve(**values)


def source_review(state, row):
    return {
        "operation_id": row.guid,
        "expected_actor_id": state.world.user.pk,
        "expected_site_version": state.site.version,
        "expected_authority_version": state.authority.version,
        "expected_operation_version": row.version,
    }


def test_authority_is_encrypted_verified_and_site_version_fenced(configured):
    state = configured
    row = state.authority
    assert row.verification_code == "AGENT_VERIFIED" and row.verified_at
    assert b"PRIVATE KEY" not in bytes(row.credential_ciphertext)
    original = bytes(row.credential_ciphertext)
    with context(state.world):
        rotated = authority.configure(
            **review(state),
            endpoint=row.endpoint,
            instance_id=str(row.instance_id),
            bundle_manifest_sha256=row.bundle_manifest_sha256,
        )
        assert bytes(rotated.credential_ciphertext) == original and rotated.verified_at is None
        with pytest.raises(sites.NvkeSiteError) as stale:
            authority.set_enabled(**review(state), enabled=False)
        assert stale.value.code == "VERSION_MISMATCH"
    state.site.refresh_from_db()
    with pytest.raises(NvkeError) as unverified:
        reserve(state, expected_authority_version=rotated.version)
    assert unverified.value.code == "SURFACE_UNAVAILABLE"


def test_original_request_is_private_idempotent_and_binds_all_review_inputs(configured):
    state = configured
    identifier = uuid4()
    row = reserve(state, request_id=identifier)
    assert b"private-operator-site" not in bytes(row.input_ciphertext)
    assert "private-operator-site" not in str(row.authority)
    assert reserve(state, request_id=identifier).pk == row.pk
    for changes in (
        {"timeout_seconds": 60},
        {"expected_site_version": 0},
        {"expected_authority_version": 0},
        {"request_text": request(state, positionals=["other-site"])},
    ):
        with pytest.raises(NvkeError) as conflict:
            reserve(state, request_id=identifier, **changes)
        assert conflict.value.code == "CONFLICT"
    assert not list((state.agent.root / "state/jobs").iterdir())


@pytest.mark.parametrize(
    "changes",
    [
        {"request_text": "PRIVATE_INVALID_JSON"},
        {"request_id": "invalid"},
        {"timeout_seconds": True},
        {"expected_actor_id": -1},
    ],
)
def test_invalid_or_changed_actor_cannot_admit_a_native_process(configured, changes):
    with pytest.raises((NvkeError, sites.NvkeSiteError)) as refused:
        reserve(configured, **changes)
    assert "PRIVATE" not in str(refused.value)
    assert not NvkeOperation.objects.exists()
    assert not list((configured.agent.root / "state/jobs").iterdir())


def test_revoked_member_cannot_verify_or_admit(configured):
    state = configured
    state.world.member.soft_delete()
    with pytest.raises(PermissionDenied):
        reserve(state)
    with context(state.world), pytest.raises(PermissionDenied):
        authority.verify(**review(state))
    assert not NvkeOperation.objects.exists()


def test_disabled_authority_revokes_original_attempt_before_any_agent_job(configured):
    state = configured
    row = reserve(state)
    with context(state.world):
        authority.set_enabled(**review(state), enabled=False)
    assert (
        runtime.attempt(
            row.pk, row.generation, execution=(row.workflow_id, "revoked-agent", "NvkeOperationWorkflow")
        )
        == "failed"
    )
    row.refresh_from_db()
    assert row.error_code == "SOURCE_CHANGED"
    assert not list((state.agent.root / "state/jobs").iterdir())


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("lose_reply", [False, True])
async def test_real_temporal_postgres_and_tls_agent_complete_one_private_native_job(
    configured, temporal_env, monkeypatch, lose_reply
):
    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
    from core.testing.temporal import temporal_worker

    state = configured
    queue = "nvke-operator-" + uuid4().hex

    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(workflow_client, "_task_queue", lambda: queue)
    if lose_reply:
        original = OperatorAgentClient.submit

        def lost(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise NvkeError("OPERATOR_UNCONFIRMED", "PRIVATE_LOST_REPLY", ambiguous=True)

        monkeypatch.setattr(OperatorAgentClient, "submit", lost)
    request_id = uuid4()
    async with temporal_worker(
        temporal_env, task_queue=queue, workflows=[NvkeOperationWorkflow], activities=[nvke_operation_attempt]
    ):
        row = await sync_to_async(reserve)(state, request_id=request_id)
        handle = temporal_env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id or None)
        assert await asyncio.wait_for(handle.result(), timeout=65) == "succeeded"
        await sync_to_async(row.refresh_from_db)()
        result = await sync_to_async(outbox.decode_result)(row)
        assert result["operator_job"]["intent"] == row.request_digest
        assert result["operator_job"]["state"] == "completed"
        assert b"private-operator-site" not in bytes(row.result_ciphertext)
        repeated = await sync_to_async(reserve)(state, request_id=request_id)
        assert repeated.pk == row.pk
        assert len(list((state.agent.root / "state/workspace/runs").glob("*/started.json"))) == 1
        history = (await handle.fetch_history()).to_json()
        for private in (
            "private-operator-site",
            "nodes.csv",
            "PRIVATE_LOST_REPLY",
            "PRIVATE KEY",
            row.request_digest,
        ):
            assert private not in history

        def download():
            with context(state.world):
                return jobs.artifact(**source_review(state, row), name="stdout")

        artifact = await sync_to_async(download)()
        assert b"private-operator-site" in base64.b64decode(artifact["dataBase64"], validate=True)

        def revoke_and_refuse():
            RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
            with pytest.raises(PermissionDenied):
                download()

        await sync_to_async(revoke_and_refuse)()

        def recover_as_current_admin():
            from django.contrib.auth import get_user_model

            from astrolift_identity.models import Member
            from core.permissions import Permission
            from core.tests.utils.scope_world import bind_role

            caller = get_user_model().objects.create(
                username="operator-review-" + uuid4().hex, is_active=True
            )
            Member.objects.create(user=caller, scope_kind="ORG", scope_id=state.world.org.pk)
            bind_role(
                caller,
                permissions=[Permission.NVKE_ADMIN, Permission.NVKE_READ],
                kind="ORG",
                scope_id=state.world.org.pk,
                slug="operator-review-" + uuid4().hex,
            )
            row.status = "uncertain"
            row.save(update_fields=["status", "version"])
            reviewed = source_review(state, row)
            reviewed["expected_actor_id"] = caller.pk
            with context(SimpleNamespace(org=state.world.org, user=caller)):
                recovered = jobs.reconcile(**reviewed)
            assert recovered.status == "succeeded" and recovered.updated_by_id == caller.pk
            assert len(list((state.agent.root / "state/workspace/runs").glob("*/started.json"))) == 1

        await sync_to_async(recover_as_current_admin)()


def test_schema_exposes_full_catalog_and_masks_aliased_private_request(configured):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    state = configured
    catalog = execute(
        state.world,
        "{astroliftNvkeOperatorContract{revision commands{key options{name kind required choices}}}}",
    )
    assert catalog.errors is None
    commands = catalog.data["astroliftNvkeOperatorContract"]["commands"]
    assert len(commands) == 17 and any(item["key"] == "bootstrap.site" for item in commands)
    variables = {
        "unrelated": {
            "siteId": str(state.site.guid),
            "expectedActorId": state.world.user.pk,
            "expectedSiteVersion": state.site.version,
            "expectedAuthorityVersion": state.authority.version,
            "requestId": str(uuid4()),
            "requestText": request(state),
        }
    }
    query = """mutation($unrelated:RequestNvkeOperatorJobInput!){requestNvkeOperatorJob(input:$unrelated){ok errors{code} data{id status operationKey}}}"""
    result = execute(state.world, query, variables)
    assert result.errors is None and result.data["requestNvkeOperatorJob"]["ok"]
    row = MutationAuditLog.objects.filter(operation="nvke.operator_request").latest("timestamp")
    assert row.variables["unrelated"]["requestText"] == "***REDACTED***"
    assert "private-operator-site" not in str(row.variables)


def test_schema_masks_rejected_operator_endpoint_and_client_key(configured, caplog):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    state = configured
    query = """mutation($innocent:ConfigureNvkeOperatorAuthorityInput!){configureNvkeOperatorAuthority(input:$innocent){ok errors{code message}}}"""
    variables = {
        "innocent": {
            "siteId": str(state.site.guid),
            "expectedActorId": state.world.user.pk,
            "expectedSiteVersion": state.site.version,
            "expectedVersion": state.authority.version,
            "endpoint": "https://PRIVATE_USER:PRIVATE_PASSWORD@agent.invalid",
            "instanceId": state.agent.instance,
            "bundleManifestSha256": state.agent.digest,
            "credentials": {"clientCertificatePem": "PRIVATE_CERTIFICATE", "clientKeyPem": "PRIVATE_KEY"},
        }
    }
    result = execute(state.world, query, variables)
    assert result.errors is None and not result.data["configureNvkeOperatorAuthority"]["ok"]
    row = MutationAuditLog.objects.filter(operation="nvke.operator_configure").latest("timestamp")
    assert "PRIVATE" not in str(row.variables) + str(result.data) + caplog.text
    assert row.variables["innocent"]["endpoint"] == "***REDACTED***"
    assert row.variables["innocent"]["credentials"] == "***REDACTED***"


def test_live_and_uncertain_infrastructure_intents_fence_new_bootstrap(configured):
    state = configured
    effect = request(
        state,
        command="bootstrap.site",
        positionals=[],
        options={"sitespec": "input:site.yaml"},
        files={"site.yaml": "PRIVATE_SITE_CONFIG"},
    )
    with pytest.raises(NvkeError) as unreviewed:
        reserve(state, request_text=effect)
    assert unreviewed.value.code == "PRECONDITION"
    row = reserve(state, request_text=effect, acknowledge_infrastructure_change=True)
    for status in ("queued", "uncertain"):
        row.status = status
        row.save(update_fields=["status", "version"])
        with pytest.raises(NvkeError) as busy:
            reserve(state, request_text=effect, acknowledge_infrastructure_change=True)
        assert busy.value.code == "RESOURCE_BUSY"
    row.status = "succeeded"
    row.save(update_fields=["status", "version"])
    with pytest.raises(NvkeError) as duplicate:
        reserve(state, request_text=effect, acknowledge_infrastructure_change=True)
    assert duplicate.value.code == "PRECONDITION"
    assert not list((state.agent.root / "state/jobs").iterdir())


def test_reviewed_absence_reconciliation_never_submits_the_missing_original(configured):
    state = configured
    row = reserve(state)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    with context(state.world):
        recovered = jobs.reconcile(**source_review(state, row))
    assert recovered.status == "uncertain" and recovered.error_code == "OPERATOR_RECEIPT_NOT_FOUND"
    assert not list((state.agent.root / "state/jobs").iterdir())
    late = state.agent.client.submit(str(row.guid), outbox.decode_input(row), intent=row.request_digest)
    assert late["id"] == str(row.guid)


def test_token_identity_is_part_of_the_original_reviewed_intent(configured):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    state = configured
    identifier = uuid4()
    for _index in range(2):
        issued = mint_token()
        token = ApiToken.objects.create(
            user=state.world.user,
            organization=state.world.org,
            name="operator synthetic caller",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=["admin", "read"],
        )
        mark = set_current_api_token(token)
        try:
            if _index == 0:
                row = reserve(state, request_id=identifier)
                assert row.authority["token_id"] == str(token.guid)
            else:
                with pytest.raises(NvkeError) as conflict:
                    reserve(state, request_id=identifier)
                assert conflict.value.code == "CONFLICT"
        finally:
            reset_current_api_token(mark)
    assert NvkeOperation.objects.count() == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"endpoint": "https://PRIVATE_USER:PRIVATE_PASSWORD@agent.invalid"},
        {"endpoint": "https://agent.invalid/" + "x" * 2048},
        {"instance_id": "PRIVATE_INVALID_INSTANCE"},
        {"timeout_seconds": True},
        {"credentials": {"token": "PRIVATE_TOKEN"}},
    ],
)
def test_invalid_agent_authority_is_refused_without_storage_changes(configured, changed):
    state = configured
    values = {
        **review(state),
        "endpoint": state.authority.endpoint,
        "instance_id": str(state.authority.instance_id),
        "bundle_manifest_sha256": state.agent.digest,
        **changed,
    }
    with context(state.world), pytest.raises(NvkeError) as refused:
        authority.configure(**values)
    assert refused.value.code == "INVALID_INPUT" and "PRIVATE" not in str(refused.value)
    state.authority.refresh_from_db()
    assert state.authority.verified_at and state.authority.verification_code == "AGENT_VERIFIED"


def test_reviewed_artifact_pages_observe_one_native_job_with_bound_cursors(configured):
    import time

    state = configured
    row = reserve(state)
    reviewed = source_review(state, row)
    with context(state.world):
        absent = jobs.artifact_page(**reviewed)
    assert not absent["found"] and absent["state"] == "not_found" and absent["items"] == []
    state.agent.client.submit(str(row.guid), outbox.decode_input(row), intent=row.request_digest)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        receipt = state.agent.client.observe(
            str(row.guid), command="generate.sitespec", intent=row.request_digest
        )
        if receipt["state"] == "completed":
            break
        time.sleep(0.02)
    assert receipt["state"] == "completed"
    with context(state.world):
        first = jobs.artifact_page(**reviewed, limit=1)
        assert first["found"] and first["command"] == "generate.sitespec"
        assert first["instance_id"] == state.agent.instance
        assert first["next_cursor"] and first["total_count"] > 1
        names = [first["items"][0]["name"]]
        cursor = first["next_cursor"]
        while cursor:
            page = jobs.artifact_page(**reviewed, after=cursor, limit=1)
            assert page["receipt_sha256"] == first["receipt_sha256"]
            names.extend(item["name"] for item in page["items"])
            cursor = page["next_cursor"]
        assert names == sorted(item["name"] for item in receipt["artifacts"])
        filtered = jobs.artifact_page(**reviewed, search="stdout")
        assert all("stdout" in item["name"] for item in filtered["items"])
        for changes in ({"search": "different"}, {"limit": 2}, {"after": first["next_cursor"] + "x"}):
            kwargs = {"after": first["next_cursor"], "limit": 1, **changes}
            with pytest.raises(NvkeError) as invalid:
                jobs.artifact_page(**reviewed, **kwargs)
            assert invalid.value.code == "INVALID_CURSOR"
        row.status = "uncertain"
        row.save(update_fields=["status", "version"])
        with pytest.raises(NvkeError) as stale:
            jobs.artifact_page(**source_review(state, row), after=first["next_cursor"], limit=1)
        assert stale.value.code == "INVALID_CURSOR"
    assert len(list((state.agent.root / "state/workspace/runs").glob("*/started.json"))) == 1
    serialized = json.dumps(first)
    for private in (row.request_digest, "private-operator-site", "nodes.csv", "PRIVATE KEY"):
        assert private not in serialized


def test_operator_job_page_is_typed_private_and_does_not_admit_a_job(configured):
    from astrolift_clusters.tests.test_nvke_schema import execute

    state = configured
    row = reserve(state)
    query = """query($input:NvkeOperatorJobReviewInput!,$search:String){astroliftNvkeOperatorJobPage(input:$input,search:$search){ok errors{code} data{siteId siteVersion operationId operationVersion authorityId authorityVersion instanceId bundleManifestSha256 nativeRevision command found state effectful receiptSha256 items{name size sha256} nextCursor totalCount}}}"""
    variables = {
        "input": {
            "operationId": str(row.guid),
            "expectedActorId": state.world.user.pk,
            "expectedSiteVersion": state.site.version,
            "expectedAuthorityVersion": state.authority.version,
            "expectedOperationVersion": row.version,
        }
    }
    variables["search"] = None
    result = execute(state.world, query, variables)
    assert result.errors is None
    page = result.data["astroliftNvkeOperatorJobPage"]
    assert page["ok"] and page["data"]["operationId"] == str(row.guid)
    assert page["data"]["state"] == "not_found" and page["data"]["totalCount"] == 0
    assert row.request_digest not in json.dumps(page)
    assert not list((state.agent.root / "state/jobs").iterdir())
    variables["input"]["expectedActorId"] += 1
    changed = execute(state.world, query, variables)
    assert changed.errors is None and not changed.data["astroliftNvkeOperatorJobPage"]["ok"]
    RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
    variables["input"]["expectedActorId"] = state.world.user.pk
    revoked = execute(state.world, query, variables)
    assert revoked.errors is None and not revoked.data["astroliftNvkeOperatorJobPage"]["ok"]


@pytest.mark.parametrize(
    "changes", [{"limit": 0}, {"limit": True}, {"limit": 101}, {"search": "x" * 201}, {"search": None}]
)
def test_operator_artifact_page_refuses_unbounded_queries(configured, changes):
    state = configured
    row = reserve(state)
    with context(state.world), pytest.raises(NvkeError) as invalid:
        jobs.artifact_page(**source_review(state, row), **changes)
    assert invalid.value.code == "INVALID_INPUT"
    assert not list((state.agent.root / "state/jobs").iterdir())


def test_operator_artifact_page_refuses_changed_native_effect_classification(configured, monkeypatch):
    state = configured
    row = reserve(state)
    monkeypatch.setattr(OperatorAgentClient, "observe", lambda *args, **kwargs: {"effectful": True})
    with context(state.world), pytest.raises(NvkeError) as invalid:
        jobs.artifact_page(**source_review(state, row))
    assert invalid.value.code == "OPERATOR_INVALID_RESPONSE"
    assert not list((state.agent.root / "state/jobs").iterdir())


@pytest.mark.parametrize("code", [2**31, -(2**31) - 1])
def test_operator_artifact_page_refuses_unrepresentable_exit_code(configured, monkeypatch, code):
    state = configured
    row = reserve(state)
    monkeypatch.setattr(
        OperatorAgentClient, "observe", lambda *args, **kwargs: {"effectful": False, "nativeExitCode": code}
    )
    with context(state.world), pytest.raises(NvkeError) as invalid:
        jobs.artifact_page(**source_review(state, row))
    assert invalid.value.code == "OPERATOR_INVALID_RESPONSE"
    assert not list((state.agent.root / "state/jobs").iterdir())


def test_missing_agent_receipt_keeps_unknown_infrastructure_intent_fenced(configured):
    state = configured
    effect = request(
        state,
        command="bootstrap.site",
        positionals=[],
        options={"sitespec": "input:site.yaml"},
        files={"site.yaml": "not executed"},
    )
    row = reserve(state, request_text=effect, acknowledge_infrastructure_change=True)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    with context(state.world):
        recovered = jobs.reconcile(**source_review(state, row))
    assert recovered.status == "uncertain"
    with pytest.raises(NvkeError) as fenced:
        reserve(state, request_text=effect, acknowledge_infrastructure_change=True)
    assert fenced.value.code == "RESOURCE_BUSY"
    assert not list((state.agent.root / "state/jobs").iterdir())


def test_reviewed_stop_fences_absent_original_and_releases_infrastructure_intent(configured):
    state = configured
    effect = request(
        state,
        command="bootstrap.site",
        positionals=[],
        options={"sitespec": "input:site.yaml"},
        files={"site.yaml": "not executed"},
    )
    row = reserve(state, request_text=effect, acknowledge_infrastructure_change=True)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    with context(state.world):
        stopped = jobs.stop(**source_review(state, row))
    assert stopped.status == "cancelled"
    assert outbox.decode_result(stopped)["operator_job"]["errorCode"] == "ORIGINAL_JOB_FENCED"
    with pytest.raises(NvkeError) as delayed:
        state.agent.client.submit(str(row.guid), json.loads(effect), intent=row.request_digest)
    assert delayed.value.code == "OPERATOR_JOB_FENCED"
    replacement = reserve(state, request_text=effect, acknowledge_infrastructure_change=True)
    assert replacement.guid != row.guid
    assert not list((state.agent.root / "state/jobs").iterdir())
    assert not (state.agent.root / "state/workspace/runs").exists()


def test_unconfirmed_stop_recovers_original_fence_through_read_only_reconciliation(configured, monkeypatch):
    state = configured
    row = reserve(state)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    original_version = row.version
    original = OperatorAgentClient.stop_or_fence

    def lose_reply(client, *args, **kwargs):
        original(client, *args, **kwargs)
        raise NvkeError("OPERATOR_UNCONFIRMED", "synthetic lost stop reply", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "stop_or_fence", lose_reply)
    with context(state.world), pytest.raises(NvkeError) as unknown:
        jobs.stop(**source_review(state, row))
    assert unknown.value.ambiguous
    row.refresh_from_db()
    assert row.status == "uncertain" and row.version == original_version
    with context(state.world):
        recovered = jobs.reconcile(**source_review(state, row))
    assert recovered.status == "cancelled" and recovered.version > original_version
    assert not list((state.agent.root / "state/jobs").iterdir())


@pytest.mark.parametrize(
    "key",
    [
        "expected_actor_id",
        "expected_site_version",
        "expected_authority_version",
        "expected_operation_version",
    ],
)
def test_stale_stop_review_never_closes_the_original_agent_intent(configured, key):
    state = configured
    row = reserve(state)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    reviewed = source_review(state, row)
    reviewed[key] += 1
    with context(state.world), pytest.raises((NvkeError, sites.NvkeSiteError, PermissionDenied)):
        jobs.stop(**reviewed)
    journal = json.loads((state.agent.root / "state/admissions.json").read_bytes())
    assert journal["payload"]["entries"] == {}


def test_schema_stop_is_reviewed_audited_and_refuses_revoked_admin(configured):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    state = configured
    row = reserve(state)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    variables = {
        "input": {
            "operationId": str(row.guid),
            "expectedActorId": state.world.user.pk,
            "expectedSiteVersion": state.site.version,
            "expectedAuthorityVersion": state.authority.version,
            "expectedOperationVersion": row.version,
        }
    }
    query = """mutation($input:NvkeOperatorJobReviewInput!){stopNvkeOperatorJob(input:$input){ok errors{code} data{id status}}}"""
    response = execute(state.world, query, variables)
    assert response.errors is None and response.data["stopNvkeOperatorJob"]["ok"]
    assert response.data["stopNvkeOperatorJob"]["data"] == {"id": str(row.guid), "status": "CANCELLED"}
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_stop").latest("timestamp")
    assert row.request_digest not in str(audit.variables)
    RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
    revoked = execute(state.world, query, variables)
    assert revoked.errors is None and not revoked.data["stopNvkeOperatorJob"]["ok"]


def test_stop_refuses_an_active_original_observer_lease(configured):
    from django.db import connection

    state = configured
    row = reserve(state)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    other = connection.copy()
    try:
        other.ensure_connection()
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [runtime.operation_lease_key(row.guid)])
        with context(state.world), pytest.raises(NvkeError) as busy:
            jobs.stop(**source_review(state, row))
        assert busy.value.code == "RESOURCE_BUSY"
        assert (
            json.loads((state.agent.root / "state/admissions.json").read_bytes())["payload"]["entries"] == {}
        )
    finally:
        other.close()


def test_current_admin_can_fence_original_intent_after_original_caller_revocation(configured):
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member
    from core.permissions import Permission
    from core.tests.utils.scope_world import bind_role

    state = configured
    row = reserve(state)
    row.status = "uncertain"
    row.save(update_fields=["status", "version"])
    caller = get_user_model().objects.create(username="stop-review-" + uuid4().hex, is_active=True)
    Member.objects.create(user=caller, scope_kind="ORG", scope_id=state.world.org.pk)
    bind_role(
        caller,
        permissions=[Permission.NVKE_ADMIN, Permission.NVKE_READ],
        kind="ORG",
        scope_id=state.world.org.pk,
        slug="stop-review-" + uuid4().hex,
    )
    RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
    reviewed = source_review(state, row)
    reviewed["expected_actor_id"] = caller.pk
    with context(SimpleNamespace(org=state.world.org, user=caller)):
        stopped = jobs.stop(**reviewed)
    assert stopped.status == "cancelled" and stopped.updated_by_id == caller.pk
    receipt = state.agent.client.observe(
        str(row.guid), command="generate.sitespec", intent=row.request_digest
    )
    assert receipt["errorCode"] == "ORIGINAL_JOB_FENCED"
