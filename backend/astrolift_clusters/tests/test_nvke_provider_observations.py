from __future__ import annotations

import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone
from nvke.client import NvkeError
from nvke.infrastructure_client import METADATA_ACCEPT, InfrastructureClient

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_provider_observations as observations
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeConnection, NvkeOperation
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import datastore_site as _datastore_site
from astrolift_clusters.tests.test_nvke_operations import world as _world
from astrolift_clusters.tests.test_nvke_schema import execute as graphql
from astrolift_clusters.tests.test_nvke_sites import context, put
from astrolift_identity.models import RoleBinding
from core.permissions import Permission, PermissionDenied
from core.schema.audit import MutationAuditLog
from core.secrets import encrypt_at_rest
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def peer():
    state = SimpleNamespace(calls=[], extra_count=31, generation=9007199254740993, status=200)
    checked = (timezone.now() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S") + ".123456789Z"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            state.calls.append((path, self.headers.get("Accept")))
            code, payload = state.status, {}
            if path == "/apis/apps/v1/deployments":
                payload = {
                    "metadata": {"resourceVersion": "17"},
                    "items": [
                        {
                            "metadata": {
                                "name": "nico-manager",
                                "namespace": "capnico-system",
                                "generation": 1,
                                "labels": {"app.kubernetes.io/name": "cluster-api-provider-nico"},
                            },
                            "spec": {
                                "template": {
                                    "spec": {
                                        "containers": [{"args": ["--private-arg=PRIVATE_MANAGER_VALUE"]}]
                                    }
                                }
                            },
                            "status": {
                                "observedGeneration": 1,
                                "readyReplicas": 1,
                                "replicas": 1,
                                "conditions": [{"type": "Available", "status": "True"}],
                            },
                        }
                    ],
                }
            elif "/customresourcedefinitions/" in path:
                name = path.rsplit("/", 1)[1]
                if name.startswith("launchlayer"):
                    code = 404
                else:
                    payload = {
                        "metadata": {"name": name},
                        "spec": {"versions": [{"served": True}]},
                        "status": {"conditions": [{"type": "Established", "status": "True"}]},
                    }
            elif "/secrets/" in path:
                assert self.headers.get("Accept") == METADATA_ACCEPT
                payload = {
                    "apiVersion": "meta.k8s.io/v1",
                    "kind": "PartialObjectMetadata",
                    "metadata": {"namespace": "capnico-system", "name": "nico-credentials"},
                }
            elif path.endswith("/nicoidentities"):
                payload = {
                    "metadata": {"resourceVersion": "18"},
                    "items": [
                        {
                            "metadata": {
                                "name": name,
                                "namespace": "capnico-system",
                                "generation": state.generation,
                                "creationTimestamp": checked,
                            },
                            "spec": {"credentialsRef": {"name": "nico-credentials"}},
                            "status": {
                                "lastCheckedTime": checked,
                                "conditions": [
                                    {
                                        "type": "Ready",
                                        "status": "True",
                                        "observedGeneration": state.generation,
                                        "reason": "PRIVATE_NATIVE_REASON",
                                        "message": "PRIVATE_NATIVE_MESSAGE",
                                    }
                                ],
                            },
                        }
                        for name in ["a-selected", *[f"extra-{i:03d}" for i in range(state.extra_count)]]
                    ],
                }
            else:
                code = 404
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def configured(peer):
    state = _configured.__wrapped__(_world.__wrapped__())
    with context(state.world):
        state.connection = put(
            state.site,
            surface="datastore",
            endpoint=peer.url,
            allow_http=True,
            credentials={"token": "PRIVATE_KUBE_TOKEN"},
        )
    state.site.refresh_from_db()
    state.peer = peer
    return state


@pytest.fixture
def world():
    return _world.__wrapped__()


@pytest.fixture
def datastore_site(world):
    yield from _datastore_site.__wrapped__(world)


def reserve(state, **changes):
    args = {
        "site_id": state.site.guid,
        "expected_actor_id": state.world.user.pk,
        "expected_site_version": state.site.version,
        "connection_id": state.connection.guid,
        "expected_connection_version": state.connection.version,
        "request_id": uuid4(),
    }
    args.update(changes)
    with context(state.world):
        return observations.reserve(**args)


def run(row):
    result = runtime.attempt(
        row.pk, row.generation, execution=(row.workflow_id, "owned-provider-run", "NvkeOperationWorkflow")
    )
    row.refresh_from_db()
    return result


def review(state, row):
    return {
        "site_id": str(state.site.guid),
        "operation_id": str(row.guid),
        "expected_actor_id": state.world.user.pk,
        "expected_version": row.version,
    }


def test_durable_request_is_receipt_first_and_bound_to_reviewed_connection(configured):
    nonce = uuid4()
    row = reserve(configured, request_id=nonce)
    assert row.operation_key == observations.KEY and not configured.peer.calls
    assert "PRIVATE" not in str(row.authority) and "PRIVATE" not in bytes(row.input_ciphertext).decode(
        errors="ignore"
    )
    assert outbox.decode_input(row) == {"provider_observation_format": 1}
    original = {
        "request_id": nonce,
        "expected_site_version": configured.site.version,
        "expected_connection_version": configured.connection.version,
    }
    configured.connection.soft_delete()
    configured.site.save()
    assert reserve(configured, **original).pk == row.pk
    for changes in (
        {"timeout_seconds": 30},
        {"connection_id": uuid4()},
        {"expected_connection_version": 999},
        {"expected_site_version": 999},
    ):
        with pytest.raises(NvkeError) as error:
            reserve(configured, **{**original, **changes})
        assert error.value.code == "CONFLICT"
    row.soft_delete()
    with pytest.raises(NvkeError) as error:
        reserve(configured, **original)
    assert error.value.code == "CONFLICT" and NvkeOperation.all_objects.filter(request_id=nonce).count() == 1


@pytest.mark.parametrize(
    "change",
    ["actor", "connection", "version", "surface", "site-version", "site-disabled", "revision", "timeout"],
)
def test_unreviewed_or_unavailable_admission_never_creates_an_operation(configured, change):
    args = {}
    if change == "actor":
        args["expected_actor_id"] = configured.world.user.pk + 1
    elif change == "connection":
        args["connection_id"] = uuid4()
    elif change == "version":
        args["expected_connection_version"] = 0
    elif change == "site-version":
        args["expected_site_version"] = 0
    elif change == "timeout":
        args["timeout_seconds"] = True
    elif change == "surface":
        other = NvkeConnection.objects.get(site=configured.site, surface="controller")
        args.update(connection_id=other.guid, expected_connection_version=other.version)
    elif change == "site-disabled":
        configured.site.is_active = False
        configured.site.save()
        args["expected_site_version"] = configured.site.version
    else:
        type(configured.site).objects.filter(pk=configured.site.pk).update(upstream_revision="0" * 40)
    with pytest.raises((NvkeError, sites.NvkeSiteError)):
        reserve(configured, **args)
    assert not NvkeOperation.objects.exists() and not configured.peer.calls


def test_no_wildcard_permission_or_generic_operation_can_dispatch_provider_reads(configured):
    assert outbox.operation_permission(observations.KEY) == Permission.NVKE_ADMIN
    with pytest.raises(NvkeError):
        outbox.operation_permission("infrastructure.arbitrary")
    with context(configured.world), pytest.raises(NvkeError):
        outbox.reserve(
            site_id=configured.site.guid,
            expected_site_version=configured.site.version,
            request_id=uuid4(),
            operation_key=observations.KEY,
            parameters={},
        )
    assert not NvkeOperation.objects.exists()


def test_real_database_observation_preserves_precision_and_historical_independence(configured):
    row = reserve(configured)
    assert run(row) == "succeeded"
    assert row.workflow_run_id == "owned-provider-run" and row.finished_at
    assert b"a-selected" not in bytes(row.result_ciphertext)
    raw = json.dumps(outbox.decode_result(row))
    assert "PRIVATE" not in raw and "extra-030" in raw
    with context(configured.world):
        _, data = observations.observation(**review(configured, row))
    assert data["complete"] and data["providers"][0]["credentials"]["health"] == "HEALTHY"
    assert data["providers"][0]["credentials"]["identity_generation"] == "9007199254740993"
    assert data["providers"][0]["credentials"]["last_checked_time"].endswith("123456789Z")
    assert data["providers"][0]["credentials"]["additional_identity_count"] == 31
    assert "extra-030" not in json.dumps(data)
    calls = len(configured.peer.calls)
    configured.connection.soft_delete()
    configured.site.save()
    with context(configured.world):
        assert observations.observation(**review(configured, row))[1] == data
    assert len(configured.peer.calls) == calls
    with context(configured.world):
        generic_result = outbox.result(str(row.guid))
    assert generic_result["providers"] == data["providers"]
    assert "extra-030" not in json.dumps(generic_result)
    assert "9007199254740993" in json.dumps(generic_result)


def test_real_database_and_tls_kubernetes_observation(datastore_site):
    row = reserve(datastore_site)
    assert run(row) == "succeeded"
    with context(datastore_site.world):
        _, data = observations.observation(**review(datastore_site, row))
    assert data["complete"] and [p["state"] for p in data["providers"]] == ["NOT-INSTALLED", "NOT-INSTALLED"]


@pytest.mark.parametrize("change", ["disable", "rotate", "membership", "role", "cancel", "deadline"])
def test_execution_rechecks_authority_before_any_kubernetes_read(configured, change):
    row = reserve(configured)
    if change == "disable":
        NvkeConnection.objects.filter(pk=configured.connection.pk).update(is_enabled=False)
    elif change == "rotate":
        NvkeConnection.objects.filter(pk=configured.connection.pk).update(
            credential_ciphertext=b"PRIVATE_CHANGED"
        )
    elif change == "membership":
        configured.world.member.soft_delete()
    elif change == "role":
        RoleBinding.objects.filter(user=configured.world.user).update(deleted_at=timezone.now())
    elif change == "cancel":
        NvkeOperation.objects.filter(pk=row.pk).update(cancellation_requested_at=timezone.now())
    else:
        NvkeOperation.objects.filter(pk=row.pk).update(deadline=timezone.now() - timedelta(seconds=1))
    assert run(row) == ("cancelled" if change == "cancel" else "failed")
    assert not configured.peer.calls and not row.result_ciphertext


@pytest.mark.parametrize("change", ["source", "generation", "run", "membership", "cancel", "deadline"])
def test_inflight_fences_prevent_followup_io_and_late_publication(configured, monkeypatch, change):
    row = reserve(configured)
    original = InfrastructureClient._fetch
    injected = False

    def fetch(client, *args, **kwargs):
        nonlocal injected
        result = original(client, *args, **kwargs)
        if not injected:
            injected = True
            if change == "source":
                NvkeConnection.objects.filter(pk=configured.connection.pk).update(is_enabled=False)
            elif change == "generation":
                NvkeOperation.objects.filter(pk=row.pk).update(
                    generation=1, status="queued", stage="new-attempt"
                )
            elif change == "run":
                NvkeOperation.objects.filter(pk=row.pk).update(
                    workflow_run_id="new-owner", status="queued", stage="new-attempt"
                )
            elif change == "membership":
                configured.world.member.soft_delete()
            elif change == "cancel":
                NvkeOperation.objects.filter(pk=row.pk).update(cancellation_requested_at=timezone.now())
            else:
                NvkeOperation.objects.filter(pk=row.pk).update(deadline=timezone.now() - timedelta(seconds=1))
        return result

    monkeypatch.setattr(InfrastructureClient, "_fetch", fetch)
    outcome = run(row)
    assert len(configured.peer.calls) == 1 and not row.result_ciphertext
    if change in {"generation", "run"}:
        assert outcome == "execution_refused" and row.stage == "new-attempt" and row.status == "queued"
    else:
        assert outcome == ("cancelled" if change == "cancel" else "failed")


def test_identity_pages_search_all_saved_rows_and_bind_cursor_to_view(configured):
    row = reserve(configured)
    assert run(row) == "succeeded"
    with context(configured.world):
        args = review(configured, row)
        _, _, first, cursor, total = observations.identities_page(provider="nico", **args)
        _, _, second, last, _ = observations.identities_page(provider="nico", after=cursor, **args)
        assert len(first) == 25 and len(second) == 6 and total == 31 and last is None
        assert not {p["key"] for p in first} & {p["key"] for p in second}
        _, _, found, _, count = observations.identities_page(provider="nico", search="extra-030", **args)
        assert count == 1 and found[0]["name"] == "extra-030"
        for changes in ({"provider": "launchlayer"}, {"search": "extra"}, {"limit": 2}):
            with pytest.raises(NvkeError) as error:
                observations.identities_page(**{**args, "provider": "nico", "after": cursor, **changes})
            assert error.value.code == "INVALID_CURSOR"
        with pytest.raises(NvkeError) as error:
            observations.observation(**{**args, "expected_version": row.version - 1})
        assert error.value.code == "SNAPSHOT_UNAVAILABLE"


def test_cross_org_receipts_and_snapshots_are_indistinguishable_from_missing(configured):
    row = reserve(configured)
    assert run(row) == "succeeded"
    other = _world.__wrapped__()
    with context(other):
        for identifier in (row.guid, uuid4()):
            with pytest.raises(NvkeError) as error:
                observations.observation(
                    **{
                        **review(configured, row),
                        "operation_id": identifier,
                        "expected_actor_id": other.user.pk,
                    }
                )
            assert error.value.code == "NOT_FOUND"
    with context(other), pytest.raises(sites.NvkeSiteError) as error:
        observations.reserve(
            site_id=configured.site.guid,
            expected_actor_id=other.user.pk,
            expected_site_version=configured.site.version,
            connection_id=configured.connection.guid,
            expected_connection_version=configured.connection.version,
            request_id=uuid4(),
        )
    assert error.value.code == "NOT_FOUND"


def test_read_only_role_cannot_collect_or_decrypt_provider_history(configured, monkeypatch):
    row = reserve(configured)
    assert run(row) == "succeeded"
    RoleBinding.objects.filter(user=configured.world.user).update(deleted_at=timezone.now())
    bind_role(
        configured.world.user,
        permissions=[Permission.NVKE_READ],
        kind="ORG",
        scope_id=configured.world.org.pk,
        slug="provider-reader-" + uuid4().hex[:8],
    )
    monkeypatch.setattr(outbox, "decode_result", lambda _row: pytest.fail("unauthorized payload decryption"))
    with context(configured.world), pytest.raises(PermissionDenied):
        observations.observation(**review(configured, row))
    with pytest.raises(PermissionDenied):
        reserve(configured)


@pytest.mark.parametrize(
    "corruption",
    [
        "format",
        "connection",
        "unknown-field",
        "boolean-count",
        "bad-health",
        "bad-reason",
        "bad-generation",
        "duplicate-identity",
        "wrong-kind",
    ],
)
def test_malformed_saved_projection_never_discloses_payload(configured, corruption):
    row = reserve(configured)
    assert run(row) == "succeeded"
    data = outbox.decode_result(row)
    provider = data["observation"]["providers"][0]
    health = provider["credentials"]
    if corruption == "format":
        data["provider_observation_format"] = True
    elif corruption == "connection":
        data["connection_id"] = str(uuid4())
    elif corruption == "unknown-field":
        provider["PRIVATE_UNEXPECTED"] = "PRIVATE_NATIVE_DATA"
    elif corruption == "boolean-count":
        provider["ready_replicas"] = True
    elif corruption == "bad-health":
        health["health"] = "PRIVATE_NATIVE_DATA"
    elif corruption == "bad-reason":
        health["reason"] = "PRIVATE_NATIVE_DATA"
    elif corruption == "bad-generation":
        health["observed_generation"] = 1
    elif corruption == "duplicate-identity":
        health["additional_identities"].append(health["additional_identities"][0])
    else:
        provider["provider"] = "unknown-provider"
    sealed = encrypt_at_rest(outbox.canonical(data))
    NvkeOperation.objects.filter(pk=row.pk).update(
        result_backend_kind=sealed.backend_kind, result_ciphertext=sealed.backend_ref
    )
    with context(configured.world), pytest.raises(NvkeError) as error:
        observations.observation(**review(configured, row))
    assert error.value.code == "SNAPSHOT_UNAVAILABLE" and "PRIVATE" not in str(error.value)
    with context(configured.world), pytest.raises(NvkeError) as generic_error:
        outbox.result(str(row.guid))
    assert generic_error.value.code == "SNAPSHOT_UNAVAILABLE"


def test_graphql_admission_catalog_and_historical_types_keep_exact_metadata(configured):
    variables = {
        "input": {
            "siteId": str(configured.site.guid),
            "expectedActorId": configured.world.user.pk,
            "expectedSiteVersion": configured.site.version,
            "connectionId": str(configured.connection.guid),
            "expectedConnectionVersion": configured.connection.version,
            "requestId": str(uuid4()),
        }
    }
    mutation = """mutation($input:RequestNvkeProviderObservationInput!){requestNvkeProviderObservation(input:$input){ok errors{code} data{id version requestId operationKey providerSource{connectionId connectionVersion siteVersion upstreamRevision}}}}"""
    result = graphql(configured.world, mutation, variables)
    assert result.errors is None and result.data["requestNvkeProviderObservation"]["ok"]
    receipt = result.data["requestNvkeProviderObservation"]["data"]
    assert receipt["providerSource"]["connectionId"] == str(configured.connection.guid)
    assert receipt["providerSource"]["connectionVersion"] == configured.connection.version
    row = NvkeOperation.objects.get(guid=receipt["id"])
    assert run(row) == "succeeded"
    replay = graphql(configured.world, mutation, variables)
    assert (
        replay.errors is None and replay.data["requestNvkeProviderObservation"]["data"]["id"] == receipt["id"]
    )
    query = """query($site:GUID!,$op:GUID!,$actor:Int!,$version:Int!){astroliftNvkeProviderObservation(siteId:$site,operationId:$op,expectedActorId:$actor,expectedVersion:$version){ok errors{code} data{connectionId connectionVersion observedAt complete providers{provider state credentials{identityGeneration observedGeneration lastCheckedTime additionalIdentityCount}}}} astroliftNvkeProviderIdentitiesPage(siteId:$site,operationId:$op,expectedActorId:$actor,expectedVersion:$version,provider:"nico",search:"extra-030"){ok data{totalCount items{name}}} astroliftNvkeContract{infrastructureProviders actions{key}}}"""
    response = graphql(
        configured.world,
        query,
        {
            "site": str(configured.site.guid),
            "op": str(row.guid),
            "actor": configured.world.user.pk,
            "version": row.version,
        },
    )
    assert response.errors is None
    value = response.data["astroliftNvkeProviderObservation"]
    assert (
        value["ok"]
        and value["data"]["providers"][0]["credentials"]["identityGeneration"] == "9007199254740993"
    )
    assert response.data["astroliftNvkeProviderIdentitiesPage"]["data"]["totalCount"] == 1
    assert (
        response.data["astroliftNvkeContract"]["infrastructureProviders"]["operationKey"] == observations.KEY
    )
    assert len(response.data["astroliftNvkeContract"]["actions"]) == 88
    assert "PRIVATE" not in json.dumps(response.data)
    log = MutationAuditLog.objects.filter(operation="nvke.provider_observation_request").latest("timestamp")
    assert "PRIVATE" not in json.dumps(log.variables)


@pytest.mark.django_db(transaction=True)
async def test_real_temporal_carries_only_operation_ids_not_provider_inventory(configured, monkeypatch):
    import asyncio

    from asgiref.sync import sync_to_async

    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
    from core.testing.temporal import temporal_test_env, temporal_worker

    async with temporal_test_env() as env:

        async def get_client():
            return env.client

        monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
        monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
        monkeypatch.setattr(
            workflow_client, "_task_queue", lambda: "nvke-providers-" + str(configured.site.guid)
        )
        async with temporal_worker(
            env,
            task_queue=workflow_client._task_queue(),
            workflows=[NvkeOperationWorkflow],
            activities=[nvke_operation_attempt],
        ):
            row = await sync_to_async(reserve)(configured)
            handle = env.client.get_workflow_handle(row.workflow_id)
            assert await asyncio.wait_for(handle.result(), 30) == "succeeded"
            await sync_to_async(row.refresh_from_db)()
            raw = (await handle.fetch_history()).to_json()
            for private in (
                "PRIVATE_KUBE_TOKEN",
                "nico-credentials",
                "capnico-system",
                "a-selected",
                "extra-030",
                "additional_identities",
            ):
                assert private not in raw
            history = await handle.fetch_history()
            started = history.events[0].workflow_execution_started_event_attributes
            assert await env.client.data_converter.decode(started.input.payloads) == [row.pk, 0]


@pytest.mark.parametrize("revocation", ["revoked", "expired", "narrowed"])
def test_original_scoped_token_is_rechecked_before_execution(configured, revocation):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    issued = mint_token()
    token = ApiToken.objects.create(
        user=configured.world.user,
        organization=configured.world.org,
        name="provider automation",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:nvke", "admin:nvke"],
    )
    mark = set_current_api_token(token)
    try:
        row = reserve(configured)
    finally:
        reset_current_api_token(mark)
    if revocation == "revoked":
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
    elif revocation == "expired":
        ApiToken.objects.filter(pk=token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    else:
        ApiToken.objects.filter(pk=token.pk).update(scopes=["read:nvke"])
    assert run(row) == "failed" and row.error_code == "AUTHORITY_REVOKED"
    assert not configured.peer.calls


def test_cached_role_and_token_cannot_read_historical_payload_after_revocation(configured):
    from astrolift_identity import abac
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    row = reserve(configured)
    assert run(row) == "succeeded"
    issued = mint_token()
    token = ApiToken.objects.create(
        user=configured.world.user,
        organization=configured.world.org,
        name="provider history",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:nvke", "admin:nvke"],
    )
    mark = set_current_api_token(token)
    try:
        with (
            context(configured.world),
            abac.request_attributes(abac.attributes_for(configured.world.user.pk)),
        ):
            observations.observation(**review(configured, row))
            assert outbox.result(str(row.guid))["provider_observation_format"] == 1
            ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
            with pytest.raises(NvkeError) as error:
                observations.observation(**review(configured, row))
            assert error.value.code == "AUTHORITY_REVOKED"
            with pytest.raises(NvkeError) as raw_error:
                outbox.result(str(row.guid))
            assert raw_error.value.code == "AUTHORITY_REVOKED"
            ApiToken.objects.filter(pk=token.pk).update(is_revoked=False)
            RoleBinding.objects.filter(user=configured.world.user).update(deleted_at=timezone.now())
            with pytest.raises(PermissionDenied):
                observations.observation(**review(configured, row))
            with pytest.raises(PermissionDenied):
                outbox.result(str(row.guid))
    finally:
        reset_current_api_token(mark)


def test_cursor_cannot_be_reused_by_another_current_admin_in_the_same_org(configured):
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member

    row = reserve(configured)
    assert run(row) == "succeeded"
    with context(configured.world):
        _, _, _, cursor, _ = observations.identities_page(provider="nico", **review(configured, row))
    user = get_user_model().objects.create(username="provider-other-" + uuid4().hex[:8], is_active=True)
    member = Member.objects.create(user=user, scope_kind="ORG", scope_id=configured.world.org.pk)
    bind_role(
        user,
        permissions=[Permission.NVKE_READ, Permission.NVKE_ADMIN],
        kind="ORG",
        scope_id=configured.world.org.pk,
        slug="provider-other-admin-" + uuid4().hex[:8],
    )
    other = SimpleNamespace(org=configured.world.org, user=user, member=member)
    with context(other):
        arguments = {**review(configured, row), "expected_actor_id": user.pk}
        assert observations.observation(**arguments)[1]["complete"]
        with pytest.raises(NvkeError) as error:
            observations.identities_page(provider="nico", after=cursor, **arguments)
        assert error.value.code == "INVALID_CURSOR"


def test_partial_collection_success_is_not_provider_readiness(configured):
    configured.peer.status = 503
    row = reserve(configured)
    assert run(row) == "succeeded" and row.stage == "provider_inventory_observed"
    with context(configured.world):
        _, data = observations.observation(**review(configured, row))
    assert not data["complete"] and all(p["state"] == "UNKNOWN" for p in data["providers"])
    assert len(configured.peer.calls) == 1


def test_ciphertext_from_another_observation_cannot_be_attached_to_this_receipt(configured):
    first, second = reserve(configured), reserve(configured)
    assert run(first) == run(second) == "succeeded"
    NvkeOperation.objects.filter(pk=second.pk).update(
        result_backend_kind=first.result_backend_kind, result_ciphertext=first.result_ciphertext
    )
    second.refresh_from_db()
    with context(configured.world), pytest.raises(NvkeError) as error:
        observations.observation(**review(configured, second))
    assert error.value.code == "SNAPSHOT_UNAVAILABLE"
