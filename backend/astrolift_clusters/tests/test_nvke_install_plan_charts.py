from __future__ import annotations

import copy
import json
import os
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_charts as charts
from astrolift_clusters import nvke_install_plans as plans
from astrolift_clusters.models import NvkeInstallPlan, NvkeInstallPlanCharts, NvkeOperation
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plans import prepared_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_operator import agent, configured, source_review, world  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import Member, RoleBinding
from core.permissions import Permission, PermissionDenied
from core.secrets import EncryptedSecret, decrypt
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def chart_fixture(prepared_fixture, monkeypatch):  # noqa: F811
    state, original, plan_id, plan_body, _metadata, _calls = prepared_fixture
    with context(state.world):
        parent = plans.prepare(body=plan_body, **source_review(state, original))
    body = {
        "format": 1,
        "artifactId": str(uuid4()),
        "planId": str(plan_id),
        "planDigest": "b" * 64,
        "maximumBytes": "9007199254740993",
        "maximumExpandedBytes": "9223372036854775807",
    }
    result = {
        "format": 1,
        "artifactId": body["artifactId"],
        "planId": str(plan_id),
        "planDigest": "b" * 64,
        "artifactDigest": "c" * 64,
        "chartCount": 1,
        "bytes": body["maximumBytes"],
        "expandedBytes": body["maximumExpandedBytes"],
        "executionState": "requires-rendering-images-and-live-verification",
        "continuationAuthorized": False,
    }
    calls = []

    def prepare(_client, job, *, intent, body):
        assert job == str(original.guid) and intent == original.request_digest
        assert NvkeInstallPlanCharts.objects.filter(guid=body["artifactId"]).exists()
        calls.append(("prepare", copy.deepcopy(body)))
        return copy.deepcopy(result)

    def read(_client, job, *, intent, plan_id, plan_digest, artifact_id):
        assert job == str(original.guid) and intent == original.request_digest
        assert (plan_id, plan_digest, artifact_id) == (body["planId"], body["planDigest"], body["artifactId"])
        calls.append(("read", None))
        return copy.deepcopy(result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_charts", prepare)
    monkeypatch.setattr(OperatorAgentClient, "install_plan_charts", read)
    review = {
        **source_review(state, original),
        "plan_id": plan_id,
        "expected_plan_version": parent["version"],
        "plan_digest": body["planDigest"],
    }
    return SimpleNamespace(
        state=state, original=original, body=body, result=result, calls=calls, review=review
    )


def test_durable_encrypted_intent_and_read_preserve_parent_and_original(chart_fixture):
    f = chart_fixture
    original = NvkeOperation.objects.filter(pk=f.original.pk).values().get()
    parent = NvkeInstallPlan.objects.filter(guid=f.body["planId"]).values().get()
    with context(f.state.world):
        result = charts.prepare(body=f.body, **f.review)
        assert charts.read(artifact_id=f.body["artifactId"], **f.review) == result
        assert charts.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    row = NvkeInstallPlanCharts.objects.get(guid=f.body["artifactId"])
    assert json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))) == f.body
    assert f.body["maximumExpandedBytes"].encode() not in bytes(row.input_ciphertext)
    assert row.status == "prepared" and result["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert NvkeOperation.objects.filter(pk=f.original.pk).values().get() == original
    assert NvkeInstallPlan.objects.filter(guid=f.body["planId"]).values().get() == parent
    assert NvkeOperation.objects.count() == 1


def test_lost_reply_recovers_by_read_without_another_preparation(chart_fixture, monkeypatch):
    f = chart_fixture

    def lost(*_a, **_k):
        f.calls.append(("lost", None))
        raise NvkeError("OPERATOR_UNAVAILABLE", "Deliberately lost response", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_charts", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError):
            charts.prepare(body=f.body, **f.review)
        assert NvkeInstallPlanCharts.objects.get(guid=f.body["artifactId"]).status == "preparing"
        assert charts.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["lost", "read"]


@pytest.mark.parametrize(
    "change", ["plan-version", "plan-digest", "revoke", "original-version", "delete-parent"]
)
def test_midflight_source_or_permission_change_cannot_publish(chart_fixture, monkeypatch, change):
    f = chart_fixture

    def drift(*_a, **_k):
        if change == "plan-version":
            NvkeInstallPlan.objects.filter(guid=f.body["planId"]).update(version=999)
        elif change == "plan-digest":
            row = NvkeInstallPlan.objects.get(guid=f.body["planId"])
            value = copy.deepcopy(row.summary)
            value["plan"]["planDigest"] = "f" * 64
            NvkeInstallPlan.objects.filter(pk=row.pk).update(summary=value)
        elif change == "revoke":
            RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
        elif change == "original-version":
            NvkeOperation.objects.filter(pk=f.original.pk).update(version=999)
        else:
            NvkeInstallPlan.objects.filter(guid=f.body["planId"]).update(deleted_at=timezone.now())
        return copy.deepcopy(f.result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_charts", drift)
    with context(f.state.world), pytest.raises((NvkeError, PermissionDenied)):
        charts.prepare(body=f.body, **f.review)
    row = NvkeInstallPlanCharts.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("continuationAuthorized", True),
        ("bytes", "9223372036854775807"),
        ("privatePath", "PRIVATE_CHART_INPUT"),
        ("artifactId", str(uuid4())),
    ],
)
def test_custom_adapter_malformed_or_authorizing_reply_is_redacted(chart_fixture, field, value):
    f = chart_fixture
    f.result[field] = value
    with context(f.state.world), pytest.raises(NvkeError) as error:
        charts.prepare(body=f.body, **f.review)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE_CHART_INPUT" not in str(error.value)
    assert NvkeInstallPlanCharts.objects.get(guid=f.body["artifactId"]).summary == {}


def test_another_current_admin_can_read_but_cannot_retry_private_intent(chart_fixture):
    f = chart_fixture
    with context(f.state.world):
        charts.prepare(body=f.body, **f.review)
    caller = get_user_model().objects.create(username="chart-admin-" + uuid4().hex, is_active=True)
    Member.objects.create(user=caller, scope_kind="ORG", scope_id=f.state.world.org.pk)
    bind_role(
        caller,
        permissions=[Permission.NVKE_ADMIN, Permission.NVKE_READ],
        kind="ORG",
        scope_id=f.state.world.org.pk,
        slug="chart-admin-" + uuid4().hex,
    )
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    review = {**f.review, "expected_actor_id": caller.pk}
    with context(SimpleNamespace(org=f.state.world.org, user=caller)):
        assert charts.read(artifact_id=f.body["artifactId"], **review)["metadata"] == f.result
        with pytest.raises(NvkeError) as error:
            charts.recover_preparation(artifact_id=f.body["artifactId"], **review)
        assert error.value.code == "PRECONDITION"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_changed_intent_deleted_identity_and_cross_kind_collision_are_refused(chart_fixture):
    f = chart_fixture
    with context(f.state.world):
        charts.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            charts.prepare(body={**f.body, "maximumBytes": "1"}, **f.review)
        assert error.value.code == "CONFLICT"
        with pytest.raises(NvkeError):
            charts.prepare(body={**f.body, "artifactId": str(f.original.guid)}, **f.review)
        NvkeInstallPlanCharts.objects.filter(guid=f.body["artifactId"]).update(deleted_at=timezone.now())
        with pytest.raises(NvkeError):
            charts.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError):
            charts.read(artifact_id=f.body["artifactId"], **f.review)
    assert [kind for kind, _ in f.calls] == ["prepare"]


@pytest.mark.parametrize(
    "field,value", [("summary", {}), ("status", "preparing"), ("request_digest", "f" * 64)]
)
def test_prepared_identity_private_intent_and_summary_are_immutable(chart_fixture, field, value):
    f = chart_fixture
    with context(f.state.world):
        charts.prepare(body=f.body, **f.review)
    row = NvkeInstallPlanCharts.objects.get(guid=f.body["artifactId"])
    setattr(row, field, value)
    with pytest.raises(ValueError):
        row.save()


def test_tampered_retained_private_intent_is_refused_before_agent_retry(chart_fixture):
    f = chart_fixture
    with context(f.state.world):
        charts.prepare(body=f.body, **f.review)
        NvkeInstallPlanCharts.objects.filter(guid=f.body["artifactId"]).update(request_digest="0" * 64)
        with pytest.raises(NvkeError) as error:
            charts.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
        assert error.value.code == "PREPARATION_UNAVAILABLE"
    assert [kind for kind, _ in f.calls] == ["prepare"]


def test_chart_identity_cannot_be_reused_as_a_plan(chart_fixture):
    f = chart_fixture
    with context(f.state.world):
        charts.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            plans.prepare(
                body={
                    "format": 1,
                    "planId": f.body["artifactId"],
                    "request": None,
                    "maximumInputBytes": "10000",
                },
                **source_review(f.state, f.original),
            )
        assert error.value.code == "CONFLICT"


def graph_review(f):
    return {
        "operationId": str(f.original.guid),
        "expectedActorId": f.state.world.user.pk,
        "expectedSiteVersion": f.state.site.version,
        "expectedAuthorityVersion": f.state.authority.version,
        "expectedOperationVersion": f.original.version,
        "planId": f.body["planId"],
        "expectedPlanVersion": f.review["expected_plan_version"],
        "planDigest": f.body["planDigest"],
    }


FIELDS = "ok errors{code} data{id version status planId planVersion operationId operationVersion siteId siteVersion authorityId authorityVersion planDigest artifactDigest chartCount bytes expandedBytes executionState continuationAuthorized}"


def test_typed_schema_prepare_review_retry_and_private_audit(chart_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = chart_fixture
    prepare = (
        "mutation($alias:PrepareNvkeOperatorInstallPlanChartsInput!){prepareNvkeOperatorInstallPlanCharts(input:$alias){"
        + FIELDS
        + "}}"
    )
    review = graph_review(f)
    response = execute(f.state.world, prepare, {"alias": {**review, "requestText": json.dumps(f.body)}})
    assert response.errors is None and response.data["prepareNvkeOperatorInstallPlanCharts"]["ok"]
    data = response.data["prepareNvkeOperatorInstallPlanCharts"]["data"]
    assert data["id"] == f.body["artifactId"] and data["bytes"] == f.body["maximumBytes"]
    assert data["expandedBytes"] == f.body["maximumExpandedBytes"] and data["continuationAuthorized"] is False
    for action in ("reviewNvkeOperatorInstallPlanCharts", "recoverNvkeOperatorInstallPlanChartsPreparation"):
        response = execute(
            f.state.world,
            "mutation($v:NvkeOperatorInstallPlanChartsReviewInput!){"
            + action
            + "(input:$v){"
            + FIELDS
            + "}}",
            {"v": {**review, "artifactId": f.body["artifactId"]}},
        )
        assert (
            response.errors is None and response.data[action]["ok"] and response.data[action]["data"] == data
        )
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_charts_prepare").latest(
        "timestamp"
    )
    assert f.original.request_digest not in str(audit.variables)
    assert f.body["maximumExpandedBytes"] not in str(audit.variables)
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]


def test_typed_schema_rejected_private_text_and_denied_caller_are_redacted(chart_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = chart_fixture
    query = "mutation($renamed:PrepareNvkeOperatorInstallPlanChartsInput!){prepareNvkeOperatorInstallPlanCharts(input:$renamed){ok errors{code}}}"
    values = {**graph_review(f), "requestText": json.dumps({**f.body, "credentials": "PRIVATE_CHART_INPUT"})}
    response = execute(f.state.world, query, {"renamed": values})
    assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlanCharts"]["ok"]
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_charts_prepare").latest(
        "timestamp"
    )
    assert "PRIVATE_CHART_INPUT" not in str(audit.variables)
    assert not NvkeInstallPlanCharts.objects.exists() and f.calls == []
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    response = execute(
        f.state.world, query, {"renamed": {**values, "requestText": "not json PRIVATE_CHART_INPUT"}}
    )
    assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlanCharts"]["ok"]
    assert f.calls == []


def test_wrong_plan_review_refused_before_agent_io(chart_fixture):
    f = chart_fixture
    with context(f.state.world):
        for change in ({"expected_plan_version": 0}, {"plan_digest": "f" * 64}, {"plan_id": uuid4()}):
            with pytest.raises(NvkeError):
                charts.prepare(body=f.body, **{**f.review, **change})
    assert f.calls == [] and not NvkeInstallPlanCharts.objects.exists()


def test_new_token_can_read_but_cannot_repeat_original_private_intent(chart_fixture):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = chart_fixture

    def token():
        issued = mint_token()
        return ApiToken.objects.create(
            user=f.state.world.user,
            organization=f.state.world.org,
            name="chart token",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=["admin", "read"],
        )

    original, replacement = token(), token()
    mark = set_current_api_token(original)
    try:
        with context(f.state.world):
            charts.prepare(body=f.body, **f.review)
    finally:
        reset_current_api_token(mark)
    ApiToken.objects.filter(pk=original.pk).update(is_revoked=True)
    mark = set_current_api_token(replacement)
    try:
        with context(f.state.world):
            assert charts.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
            with pytest.raises(NvkeError) as error:
                charts.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
            assert error.value.code == "PRECONDITION"
    finally:
        reset_current_api_token(mark)
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_token_revoked_during_remote_prepare_cannot_publish(chart_fixture, monkeypatch):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = chart_fixture
    issued = mint_token()
    token = ApiToken.objects.create(
        user=f.state.world.user,
        organization=f.state.world.org,
        name="chart revoked token",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["admin", "read"],
    )

    def revoked(*_a, **_k):
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
        return copy.deepcopy(f.result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_charts", revoked)
    mark = set_current_api_token(token)
    try:
        with context(f.state.world), pytest.raises((PermissionDenied, NvkeSiteError)) as error:
            charts.prepare(body=f.body, **f.review)
        if isinstance(error.value, NvkeSiteError):
            assert error.value.code == "AUTHORITY_REVOKED"
    finally:
        reset_current_api_token(mark)
    assert NvkeInstallPlanCharts.objects.get(guid=f.body["artifactId"]).summary == {}


def test_other_tenant_cannot_read_retained_artifacts(chart_fixture):
    from astrolift_clusters.tests.test_nvke_sites import world as world_fixture

    f = chart_fixture
    with context(f.state.world):
        charts.prepare(body=f.body, **f.review)
    other = world_fixture.__wrapped__()
    with context(other), pytest.raises((NvkeError, PermissionDenied)):
        charts.read(artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": other.user.pk})
    assert [kind for kind, _ in f.calls] == ["prepare"]


@pytest.mark.skipif(not shutil.which("helm"), reason="Real Helm is required")
def test_real_graphql_postgres_sdk_mtls_helm_lost_reply_recovers_without_refetch(configured, monkeypatch):  # noqa: F811
    from astrolift_clusters import nvke_operator_authority as authority
    from astrolift_clusters.tests.test_nvke_operator import partial_install, review
    from astrolift_clusters.tests.test_nvke_schema import execute

    monkeypatch.syspath_prepend(os.path.join(os.environ["NVKE_TEST_AGENT_OPSCODE"], "kubernetes/nvke"))
    import test_management_chart_materialization as chart_lab
    import test_operator_agent as native_lab

    state = configured
    monkeypatch.setattr(native_lab, "SITE", str(state.site.guid))
    native_lab.NativeAgentTests.setUpClass()
    case, repository = native_lab.NativeAgentTests(), chart_lab.ChartMaterializationTests()
    try:
        case.setUp()
        repository.setUp()
        try:
            credentials = {
                key: (case.certs / filename).read_text()
                for key, filename in (
                    ("ca_pem", "ca.pem"),
                    ("client_certificate_pem", "client.pem"),
                    ("client_key_pem", "client.key"),
                )
            }
            with context(state.world):
                state.authority = authority.configure(
                    **review(state),
                    endpoint=case.url,
                    instance_id=case.engine.instance_id,
                    bundle_manifest_sha256=case.bundle_digest,
                    credentials=credentials,
                )
                state.site.refresh_from_db()
                state.authority = authority.verify(**review(state))
                original = partial_install(state)
            job, intent, plan_body = case.plan_fixture()
            # The owned fixture supplies an unsuccessful native receipt; this
            # mapping is setup only, before any plan/chart request or snapshot.
            NvkeOperation.objects.filter(pk=original.pk).update(guid=job, request_digest=intent)
            original.refresh_from_db()
            original_row = NvkeOperation.objects.filter(pk=original.pk).values().get()
            original_native = (case.engine._job(job) / "state.json").read_bytes()
            original_guard = (case.engine.workspace / "management-install-state.json").read_bytes()
            runs = sorted(path.name for path in (case.engine.workspace / "runs").iterdir())
            repository.serve("nktp")
            graph = {"charts": [repository.remote()], "inputs": [], "continuationAuthorized": False}
            graph["inventoryDigest"] = native_lab.agent.management_plan_chart_service.artifacts._hash(graph)
            with context(state.world), monkeypatch.context() as native_patch:
                native_patch.setattr(
                    native_lab.agent.management_plan_preparation.qualification, "kubectl", case.plan_kubectl
                )
                native_patch.setattr(
                    native_lab.agent.management_plan_preparation.dependencies, "inventory", lambda **_k: graph
                )
                parent = plans.prepare(body=plan_body, **source_review(state, original))
            prepared_parent = NvkeInstallPlan.objects.filter(guid=plan_body["planId"]).values().get()
            artifact_id = str(uuid4())
            body = {
                "format": 1,
                "artifactId": artifact_id,
                "planId": plan_body["planId"],
                "planDigest": parent["metadata"]["plan"]["planDigest"],
                "maximumBytes": "1048576",
                "maximumExpandedBytes": "1048576",
                "allowPlainHttp": True,
            }
            args = {
                **source_review(state, original),
                "plan_id": plan_body["planId"],
                "expected_plan_version": parent["version"],
                "plan_digest": body["planDigest"],
            }
            fake = SimpleNamespace(state=state, original=original, body=body, review=args)
            reviewed = graph_review(fake)
            actual = OperatorAgentClient.prepare_install_plan_charts

            def lose_after_publication(client, *a, **kw):
                actual(client, *a, **kw)
                raise NvkeError(
                    "OPERATOR_UNAVAILABLE", "Deliberately lost application response", ambiguous=True
                )

            monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_charts", lose_after_publication)
            query = (
                "mutation($v:PrepareNvkeOperatorInstallPlanChartsInput!){prepareNvkeOperatorInstallPlanCharts(input:$v){"
                + FIELDS
                + "}}"
            )
            response = execute(state.world, query, {"v": {**reviewed, "requestText": json.dumps(body)}})
            assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlanCharts"]["ok"]
            assert NvkeInstallPlanCharts.objects.get(guid=artifact_id).status == "preparing"
            assert repository.requests.count("/nktp-1.0.0.tgz") == 1
            requests = list(repository.requests)
            repository.responses.clear()
            recover = (
                "mutation($v:NvkeOperatorInstallPlanChartsReviewInput!){reviewNvkeOperatorInstallPlanCharts(input:$v){"
                + FIELDS
                + "}}"
            )
            variables = {"v": {**reviewed, "artifactId": artifact_id}}
            response = execute(state.world, recover, variables)
            assert response.errors is None and response.data["reviewNvkeOperatorInstallPlanCharts"]["ok"]
            data = response.data["reviewNvkeOperatorInstallPlanCharts"]["data"]
            assert (
                data["id"] == artifact_id and data["chartCount"] == 1 and not data["continuationAuthorized"]
            )
            assert int(data["bytes"]) > 0 and isinstance(data["bytes"], str)
            monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_charts", actual)
            response = execute(
                state.world,
                recover.replace(
                    "reviewNvkeOperatorInstallPlanCharts", "recoverNvkeOperatorInstallPlanChartsPreparation"
                ),
                variables,
            )
            assert (
                response.errors is None
                and response.data["recoverNvkeOperatorInstallPlanChartsPreparation"]["ok"]
            )
            assert response.data["recoverNvkeOperatorInstallPlanChartsPreparation"]["data"] == data
            assert repository.requests == requests
            assert NvkeOperation.objects.filter(pk=original.pk).values().get() == original_row
            assert NvkeInstallPlan.objects.filter(guid=plan_body["planId"]).values().get() == prepared_parent
            assert (case.engine._job(job) / "state.json").read_bytes() == original_native
            assert (case.engine.workspace / "management-install-state.json").read_bytes() == original_guard
            assert sorted(path.name for path in (case.engine.workspace / "runs").iterdir()) == runs
            assert list(case.engine.install_plans.links.iterdir()) == []
        finally:
            repository.doCleanups()
            case.tearDown()
    finally:
        native_lab.NativeAgentTests.tearDownClass()
