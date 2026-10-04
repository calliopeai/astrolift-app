"""Actual activity context reaches SDK metadata threads and fresh source transitions."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread, get_ident
from uuid import uuid4

import grpc
import pytest
from asgiref.sync import sync_to_async
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from gcp import gke_identity_observation as observation
from google.auth.transport.grpc import secure_authorized_channel
from google.oauth2.credentials import Credentials
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from astrolift_lifecycle.deployment_execution_checkpoint import deployment_execution_checkpoint
from astrolift_lifecycle.deployment_execution_receipt import (
    DeploymentExecutionError,
    admitted_deployment_execution,
)
from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt
from astrolift_services.gcp_app_identity_plan import (
    endpoint_app_checkpoint,
    produce_endpoint_app_plan,
    refresh_endpoint_app_sources,
)
from astrolift_services.gcp_identity_source import (
    SourceOperation,
    bootstrap_original_identity,
    original_identity_context,
)
from astrolift_services.models import GCPAppIdentitySource
from astrolift_services.tests.test_gcp_deployment_context_2278 import (
    dispatch_context,
)
from astrolift_services.tests.test_gcp_deployment_context_2278 import (
    source_world as context_world,
)
from astrolift_workflows.activities.deployment_identity_origin import validate_deployment_identity_origin
from astrolift_workflows.inputs import DeployAppInput
from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext

pytestmark = pytest.mark.django_db(transaction=True)


@workflow.defn(name="DeployAppWorkflow")
class NativeCheckpointWorkflow:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        try:
            return await workflow.execute_activity(
                "checkpoint_native_boundary",
                input,
                start_to_close_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            return {"refused": True}


@pytest.fixture
def world(monkeypatch, client, tmp_path):
    return context_world.__wrapped__(monkeypatch, client, tmp_path)


@contextmanager
def bind_and_checkpoint(input):
    try:
        validate_deployment_identity_origin(input)
    except ApplicationError as exc:
        if exc.message != "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED":
            raise
    with deployment_execution_checkpoint(input) as current:
        yield current


async def run_activity(world, client, temporal_env, implementation, *, cancel_started=None):
    await sync_to_async(dispatch_context)(world, client)
    payload = world.starts[0]
    expected = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
        deployment_id=payload.deployment_id,
        kind="EXPECTED",
    )
    queue = "native-checkpoint-" + uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[NativeCheckpointWorkflow],
            activities=[implementation],
            activity_executor=executor,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await temporal_env.client.start_workflow(
                NativeCheckpointWorkflow.run,
                payload,
                id=expected.workflow_id,
                task_queue=queue,
            )
            if cancel_started is not None:
                assert await sync_to_async(cancel_started.wait)(10)
                await handle.cancel()
            try:
                result = await handle.result()
            except WorkflowFailureError:
                if cancel_started is None:
                    raise
                result = {"cancelled": True}
            history = await handle.fetch_history()
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    await Replayer(
        workflows=[NativeCheckpointWorkflow], workflow_runner=UnsandboxedWorkflowRunner()
    ).replay_workflow(history)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "withdraw", ["none", "before_refresh", "after_refresh", "token_after_refresh", "reader", "reader_denied"]
)
async def test_actual_google_auth_grpc_refresh_thread_checks_execution_before_http(
    world,
    client,
    temporal_env,
    monkeypatch,
    tmp_path,
    caplog,
    capsys,
    withdraw,
):
    marker = "private-synthetic-refresh-" + uuid4().hex
    requests = []
    calls = []
    background = []
    completed = []

    class TokenHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(self.path)
            body = json.dumps({"access_token": marker, "expires_in": 3600, "token_type": "Bearer"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    http = ThreadingHTTPServer(("127.0.0.1", 0), TokenHandler)
    http_thread = Thread(target=http.serve_forever, daemon=True)
    http_thread.start()
    owner = []

    def controlled_http(url, *, method, headers, data, context):
        assert url == "https://oauth2.googleapis.com/token" and method == "POST"
        assert get_ident() != owner[0]
        assert connections["default"].connection is None
        background.append(get_ident())
        client_http = HTTPConnection("127.0.0.1", http.server_port, timeout=5)
        try:
            client_http.request(method, "/token", body=data, headers=headers)
            reply = client_http.getresponse()
            result = observation._Response(reply.status, reply.read(), dict(reply.headers))
        finally:
            client_http.close()
        if withdraw in {"after_refresh", "token_after_refresh", "reader_denied"}:
            try:
                if withdraw == "token_after_refresh":
                    type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
                else:
                    Deployment.objects.filter(pk=world.starts[0].deployment_id).update(status="superseded")
            finally:
                connections.close_all()
        return result

    monkeypatch.setattr(observation, "_http", controlled_http)
    server = grpc.server(ThreadPoolExecutor(max_workers=2))

    def echo(body, context):
        calls.append(
            any(k == "authorization" and v == "Bearer " + marker for k, v in context.invocation_metadata())
        )
        return b"owned-response"

    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "Owned",
                {
                    "Read": grpc.unary_unary_rpc_method_handler(echo),
                },
            ),
        )
    )
    cert, key = (tmp_path / "cert.pem").read_bytes(), (tmp_path / "key.pem").read_bytes()
    port = server.add_secure_port("127.0.0.1:0", grpc.ssl_server_credentials(((key, cert),)))
    server.start()

    @activity.defn(name="checkpoint_native_boundary")
    def native(input: DeployAppInput) -> dict:
        owner.append(get_ident())
        with bind_and_checkpoint(input) as current:
            if withdraw == "before_refresh":
                Deployment.objects.filter(pk=input.deployment_id).update(status="superseded")

            def invoke(checkpoint):
                credential = Credentials(
                    token=None,
                    refresh_token="controlled-refresh",
                    client_id="controlled-client",
                    client_secret="controlled-secret",
                    token_uri="https://oauth2.googleapis.com/token",
                )
                request = observation._CredentialRequest(checkpoint)

                def recorded_request(*args, **kwargs):
                    try:
                        return request(*args, **kwargs)
                    finally:
                        completed.append(connections["default"].connection is None)

                channel = secure_authorized_channel(
                    credential,
                    recorded_request,
                    f"127.0.0.1:{port}",
                    ssl_credentials=grpc.ssl_channel_credentials(root_certificates=cert),
                    options=(("grpc.ssl_target_name_override", "source-fixture"),),
                )
                try:
                    try:
                        reply = channel.unary_unary("/Owned/Read")(b"owned-request", timeout=10)
                        return {"received": reply == b"owned-response"}
                    except grpc.RpcError:
                        return {"refused": True}
                finally:
                    channel.close()

            try:
                if withdraw in {"reader", "reader_denied"}:
                    context = DeploymentAuthorityContext(
                        str(Deployment.objects.get(pk=input.deployment_id).guid)
                    )
                    with admitted_deployment_execution(input) as execution:
                        operation = SourceOperation(
                            execution.operation_uuid, execution.workflow_id, execution.run_id
                        )
                    bootstrap_original_identity(
                        input.identity_authority,
                        operation,
                        native_factory=world.factory,
                        deployment_context=context,
                        execution_checkpoint=current,
                    )

                    def reader_factory(declaration):
                        native = world.factory(declaration)
                        observe = native.observe_cluster

                        def refreshed(*, current):
                            if invoke(current) != {"received": True}:
                                raise ValueError("CONTROLLED_REFRESH_REFUSED")
                            return observe(current=current)

                        native.observe_cluster = refreshed
                        return native

                    try:
                        original_identity_context(
                            input.identity_authority,
                            native_factory=reader_factory,
                            deployment_context=context,
                            execution_checkpoint=current,
                        )
                        return {"received": True}
                    except ValueError:
                        return {"refused": True}
                return invoke(current)
            finally:
                connections.close_all()

    try:
        result = await run_activity(world, client, temporal_env, native)
    finally:
        server.stop(0).wait()
        http.shutdown()
        http.server_close()
        http_thread.join(timeout=5)
    assert result == ({"received": True} if withdraw in {"none", "reader"} else {"refused": True})
    refreshes = 2 if withdraw == "reader" else 1
    assert len(requests) == (0 if withdraw == "before_refresh" else refreshes)
    assert calls == ([True] * refreshes if withdraw in {"none", "reader"} else [])
    assert completed == [True] * refreshes
    assert all(thread != owner[0] for thread in background)
    assert marker not in caplog.text and marker not in str([r.__dict__ for r in caplog.records])
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "withdraw", ["none", "before_create", "after_create", "reader", "producer", "refresh"]
)
async def test_actual_source_rpc_and_final_transition_guard_preserves_withdrawn_evidence(
    world,
    client,
    temporal_env,
    withdraw,
):
    flags = []

    @activity.defn(name="checkpoint_native_boundary")
    def native(input: DeployAppInput) -> dict:
        with bind_and_checkpoint(input) as current:
            context = DeploymentAuthorityContext(str(Deployment.objects.get(pk=input.deployment_id).guid))
            with admitted_deployment_execution(input) as execution:
                operation = SourceOperation(execution.operation_uuid, execution.workflow_id, execution.run_id)

            def checkpoint():
                flags.append(connection.in_atomic_block)
                current()

            def after(name):
                selected = (
                    name == "GetServiceAccount" and not world.native.writes
                    if withdraw == "before_create"
                    else name == "CreateServiceAccount"
                    if withdraw == "after_create"
                    else False
                )
                if selected:
                    Deployment.objects.filter(pk=input.deployment_id).update(status="superseded")

            world.native.after = after
            try:
                source = bootstrap_original_identity(
                    input.identity_authority,
                    operation,
                    native_factory=world.factory,
                    deployment_context=context,
                    execution_checkpoint=checkpoint,
                )
                world.source.identity = source.identity
                world.source.account = type(world.source.account)(world.native.account)
                if withdraw == "reader":
                    Deployment.objects.filter(pk=input.deployment_id).update(status="superseded")
                world.native.calls.clear()
                original_identity_context(
                    input.identity_authority,
                    native_factory=world.factory,
                    deployment_context=context,
                    execution_checkpoint=checkpoint,
                )
                if withdraw == "producer":
                    Deployment.objects.filter(pk=input.deployment_id).update(status="superseded")
                plan = produce_endpoint_app_plan(
                    input.identity_authority,
                    source.identity,
                    observer_factory=world.source.observer,
                    deployment_context=context,
                    execution_checkpoint=checkpoint,
                )
                endpoint_app_checkpoint(plan, execution_checkpoint=checkpoint)()
                if withdraw == "refresh":

                    def after_source(name):
                        if name == "GetRole":
                            Deployment.objects.filter(pk=input.deployment_id).update(status="superseded")

                    world.source.after = after_source
                refresh_endpoint_app_sources(
                    plan,
                    observer_factory=world.source.observer,
                    execution_checkpoint=checkpoint,
                )
                return {"observed": True}
            except (ValueError, PermissionError):
                return {"refused": True}
            finally:
                connections.close_all()

    result = await run_activity(world, client, temporal_env, native)
    assert result == ({"observed": True} if withdraw == "none" else {"refused": True})
    row = await sync_to_async(GCPAppIdentitySource.objects.get)()
    assert row.state == (
        "UNSENT" if withdraw == "before_create" else "EVIDENCE" if withdraw == "after_create" else "OBSERVED"
    )
    assert bool(row.unique_id) == (withdraw != "before_create")
    assert world.native.writes == (0 if withdraw == "before_create" else 1)
    if withdraw == "reader":
        assert not world.native.calls
    assert True in flags and False in flags


@pytest.mark.asyncio
async def test_bound_callback_under_source_parent_locks_never_waits_for_expected(world, client, temporal_env):
    held, release = Event(), Event()
    errors = []

    @activity.defn(name="checkpoint_native_boundary")
    def native(input: DeployAppInput) -> dict:
        with bind_and_checkpoint(input) as current:
            expected = DeploymentExecutionReceipt.objects.get(
                deployment_id=input.deployment_id, kind="EXPECTED"
            )

            def hold():
                db = connection.Database.connect(**connection.get_connection_params())
                try:
                    with db.cursor() as cursor:
                        cursor.execute(
                            "SELECT id FROM astrolift_lifecycle_deploymentexecutionreceipt WHERE id=%s FOR UPDATE",
                            [expected.pk],
                        )
                    held.set()
                    assert release.wait(30)
                    db.rollback()
                except BaseException as exc:
                    errors.append(type(exc).__name__)
                finally:
                    db.close()

            holder = Thread(target=hold)
            holder.start()
            assert held.wait(5)
            try:
                with admitted_deployment_execution(input) as execution:
                    operation = SourceOperation(
                        execution.operation_uuid, execution.workflow_id, execution.run_id
                    )
                context = DeploymentAuthorityContext(str(Deployment.objects.get(pk=input.deployment_id).guid))
                locks = []

                def checkpoint():
                    if connection.in_atomic_block:
                        db = connection.Database.connect(**connection.get_connection_params())
                        try:
                            with db.cursor() as cursor:
                                try:
                                    cursor.execute(
                                        "SELECT id FROM astrolift_registry_registeredapp WHERE id=%s FOR UPDATE NOWAIT",
                                        [input.registered_app_id],
                                    )
                                except Exception as exc:
                                    assert getattr(exc, "pgcode", None) == "55P03"
                                    locks.append(True)
                                else:
                                    pytest.fail("source final callback must follow parent locks")
                        finally:
                            db.rollback()
                            db.close()
                    current()

                bootstrap_original_identity(
                    input.identity_authority,
                    operation,
                    native_factory=world.factory,
                    deployment_context=context,
                    execution_checkpoint=checkpoint,
                )
                assert locks and not release.is_set()
                return {"observed": True}
            finally:
                release.set()
                holder.join(timeout=5)
                connections.close_all()

    result = await run_activity(world, client, temporal_env, native)
    assert result == {"observed": True} and not errors


def test_scope_requires_real_activity_before_any_database_admission():
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(RuntimeError):
            with deployment_execution_checkpoint(None):
                pytest.fail("outside-activity capture must refuse")
    assert not queries


@pytest.mark.asyncio
async def test_scoped_callback_refuses_after_exit_on_owner_and_metadata_thread(world, client, temporal_env):
    @activity.defn(name="checkpoint_native_boundary")
    def native(input: DeployAppInput) -> dict:
        with bind_and_checkpoint(input) as current:
            current()
        with CaptureQueriesContext(connection) as queries:
            with pytest.raises(DeploymentExecutionError):
                current()
        assert not queries
        outcomes = []

        def late():
            try:
                with CaptureQueriesContext(connections["default"]) as queries:
                    with pytest.raises(DeploymentExecutionError):
                        current()
                    outcomes.append(len(queries))
            finally:
                connections.close_all()

        thread = Thread(target=late)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive() and outcomes == [0]
        connections.close_all()
        return {"closed": True}

    assert await run_activity(world, client, temporal_env, native) == {"closed": True}


@pytest.mark.asyncio
async def test_actual_temporal_cancellation_refuses_scoped_and_bare_execution(world, client, temporal_env):
    started, observed = Event(), Event()
    outcomes = []

    @activity.defn(name="checkpoint_native_boundary", no_thread_cancel_exception=True)
    def native(input: DeployAppInput) -> dict:
        try:
            with bind_and_checkpoint(input) as current:
                started.set()
                activity.wait_for_cancelled_sync(timeout=10)
                assert activity.is_cancelled()
                with CaptureQueriesContext(connection) as queries:
                    with pytest.raises(DeploymentExecutionError):
                        current()
                    with pytest.raises(DeploymentExecutionError):
                        with admitted_deployment_execution(input):
                            pytest.fail("cancelled direct execution must refuse")
                assert not queries
                outcomes.append("refused")
                observed.set()
                return {"refused": True}
        finally:
            connections.close_all()

    result = await run_activity(world, client, temporal_env, native, cancel_started=started)
    assert result in ({"refused": True}, {"cancelled": True})
    assert observed.is_set() and outcomes == ["refused"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_atomic", [False, True])
async def test_metadata_scope_preserves_foreign_connection_and_transaction(
    world, client, temporal_env, outer_atomic
):
    from contextlib import nullcontext

    from django.db import transaction

    @activity.defn(name="checkpoint_native_boundary")
    def native(input: DeployAppInput) -> dict:
        outcomes = []
        with bind_and_checkpoint(input) as current:

            def foreign():
                db = connections["default"]
                try:
                    with transaction.atomic() if outer_atomic else nullcontext():
                        db.ensure_connection()
                        original = db.connection
                        with CaptureQueriesContext(db) as queries:
                            with pytest.raises(DeploymentExecutionError):
                                current()
                            with pytest.raises(DeploymentExecutionError):
                                current.compose(lambda: None)()
                        assert not queries and db.connection is original
                        assert db.in_atomic_block == outer_atomic
                        with db.cursor() as cursor:
                            cursor.execute("SELECT 1")
                            assert cursor.fetchone() == (1,)
                        outcomes.append(True)
                finally:
                    db.close()

            thread = Thread(target=foreign)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive() and outcomes == [True]
        connections.close_all()
        return {"preserved": True}

    assert await run_activity(world, client, temporal_env, native) == {"preserved": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_atomic", [False, True])
async def test_metadata_cleanup_never_sweeps_unrelated_database_alias(
    world,
    client,
    temporal_env,
    outer_atomic,
    django_db_blocker,
):
    from contextlib import nullcontext

    from django.db import transaction

    alias = "owned_callback_secondary"
    connections.databases[alias] = dict(connections.databases["default"])
    outcomes = []

    @activity.defn(name="checkpoint_native_boundary")
    def native(input: DeployAppInput) -> dict:
        with bind_and_checkpoint(input) as current:

            def unrelated():
                secondary = connections[alias]
                try:
                    with django_db_blocker.unblock():
                        with transaction.atomic(using=alias) if outer_atomic else nullcontext():
                            secondary.ensure_connection()
                            original = secondary.connection
                            # The secondary is explicitly expired; a global
                            # close_old_connections sweep would close/break it.
                            secondary.close_at = 0
                            current.compose(lambda: None)()
                            assert connections["default"].connection is None
                            assert secondary.connection is original
                            assert secondary.in_atomic_block == outer_atomic
                            with secondary.cursor() as cursor:
                                cursor.execute("SELECT 1")
                                assert cursor.fetchone() == (1,)
                            outcomes.append(True)
                finally:
                    secondary.close()
                    connections["default"].close()
                    del connections[alias]

            thread = Thread(target=unrelated)
            thread.start()
            thread.join(timeout=10)
            assert not thread.is_alive() and outcomes == [True]
        connections.close_all()
        return {"preserved": True}

    try:
        assert await run_activity(world, client, temporal_env, native) == {"preserved": True}
    finally:
        connections.databases.pop(alias)
