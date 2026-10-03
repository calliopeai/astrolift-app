"""Native Tempo HTTP and real PostgreSQL admission/placement proofs."""

import datetime as dt
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.schema.log_queries import LogHistoryQuery
from astrolift_observability.schema.scoped_trace_queries import ScopedTracesQuery
from astrolift_observability.scoped_traces import _attributes, _scope
from core.permissions import Permission, PermissionDenied
from core.schema.enums import ObservabilityPanelReason as Reason
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db
TRACE = "a" * 32
FOREIGN = "b" * 32


@pytest.fixture(autouse=True)
def no_index(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, p: None))


@pytest.fixture
def tempo():
    state = {"requests": [], "ids": [TRACE], "body": {}, "failure": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            state["requests"].append((parsed.path, parse_qs(parsed.query)))
            if state["failure"]:
                self.send_response(503)
                self.end_headers()
                return
            body = (
                {
                    "traces": [
                        {
                            "traceID": identity,
                            "rootServiceName": "FOREIGN_ROOT",
                            "rootTraceName": "FOREIGN_OPERATION",
                            "spanCount": 999,
                        }
                        for identity in state["ids"]
                    ]
                }
                if parsed.path == "/api/search"
                else state["body"]
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["endpoint"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def target(tempo):
    world = ScopeWorld("explorers")
    actor = make_user("explorers")
    bind_role(
        actor,
        permissions=[Permission.APP_READ, Permission.APP_READ_LOGS],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="explorers-own",
    )
    cluster = make_cluster(world, "explorers")
    cluster.provider_config = {
        "trace_driver": "tempo",
        "trace_config": {"endpoint": tempo["endpoint"], "attribution": "collector-resource-v1"},
    }
    cluster.save(update_fields=["provider_config"])
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=cluster, name="prod", k8s_namespace="owned-production"
    )
    return world, actor, cluster, env


def batch(attrs, *, name="OWNED_OPERATION", span="1" * 16, parent=None, span_attrs=None, trace=TRACE):
    return {
        "resource": {
            "attributes": [{"key": key, "value": {"stringValue": value}} for key, value in attrs.items()]
        },
        "scopeSpans": [
            {
                "spans": [
                    {
                        "traceId": trace,
                        "spanId": span,
                        "parentSpanId": parent,
                        "name": name,
                        "startTimeUnixNano": "1000000000",
                        "endTimeUnixNano": "1005000000",
                        "status": {"code": 1},
                        "attributes": [
                            {"key": key, "value": {"stringValue": value}}
                            for key, value in (span_attrs or {}).items()
                        ],
                    }
                ]
            }
        ],
    }


def context(target):
    world, actor, _, _ = target
    return tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk))


def window():
    end = int(dt.datetime.now(dt.UTC).timestamp())
    return {"since": str(end - 3600), "until": str(end)}


def read(target, **kwargs):
    world, actor, _, env = target
    with context(target):
        return ScopedTracesQuery().astrolift_app_trace_page(
            make_info(actor), app_slug=world.medops_app.slug, environment_name=env.name, **window(), **kwargs
        )


def detail(target):
    world, actor, _, env = target
    with context(target):
        return ScopedTracesQuery().astrolift_trace_spans_result(
            make_info(actor),
            app_slug=world.medops_app.slug,
            environment_name=env.name,
            trace_id=TRACE,
            **window(),
        )


def test_native_scoped_search_projects_mixed_trace_and_never_exposes_foreign_root(target, tempo):
    world, _, _, env = target
    attrs = {**_attributes(_scope(world.medops_app, env)), "service.name": "OWNED_SERVICE"}
    foreign = {**attrs, "astrolift.app.id": str(world.platform_app.guid)}
    tempo["body"] = {
        "resourceSpans": [
            batch(foreign, name="FOREIGN_OPERATION", span="2" * 16),
            batch(attrs, parent="2" * 16, span_attrs={"literal": "preserved"}),
        ]
    }
    result = read(target, service='OWNED_SERVICE" || true')
    assert result.reason == Reason.OK
    assert result.scope.namespace == "owned-production" and result.scope.app_id == str(world.medops_app.guid)
    assert [
        (row.root_service, row.root_operation, row.span_count, row.duration_ms) for row in result.items
    ] == [("OWNED_SERVICE", "OWNED_OPERATION", 1, 5.0)]
    query = tempo["requests"][0][1]
    for key, value in _attributes(_scope(world.medops_app, env)).items():
        assert f'resource."{key}" = "{value}"' in query["q"][0]
    assert json.dumps('OWNED_SERVICE" || true') in query["q"][0]
    assert query["limit"] == ["11"]
    spans = detail(target)
    assert spans.reason == Reason.OK and len(spans.items) == 1
    assert spans.items[0].parent_span_id is None and spans.items[0].attributes == {"literal": "preserved"}
    assert spans.items[0].resource_attributes == attrs
    assert tempo["requests"][-2][0] == "/api/search"
    assert f'trace:id = "{TRACE}"' in tempo["requests"][-2][1]["q"][0]


@pytest.mark.parametrize(
    "identity",
    [
        "astrolift.organization.id",
        "astrolift.app.id",
        "astrolift.environment.id",
        "astrolift.cluster.id",
        "k8s.namespace.name",
    ],
)
def test_wrong_or_span_spoofed_resource_identity_is_excluded(target, tempo, identity):
    world, _, _, env = target
    attrs = _attributes(_scope(world.medops_app, env))
    tempo["body"] = {
        "resourceSpans": [
            batch({**attrs, identity: "foreign"}, span_attrs=attrs),
            batch({}, span="2" * 16, span_attrs=attrs),
        ]
    }
    result = read(target)
    assert result.reason == Reason.ERROR and result.items == []
    assert detail(target).items == []


@pytest.mark.parametrize(
    "problem",
    [
        "unknown-env",
        "deleted-env",
        "foreign-cluster",
        "inactive-cluster",
        "deleted-cluster",
        "decommissioned",
        "unconfigured",
        "untrusted",
    ],
)
def test_unadmitted_target_never_constructs_backend(target, tempo, monkeypatch, problem):
    world, _, cluster, env = target
    if problem == "unknown-env":
        env.name = "nonexistent"
    elif problem == "deleted-env":
        AppEnvironment.objects.filter(pk=env.pk).update(deleted_at=dt.datetime.now(dt.UTC))
    elif problem == "foreign-cluster":
        cluster.organization = ScopeWorld("foreign-explorer").org
        cluster.save(update_fields=["organization"])
    elif problem == "inactive-cluster":
        cluster.is_active = False
        cluster.save(update_fields=["is_active"])
    elif problem == "deleted-cluster":
        cluster.deleted_at = dt.datetime.now(dt.UTC)
        cluster.save(update_fields=["deleted_at"])
    elif problem == "decommissioned":
        cluster.lifecycle = "decommissioned"
        cluster.save(update_fields=["lifecycle"])
    else:
        cluster.provider_config = (
            {}
            if problem == "unconfigured"
            else {"trace_driver": "tempo", "trace_config": {"endpoint": tempo["endpoint"]}}
        )
        cluster.save(update_fields=["provider_config"])

    def forbidden(*args, **kwargs):
        raise AssertionError("Unadmitted target constructed backend")

    monkeypatch.setattr("astrolift_observability.trace_client.driver_for_environment", forbidden)
    result = read(target)
    assert result.reason == Reason.NOT_CONFIGURED and not result.items and not tempo["requests"]


def test_distinct_empty_error_unavailable_and_unmatched_detail(target, tempo):
    tempo["ids"] = []
    assert read(target).reason == Reason.NO_DATA_YET
    tempo["ids"] = [FOREIGN]
    assert detail(target).reason == Reason.NO_DATA_YET
    assert all(path == "/api/search" for path, _ in tempo["requests"])
    tempo["failure"] = True
    assert read(target).reason == Reason.ERROR


@pytest.mark.parametrize("reader", ["list", "detail"])
def test_real_binding_denies_sibling_before_provider(target, tempo, reader):
    world, actor, _, _ = target
    with context(target), pytest.raises(PermissionDenied):
        if reader == "list":
            ScopedTracesQuery().astrolift_app_trace_page(
                make_info(actor), app_slug=world.platform_app.slug, **window()
            )
        else:
            ScopedTracesQuery().astrolift_trace_spans_result(
                make_info(actor), app_slug=world.platform_app.slug, trace_id=TRACE, **window()
            )
    assert not tempo["requests"]


@pytest.mark.parametrize(
    "bad", [("0", "86401"), ("2", "1"), ("now-25h", "now"), ("2026-10-03T00:00:00", "2026-10-03T01:00:00")]
)
def test_bad_windows_refused_before_network(target, tempo, bad):
    world, actor, _, env = target
    with context(target), pytest.raises(ValueError):
        ScopedTracesQuery().astrolift_app_trace_page(
            make_info(actor),
            app_slug=world.medops_app.slug,
            environment_name=env.name,
            since=bad[0],
            until=bad[1],
        )
    assert not tempo["requests"]


@pytest.mark.parametrize(
    "problem", ["unknown-env", "deleted-env", "foreign", "deleted", "inactive", "decommissioned"]
)
def test_logs_exact_placement_refuses_fallback(target, monkeypatch, problem):
    world, actor, cluster, env = target
    if problem == "unknown-env":
        env.name = "missing"
    elif problem == "deleted-env":
        AppEnvironment.objects.filter(pk=env.pk).update(deleted_at=dt.datetime.now(dt.UTC))
    elif problem == "foreign":
        cluster.organization = ScopeWorld("other-logs").org
        cluster.save(update_fields=["organization"])
    elif problem == "deleted":
        cluster.deleted_at = dt.datetime.now(dt.UTC)
        cluster.save(update_fields=["deleted_at"])
    elif problem == "inactive":
        cluster.is_active = False
        cluster.save(update_fields=["is_active"])
    else:
        cluster.lifecycle = "decommissioned"
        cluster.save(update_fields=["lifecycle"])

    def forbidden(**kwargs):
        raise AssertionError("Bad placement queried log provider")

    monkeypatch.setattr("core.cluster_log_query.query_app_logs", forbidden)
    end = dt.datetime.now(dt.UTC)
    with context(target):
        result = LogHistoryQuery().astrolift_app_logs(
            make_info(actor),
            app_slug=world.medops_app.slug,
            environment_name=env.name,
            since=end - dt.timedelta(hours=1),
            until=end,
        )
    assert result.reason == Reason.NOT_CONFIGURED and not result.items


def test_explicit_shared_cluster_placement_with_full_resource_proof(target, tempo):
    world, _, cluster, env = target
    cluster.organization = None
    cluster.save(update_fields=["organization"])
    tempo["body"] = {"resourceSpans": [batch(_attributes(_scope(world.medops_app, env)))]}
    assert read(target).reason == Reason.OK
    assert detail(target).reason == Reason.OK


@pytest.mark.parametrize("owner", ["org", "medops", "medops_project"])
def test_retired_owner_refuses_before_provider(target, tempo, monkeypatch, owner):
    world, _, _, _ = target
    row = getattr(world, owner)
    row.deleted_at = dt.datetime.now(dt.UTC)
    row.save(update_fields=["deleted_at"])
    monkeypatch.setattr(
        "astrolift_observability.trace_client.driver_for_environment",
        lambda env: pytest.fail("retired owner dispatched"),
    )
    with pytest.raises(PermissionDenied):
        read(target)
    assert tempo["requests"] == []


@pytest.mark.parametrize("change", ["retire-owner", "reassign-env", "replace-namespace"])
def test_native_response_discarded_after_concurrent_placement_change(target, tempo, monkeypatch, change):
    from _sdk.observability.tempo_traces import TempoTraceDriver

    world, _, _, env = target
    tempo["body"] = {
        "resourceSpans": [batch({**_attributes(_scope(world.medops_app, env)), "service.name": "owned"})]
    }
    original = TempoTraceDriver.get_trace

    def get_trace(self, trace_id):
        result = original(self, trace_id)
        if change == "retire-owner":
            world.medops.deleted_at = dt.datetime.now(dt.UTC)
            world.medops.save(update_fields=["deleted_at"])
        elif change == "reassign-env":
            env.registered_app = world.platform_app
            env.save(update_fields=["registered_app"])
        else:
            env.k8s_namespace = "changed-namespace"
            env.save(update_fields=["k8s_namespace"])
        return result

    monkeypatch.setattr(TempoTraceDriver, "get_trace", get_trace)
    result = read(target)
    assert result.reason == Reason.NOT_CONFIGURED and result.scope is None and result.items == []
    assert len(tempo["requests"]) == 2


def test_native_failure_does_not_log_foreign_body_or_exception(target, tempo, caplog):
    tempo["failure"] = True
    result = read(target)
    assert result.reason == Reason.ERROR
    assert not any(
        marker in caplog.text for marker in ["FOREIGN_ROOT", "FOREIGN_OPERATION", "503", "Traceback"]
    )


def test_exact_environment_id_rejects_replaced_name_before_native_read(target, tempo):
    _, _, cluster, env = target
    original_id = env.guid
    env.deleted_at = dt.datetime.now(dt.UTC)
    env.save(update_fields=["deleted_at"])
    AppEnvironment.objects.create(
        registered_app=env.registered_app, tenant_cluster=cluster, name=env.name, k8s_namespace="replacement"
    )
    result = read(target, environment_id=original_id)
    assert result.reason == Reason.NOT_CONFIGURED and result.scope is None and result.items == []
    assert tempo["requests"] == []


def test_historical_logs_keep_exact_shared_scope_and_cursor(target, monkeypatch):
    from _sdk.log_stream import LogLine, LogPage

    world, actor, cluster, env = target
    cluster.organization = None
    cluster.save(update_fields=["organization"])
    calls = []

    def query(**kwargs):
        calls.append(kwargs)
        return LogPage(
            items=[
                LogLine(
                    namespace=env.k8s_namespace,
                    timestamp="1000000000",
                    message="info OWNED_LOG",
                    pod="owned-pod",
                    container="server",
                    level="info",
                )
            ],
            next_cursor="server-cursor",
            reached_retention=True,
        )

    monkeypatch.setattr("core.cluster_log_query.query_app_logs", query)
    end = dt.datetime.now(dt.UTC)
    with context(target):
        page = LogHistoryQuery().astrolift_app_logs(
            make_info(actor),
            app_slug=world.medops_app.slug,
            environment_name=env.name,
            environment_id=env.guid,
            since=end - dt.timedelta(hours=1),
            until=end,
            cursor="opaque-in",
            search="OWNED_LOG",
            level="info",
            limit=200,
        )
    assert page.reason == Reason.OK and page.scope == _scope(world.medops_app, env)
    assert page.next_cursor == "server-cursor" and page.reached_retention
    assert [row.message for row in page.items] == ["info OWNED_LOG"]
    assert calls[0]["namespace"] == env.k8s_namespace and calls[0]["cluster"].pk == cluster.pk
    assert (calls[0]["cursor"], calls[0]["search"], calls[0]["level"], calls[0]["limit"]) == (
        "opaque-in",
        "OWNED_LOG",
        "info",
        200,
    )


def test_unconfigured_log_backend_keeps_admitted_scope(target, monkeypatch):
    world, actor, _, env = target
    monkeypatch.setattr("core.cluster_log_query.query_app_logs", lambda **kwargs: None)
    end = dt.datetime.now(dt.UTC)
    with context(target):
        page = LogHistoryQuery().astrolift_app_logs(
            make_info(actor),
            app_slug=world.medops_app.slug,
            environment_id=env.guid,
            since=end - dt.timedelta(hours=1),
            until=end,
        )
    assert page.reason == Reason.NOT_CONFIGURED and not page.historical_available
    assert page.scope == _scope(world.medops_app, env)


def test_actual_graphql_envelope_serializes_exact_scope_and_resources(target, tempo):
    from config.schema import schema

    world, actor, _, env = target
    attributes = {**_attributes(_scope(world.medops_app, env)), "service.name": "owned"}
    tempo["body"] = {"resourceSpans": [batch(attributes, span_attrs={"literal": "kept"})]}
    query = """query Proof($app:String!,$env:GUID!,$trace:String!,$since:String!,$until:String!) {
      astroliftAppTracePage(appSlug:$app,environmentId:$env,since:$since,until:$until) {reason scope {appId environmentId clusterId namespace} items {traceId spanCount} truncated limit}
      astroliftTraceSpansResult(appSlug:$app,environmentId:$env,traceId:$trace,since:$since,until:$until) {reason scope {environmentId} items {traceId spanId attributes resourceAttributes}}
    }"""
    with context(target):
        response = schema.execute_sync(
            query,
            variable_values={"app": world.medops_app.slug, "env": str(env.guid), "trace": TRACE, **window()},
            context_value=make_info(actor).context,
        )
    assert response.errors is None
    page = response.data["astroliftAppTracePage"]
    spans = response.data["astroliftTraceSpansResult"]
    assert page["reason"] == spans["reason"] == "OK"
    assert page["scope"] == {
        "appId": str(world.medops_app.guid),
        "environmentId": str(env.guid),
        "clusterId": str(env.tenant_cluster.guid),
        "namespace": env.k8s_namespace,
    }
    assert spans["items"] == [
        {
            "traceId": TRACE,
            "spanId": "1" * 16,
            "attributes": {"literal": "kept"},
            "resourceAttributes": attributes,
        }
    ]


def test_native_trace_sample_limit_is_server_bounded_not_client_pagination(target, tempo):
    world, _, _, env = target
    tempo["ids"] = [TRACE] + [f"{value:032x}" for value in range(20)]
    tempo["body"] = {
        "resourceSpans": [batch({**_attributes(_scope(world.medops_app, env)), "service.name": "owned"})]
    }
    page = read(target, limit=1000)
    assert page.limit == 20 and page.truncated
    assert [row.trace_id for row in page.items] == [TRACE]
    assert tempo["requests"][0][1]["limit"] == ["21"]
    assert len(tempo["requests"]) == 21  # one search plus at most 20 candidate fetches


def test_native_malformed_trace_diagnostics_never_log_foreign_payload(target, tempo):
    world, _, _, env = target
    owned = batch(_attributes(_scope(world.medops_app, env)))
    owned["scopeSpans"][0]["spans"][0]["startTimeUnixNano"] = "FOREIGN_PRIVATE_TIMESTAMP"
    tempo["body"] = {"resourceSpans": [owned]}
    output = io.StringIO()
    provider_log = logging.getLogger("astrolift.providers")
    handler = logging.StreamHandler(output)
    # Record actual native driver LogRecords with the configured production formatter,
    # including exc_info; this logger deliberately does not propagate to caplog.
    handler.setFormatter(
        next(
            (existing.formatter for existing in provider_log.handlers if existing.formatter),
            logging.Formatter(),
        )
    )
    provider_log.addHandler(handler)
    try:
        result = read(target)
    finally:
        provider_log.removeHandler(handler)
    assert result.reason == Reason.ERROR and result.items == []
    rendered = output.getvalue()
    assert "Tempo trace read failed" in rendered
    assert "FOREIGN_PRIVATE_TIMESTAMP" not in rendered
    assert "FOREIGN_ROOT" not in rendered and "FOREIGN_OPERATION" not in rendered


@pytest.mark.parametrize("fail", [False, True])
def test_log_read_discards_placement_retired_during_provider_call(target, monkeypatch, fail, caplog):
    from _sdk.log_stream import LogLine, LogPage

    world, actor, _, env = target

    def query(**kwargs):
        world.medops.deleted_at = dt.datetime.now(dt.UTC)
        world.medops.save(update_fields=["deleted_at"])
        if fail:
            raise RuntimeError("FOREIGN_PRIVATE_PROVIDER_BODY")
        return LogPage(
            items=[
                LogLine(
                    namespace=env.k8s_namespace,
                    timestamp="1",
                    pod="owned",
                    container="server",
                    message="STALE_OWNED",
                )
            ]
        )

    monkeypatch.setattr("core.cluster_log_query.query_app_logs", query)
    end = dt.datetime.now(dt.UTC)
    with context(target):
        result = LogHistoryQuery().astrolift_app_logs(
            make_info(actor),
            app_slug=world.medops_app.slug,
            environment_id=env.guid,
            since=end - dt.timedelta(hours=1),
            until=end,
        )
    assert result.reason == Reason.NOT_CONFIGURED and result.scope is None and result.items == []
    assert "FOREIGN_PRIVATE_PROVIDER_BODY" not in "\n".join(
        logging.Formatter().format(record) for record in caplog.records
    )


@pytest.mark.parametrize("mode", ["list-error", "detail-error", "detail-empty"])
def test_native_empty_and_failure_receipts_refuse_concurrent_retirement(target, tempo, monkeypatch, mode):
    from _sdk.observability.tempo_traces import TempoTraceDriver

    world, _, _, _ = target
    tempo["ids"] = []
    original = TempoTraceDriver.search_scoped

    def search(self, **kwargs):
        result = original(self, **kwargs)
        world.medops.deleted_at = dt.datetime.now(dt.UTC)
        world.medops.save(update_fields=["deleted_at"])
        if mode.endswith("error"):
            raise RuntimeError("controlled post-read failure")
        return result

    monkeypatch.setattr(TempoTraceDriver, "search_scoped", search)
    result = read(target) if mode.startswith("list") else detail(target)
    assert result.reason == Reason.NOT_CONFIGURED and result.scope is None and result.items == []
    assert len(tempo["requests"]) == 1
