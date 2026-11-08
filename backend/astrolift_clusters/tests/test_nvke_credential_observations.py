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

from astrolift_clusters import nvke_credential_observations as observations
from astrolift_clusters import nvke_metrics as metrics
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeMetricsAuthority, NvkeOperation
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_access import controller, create_request
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import reserve as controller_reserve
from astrolift_clusters.tests.test_nvke_operations import world as _world
from astrolift_clusters.tests.test_nvke_sites import context, put
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db


def sample(name, value, **labels):
    encoded = ",".join(f"{key}={json.dumps(value)}" for key, value in labels.items())
    return "nvke_controller_" + name + ("{" + encoded + "}" if encoded else "") + f" {value}\n"


@pytest.fixture
def configured():
    return _configured.__wrapped__(_world.__wrapped__())


@pytest.fixture
def listener():
    peer = SimpleNamespace(body=sample("credential_metrics_collection_success", 0), calls=[], status=200)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            peer.calls.append((self.path, self.headers.get("Authorization")))
            body = peer.body.encode()
            self.send_response(peer.status)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if getattr(peer, "after", None):
                peer.after()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer.url = f"http://127.0.0.1:{server.server_port}/private-metrics"
    try:
        yield peer
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def configure(state, listener, **changes):
    state.site.refresh_from_db()
    current = NvkeMetricsAuthority.objects.filter(site=state.site).first()
    args = {
        "site_id": state.site.guid,
        "expected_actor_id": state.world.user.pk,
        "expected_site_version": state.site.version,
        "expected_version": current.version if current else 0,
        "endpoint": listener.url,
        "allow_http": True,
        "credentials": {"token": "PRIVATE_METRICS_TOKEN"},
    }
    args.update(changes)
    with context(state.world):
        row = metrics.configure(**args)
    state.site.refresh_from_db()
    return row


def execute(row):
    result = runtime.attempt(
        row.pk, row.generation, execution=(row.workflow_id, "local-read", "NvkeOperationWorkflow")
    )
    row.refresh_from_db()
    return result


@pytest.fixture
def ready(configured, listener):
    state = configured
    state.metrics = configure(state, listener)
    state.overrides = {"conditions": {"provisioned": {"status": "True"}}}
    request = create_request()
    with controller(request, subresources={"parent_override": state.overrides}) as (
        endpoint,
        identity,
        calls,
    ):
        with context(state.world):
            state.connection = put(
                state.site,
                expected_connection_version=state.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
        state.site.refresh_from_db()
        state.source = controller_reserve(
            state, operation_key="getCluster", parameters={"path": {"clusterID": identity}}
        )
        assert execute(state.source) == "succeeded"
        state.cluster_id, state.name, state.calls = identity, request["body"]["name"], calls
        node = {"tenant_cluster": state.name, "node": "cp-1"}
        listener.body += "".join(
            [
                sample("credential_monitoring_enabled", 0),
                sample("certificate_inspection_available", 0, **node),
                sample("certificate_observation_age_seconds", "100.25", **node),
                sample("certificate_required", 0, **node, credential="super-admin"),
                sample("certificate_observation_available", 1, **node, credential="super-admin"),
                sample("certificate_not_after_timestamp_seconds", 1, **node, credential="super-admin"),
                sample("serving_csr_count", "9007199254740993", tenant_cluster=state.name, state="pending"),
                sample(
                    "certificate_inspection_available",
                    1,
                    tenant_cluster="PRIVATE_FOREIGN",
                    node="PRIVATE_NODE",
                ),
            ]
        )
        yield state


def admission(state, **changes):
    args = {
        "site_id": state.site.guid,
        "expected_actor_id": state.world.user.pk,
        "expected_site_version": state.site.version,
        "expected_authority_version": state.metrics.version,
        "source_operation_id": state.source.guid,
        "expected_source_version": state.source.version,
        "request_id": uuid4(),
    }
    args.update(changes)
    return args


def review(state, row):
    return {
        "site_id": state.site.guid,
        "operation_id": row.guid,
        "expected_actor_id": state.world.user.pk,
        "expected_version": row.version,
    }


def reserve(state, **changes):
    with context(state.world):
        return observations.reserve(**admission(state, **changes))


def test_authority_credentials_are_separate_encrypted_preserved_and_explicitly_cleared(configured, listener):
    row = configure(configured, listener)
    assert b"PRIVATE_METRICS_TOKEN" not in bytes(row.credential_ciphertext)
    assert sites.credentials_for(row) == {"token": "PRIVATE_METRICS_TOKEN"}
    ciphertext = bytes(row.credential_ciphertext)
    row = configure(configured, listener, credentials=None)
    assert bytes(row.credential_ciphertext) == ciphertext
    row = configure(configured, listener, credentials={})
    assert sites.credentials_for(row) == {}
    assert sites.credentials_for(configured.connection) == {"token": "PRIVATE_NVKE_TOKEN"}
    with context(configured.world):
        row = metrics.verify(
            site_id=configured.site.guid,
            expected_actor_id=configured.world.user.pk,
            expected_site_version=configured.site.version,
            expected_version=row.version,
        )
    assert row.verification_code == "METRICS_READ" and row.verified_at is not None
    assert listener.calls == [("/private-metrics", None)]


@pytest.mark.parametrize(
    "change",
    [
        {"allow_http": False},
        {"endpoint": "http://secret:PRIVATE@localhost/"},
        {"timeout_seconds": True},
        {"timeout_seconds": 301},
        {"expected_version": 1},
        {"expected_actor_id": -1},
    ],
)
def test_invalid_authority_never_creates_a_row(configured, listener, change):
    with pytest.raises(sites.NvkeSiteError):
        configure(configured, listener, **change)
    assert not NvkeMetricsAuthority.objects.exists()
    assert not listener.calls


def test_authority_disable_remove_replace_and_foreign_access(configured, listener):
    row = configure(configured, listener)

    def authority_review():
        return {
            "site_id": configured.site.guid,
            "expected_actor_id": configured.world.user.pk,
            "expected_site_version": configured.site.version,
            "expected_version": row.version,
        }

    with context(configured.world):
        row = metrics.set_enabled(**authority_review(), enabled=False)
        configured.site.refresh_from_db()
        with pytest.raises(sites.NvkeSiteError) as disabled:
            metrics.verify(**authority_review())
        assert disabled.value.code == "SOURCE_CHANGED"
        row = metrics.set_enabled(**authority_review(), remove=True)
        configured.site.refresh_from_db()
        assert row.deleted_at and row.deleted_by == configured.world.user and not row.is_enabled
        assert metrics.read(configured.site.guid) is None
    replacement = configure(configured, listener)
    assert replacement.guid != row.guid
    assert NvkeMetricsAuthority.all_objects.filter(site=configured.site).count() == 2
    other = _world.__wrapped__()
    with context(other), pytest.raises(sites.NvkeSiteError) as foreign:
        metrics.read(configured.site.guid)
    assert foreign.value.code == "NOT_FOUND" and not listener.calls


@pytest.mark.parametrize("credentials", ["trusted", "untrusted", "invalid-client-pair"])
def test_real_tls_uses_explicit_ca_and_refuses_invalid_client_material(configured, tmp_path, credentials):
    from tests.nvke.conftest import peer as peer_fixture

    server = peer_fixture.__wrapped__(SimpleNamespace(param=True), tmp_path)
    peer = next(server)
    try:
        peer.body = sample("credential_metrics_collection_success", 0).encode()
        peer.headers = {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"}
        secrets = {"token": "PRIVATE_METRICS_TOKEN"}
        if credentials != "untrusted":
            secrets["ca_pem"] = (tmp_path / "cert.pem").read_text()
        if credentials == "invalid-client-pair":
            secrets.update(
                client_certificate_pem="PRIVATE_INVALID_CERT", client_key_pem="PRIVATE_INVALID_KEY"
            )
        row = configure(
            configured, SimpleNamespace(url=peer.url + "/metrics"), allow_http=False, credentials=secrets
        )
        with context(configured.world):
            args = {
                "site_id": configured.site.guid,
                "expected_actor_id": configured.world.user.pk,
                "expected_site_version": configured.site.version,
                "expected_version": row.version,
            }
            if credentials == "trusted":
                assert metrics.verify(**args).verification_code == "METRICS_READ"
                assert peer.requests[0]["headers"]["Authorization"] == "Bearer PRIVATE_METRICS_TOKEN"
            else:
                with pytest.raises((NvkeError, sites.NvkeSiteError)) as refused:
                    metrics.verify(**args)
                assert "PRIVATE" not in str(refused.value)
                assert not peer.requests
    finally:
        server.close()


def test_cached_admin_and_revoked_api_token_are_rechecked(configured, listener):
    from astrolift_identity import abac
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken, RoleBinding
    from core.permissions import PermissionDenied

    issued = mint_token()
    token = ApiToken.objects.create(
        user=configured.world.user,
        organization=configured.world.org,
        name="metrics admin",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["admin:nvke"],
    )
    mark = set_current_api_token(token)
    try:
        with (
            context(configured.world),
            abac.request_attributes(abac.attributes_for(configured.world.user.pk)),
        ):
            metrics.actor(configured.world.user.pk)
            ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
            with pytest.raises(sites.NvkeSiteError) as refused:
                metrics.actor(configured.world.user.pk)
            assert refused.value.code == "AUTHORITY_REVOKED"
            ApiToken.objects.filter(pk=token.pk).update(is_revoked=False)
            RoleBinding.objects.filter(user=configured.world.user).update(deleted_at=timezone.now())
            with pytest.raises(PermissionDenied):
                metrics.actor(configured.world.user.pk)
    finally:
        reset_current_api_token(mark)
    assert not NvkeMetricsAuthority.objects.exists() and not listener.calls


def test_observation_is_source_bound_private_exact_and_historical(ready, listener):
    row = reserve(ready)
    assert ready.name not in str(row.authority)
    assert ready.name.encode() not in bytes(row.input_ciphertext)
    assert execute(row) == "succeeded"
    assert listener.calls == [("/private-metrics", "Bearer PRIVATE_METRICS_TOKEN")]
    assert len(ready.calls) == 3
    result = operations.decode_result(row)
    assert "PRIVATE_FOREIGN" not in json.dumps(result) and "tenant_cluster" not in json.dumps(result)
    assert b"cp-1" not in bytes(row.result_ciphertext)
    with context(ready.world):
        _, detail = observations.observation(**review(ready, row))
        assert detail["cluster_id"] == ready.cluster_id
        assert detail["collection_succeeded"] is False and detail["monitoring_enabled"] is False
        assert detail["renewal_automation_enabled"] is None and detail["cluster_reported"] is True
        _, certs, _, total, _ = observations.page(kind="certificates", **review(ready, row))
        assert total == 1
        assert certs[0]["required"] is False and certs[0]["latest_inspection_available"] is False
        assert certs[0]["observation_available"] is True and certs[0]["reported_expired_at_scrape"] is True
        _, samples, _, _, _ = observations.page(search="serving_csr", **review(ready, row))
        assert samples[0]["value"] == "9007199254740993"
    configure(ready, listener)
    with context(ready.world):
        assert observations.observation(**review(ready, row))[1]["cluster_id"] == ready.cluster_id
    assert len(listener.calls) == 1


@pytest.mark.parametrize("change", ["disable", "rotate", "source", "membership", "cancel", "deadline"])
def test_revoked_observation_never_scrapes(ready, listener, change):
    row = reserve(ready)
    if change == "disable":
        NvkeMetricsAuthority.objects.filter(pk=ready.metrics.pk).update(is_enabled=False)
    elif change == "rotate":
        NvkeMetricsAuthority.objects.filter(pk=ready.metrics.pk).update(
            credential_ciphertext=b"changed-without-version"
        )
    elif change == "source":
        ready.source.save()
    elif change == "membership":
        ready.world.member.soft_delete()
    elif change == "cancel":
        with context(ready.world):
            operations.request_cancel(row.guid)
    else:
        NvkeOperation.objects.filter(pk=row.pk).update(deadline=timezone.now() - timedelta(seconds=1))
    assert execute(row) == ("cancelled" if change == "cancel" else "failed")
    assert not listener.calls and len(ready.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"name": "reused-name"},
        {"id": str(uuid4())},
        {"conditions": {"provisioned": {"status": "False"}}},
        {"conditions": {"provisioned": {"status": "True"}, "deleting": {"status": "True"}}},
        {"conditions": {"provisioned": {"status": "True"}, "deleting": "malformed"}},
    ],
)
def test_live_controller_must_still_prove_uuid_name_and_provisioned_state(ready, listener, change):
    row = reserve(ready)
    ready.overrides.update(change)
    assert execute(row) == "failed" and row.error_code == "SOURCE_CHANGED"
    assert not listener.calls


def test_receipt_lookup_precedes_changed_authority_and_rejects_changed_request(ready, listener):
    args = admission(ready)
    with context(ready.world):
        row = observations.reserve(**args)
    configure(ready, listener)
    with context(ready.world):
        assert observations.reserve(**args).pk == row.pk
        with pytest.raises(NvkeError) as error:
            observations.reserve(**{**args, "timeout_seconds": 60})
        assert error.value.code == "CONFLICT"
    assert NvkeOperation.objects.filter(operation_key=observations.KEY).count() == 1


def test_controller_identity_change_during_scrape_prevents_publication(ready, listener):
    row = reserve(ready)
    listener.after = lambda: ready.overrides.update(name="replacement-cluster")
    assert execute(row) == "failed" and row.error_code == "SOURCE_CHANGED"
    assert len(listener.calls) == 1 and len(ready.calls) == 3
    assert "samples" not in operations.decode_result(row)


def test_absent_cluster_and_failed_collection_remain_explicit_unknowns(ready, listener):
    listener.body = sample("credential_metrics_collection_success", 0)
    row = reserve(ready)
    assert execute(row) == "succeeded"
    with context(ready.world):
        _, detail = observations.observation(**review(ready, row))
        assert detail["cluster_reported"] is False and detail["collection_succeeded"] is False
        assert detail["monitoring_enabled"] is None
        assert observations.page(kind="certificates", **review(ready, row))[3] == 0


def test_graphql_credentials_and_invalid_endpoint_are_audit_redacted(configured, listener):
    from astrolift_clusters.tests.test_nvke_schema import execute as gql
    from core.schema.audit import MutationAuditLog

    query = """mutation($unrelated:ConfigureNvkeMetricsAuthorityInput!){configureNvkeMetricsAuthority(input:$unrelated){ok errors{code} data{id version siteVersion hasCredentials}}}"""
    args = {
        "siteId": str(configured.site.guid),
        "expectedActorId": configured.world.user.pk,
        "expectedSiteVersion": configured.site.version,
        "expectedVersion": 0,
        "endpoint": listener.url,
        "allowHttp": True,
        "credentials": {"token": "PRIVATE_METRICS_TOKEN"},
    }
    result = gql(configured.world, query, {"unrelated": args})
    assert result.errors is None and result.data["configureNvkeMetricsAuthority"]["ok"]
    data = result.data["configureNvkeMetricsAuthority"]["data"]
    args.update(
        expectedSiteVersion=data["siteVersion"],
        expectedVersion=data["version"],
        endpoint="https://PRIVATE_USER:PRIVATE_KEY@invalid/",
    )
    rejected = gql(configured.world, query, {"unrelated": args})
    assert rejected.errors is None and not rejected.data["configureNvkeMetricsAuthority"]["ok"]
    logs = MutationAuditLog.objects.filter(operation="nvke.metrics_configure")
    assert logs.count() == 2
    for log in logs:
        assert "PRIVATE" not in str(log.variables)
        assert log.variables["unrelated"]["credentials"] == "***REDACTED***"
        assert log.variables["unrelated"]["endpoint"] == "***REDACTED***"


def test_graphql_observation_and_typed_pages_work_with_read_only_scope(ready):
    from astrolift_clusters.tests.test_nvke_schema import execute as gql
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken
    from core.permissions import PermissionDenied

    row = reserve(ready)
    assert execute(row) == "succeeded"
    issued = mint_token()
    token = ApiToken.objects.create(
        user=ready.world.user,
        organization=ready.world.org,
        name="credential reader",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:nvke"],
    )
    mark = set_current_api_token(token)
    try:
        query = """query($site:GUID!,$op:GUID!,$actor:Int!,$version:Int!){
          astroliftNvkeCredentialObservation(siteId:$site,operationId:$op,expectedActorId:$actor,expectedVersion:$version){ok errors{code} data{clusterId collectionSucceeded monitoringEnabled renewalAutomationEnabled}}
          astroliftNvkeCredentialCertificatesPage(siteId:$site,operationId:$op,expectedActorId:$actor,expectedVersion:$version){ok errors{code} data{totalCount items{node credential latestInspectionAvailable observationAvailable reportedExpiredAtScrape}}}
          astroliftNvkeCredentialMetricsPage(siteId:$site,operationId:$op,expectedActorId:$actor,expectedVersion:$version,search:"serving_csr"){ok errors{code} data{totalCount items{metric value}}}
        }"""
        result = gql(
            ready.world,
            query,
            {
                "site": str(ready.site.guid),
                "op": str(row.guid),
                "actor": ready.world.user.pk,
                "version": row.version,
            },
        )
        assert result.errors is None
        for payload in result.data.values():
            assert payload["ok"], payload
        assert result.data["astroliftNvkeCredentialObservation"]["data"]["collectionSucceeded"] is False
        assert (
            result.data["astroliftNvkeCredentialMetricsPage"]["data"]["items"][0]["value"]
            == "9007199254740993"
        )
        with context(ready.world), pytest.raises(PermissionDenied):
            operations.result(row.guid)
        with context(ready.world), pytest.raises(PermissionDenied):
            observations.reserve(**admission(ready))
    finally:
        reset_current_api_token(mark)


def test_graphql_admission_returns_original_durable_receipt_after_authority_rotation(ready, listener):
    from astrolift_clusters.tests.test_nvke_schema import execute as gql

    query = """mutation($input:RequestNvkeCredentialObservationInput!){requestNvkeCredentialObservation(input:$input){ok errors{code} data{id version operationKey status requestId isInventory}}}"""
    args = {
        "siteId": str(ready.site.guid),
        "expectedActorId": ready.world.user.pk,
        "expectedSiteVersion": ready.site.version,
        "expectedAuthorityVersion": ready.metrics.version,
        "sourceOperationId": str(ready.source.guid),
        "expectedSourceVersion": ready.source.version,
        "requestId": str(uuid4()),
    }
    result = gql(ready.world, query, {"input": args})
    assert result.errors is None and result.data["requestNvkeCredentialObservation"]["ok"]
    receipt = result.data["requestNvkeCredentialObservation"]["data"]
    assert receipt["operationKey"] == observations.KEY and receipt["isInventory"] is False
    configure(ready, listener)
    replay = gql(ready.world, query, {"input": args})
    assert (
        replay.errors is None
        and replay.data["requestNvkeCredentialObservation"]["data"]["id"] == receipt["id"]
    )
    assert not listener.calls


@pytest.mark.parametrize(
    "changed", ["missing_authority", "wrong_source", "wrong_source_version", "foreign_site"]
)
def test_admission_refuses_unproven_source_without_metrics_io(ready, listener, changed):
    changes = {}
    if changed == "missing_authority":
        ready.metrics.soft_delete()
    elif changed == "wrong_source":
        changes["source_operation_id"] = uuid4()
    elif changed == "wrong_source_version":
        changes["expected_source_version"] = ready.source.version - 1
    else:
        from astrolift_clusters.tests.test_nvke_sites import create

        other = _world.__wrapped__()
        with context(other):
            changes["site_id"] = create().guid
    with pytest.raises((NvkeError, sites.NvkeSiteError)):
        reserve(ready, **changes)
    assert not NvkeOperation.objects.filter(operation_key=observations.KEY).exists()
    assert not listener.calls


@pytest.mark.django_db(transaction=True)
async def test_real_temporal_observation_keeps_selected_metrics_and_credentials_out_of_history(
    configured, listener, monkeypatch
):
    import asyncio

    from asgiref.sync import sync_to_async

    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
    from core.testing.temporal import temporal_test_env, temporal_worker

    state = configured
    state.metrics = await sync_to_async(configure)(state, listener)
    request = create_request()
    with controller(
        request, subresources={"parent_override": {"conditions": {"provisioned": {"status": "True"}}}}
    ) as (endpoint, identity, calls):

        def connect():
            with context(state.world):
                state.connection = put(
                    state.site,
                    expected_connection_version=state.connection.version,
                    endpoint=endpoint,
                    allow_http=True,
                )
            state.site.refresh_from_db()

        await sync_to_async(connect)()
        listener.body += sample(
            "certificate_inspection_available",
            0,
            tenant_cluster=request["body"]["name"],
            node="PRIVATE_SELECTED_NODE",
        )
        async with temporal_test_env() as env:

            async def get_client():
                return env.client

            monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
            monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
            monkeypatch.setattr(
                workflow_client, "_task_queue", lambda: "nvke-credential-" + str(state.site.guid)
            )
            async with temporal_worker(
                env,
                task_queue=workflow_client._task_queue(),
                workflows=[NvkeOperationWorkflow],
                activities=[nvke_operation_attempt],
            ):
                state.source = await sync_to_async(controller_reserve)(
                    state, operation_key="getCluster", parameters={"path": {"clusterID": identity}}
                )
                source_handle = env.client.get_workflow_handle(state.source.workflow_id)
                assert await asyncio.wait_for(source_handle.result(), 30) == "succeeded"
                await sync_to_async(state.source.refresh_from_db)()
                row = await sync_to_async(reserve)(state)
                handle = env.client.get_workflow_handle(row.workflow_id)
                assert await asyncio.wait_for(handle.result(), 30) == "succeeded"
                await sync_to_async(row.refresh_from_db)()
                assert row.workflow_run_id and row.finished_at
                assert len(listener.calls) == 1 and len(calls) == 3
                result = await sync_to_async(operations.decode_result)(row)
                assert "PRIVATE_SELECTED_NODE" in json.dumps(result)
                history = await handle.fetch_history()
                raw = history.to_json()
                for private in (
                    "PRIVATE_SELECTED_NODE",
                    "PRIVATE_METRICS_TOKEN",
                    request["body"]["name"],
                    "samples",
                    "cluster_name",
                ):
                    assert private not in raw
                started = history.events[0].workflow_execution_started_event_attributes
                assert await env.client.data_converter.decode(started.input.payloads) == [row.pk, 0]


def test_page_cursors_bind_view_filter_size_and_observation_version(ready):
    row = reserve(ready)
    assert execute(row) == "succeeded"
    with context(ready.world):
        _, first, cursor, total, _ = observations.page(limit=2, **review(ready, row))
        _, second, _, repeated_total, _ = observations.page(limit=2, after=cursor, **review(ready, row))
        assert total == repeated_total and total > 4
        assert not {item["key"] for item in first} & {item["key"] for item in second}
        for changes in ({"limit": 3}, {"search": "cp"}, {"kind": "certificates"}):
            with pytest.raises(NvkeError) as error:
                observations.page(after=cursor, **{"limit": 2, **changes}, **review(ready, row))
            assert error.value.code == "INVALID_CURSOR"
        with pytest.raises(NvkeError) as error:
            observations.page(**{**review(ready, row), "expected_version": row.version - 1})
        assert error.value.code == "SNAPSHOT_UNAVAILABLE"


@pytest.mark.parametrize("corruption", [None, [], {"name": "PRIVATE"}, "PRIVATE"])
def test_malformed_saved_samples_are_refused_without_payload_disclosure(ready, corruption):
    row = reserve(ready)
    assert execute(row) == "succeeded"
    payload = operations.decode_result(row)
    payload["samples"] = [corruption]
    sealed = encrypt_at_rest(operations.canonical(payload))
    NvkeOperation.objects.filter(pk=row.pk).update(
        result_backend_kind=sealed.backend_kind, result_ciphertext=sealed.backend_ref
    )
    with context(ready.world), pytest.raises(NvkeError) as error:
        observations.page(**review(ready, row))
    assert error.value.code == "SNAPSHOT_UNAVAILABLE" and "PRIVATE" not in str(error.value)
