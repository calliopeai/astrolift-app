"""Real app grants, PostgreSQL and native Prometheus HTTP attribution reads."""

import json
import threading
import urllib.request
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone
from graphql import GraphQLError

from astrolift_graphql import GUID
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.model_observations import ModelObservationState as State
from astrolift_services.model_subscription_observations import subscription_metrics
from astrolift_services.models import ManagedServiceAttachment
from astrolift_services.schema.model_reads import ModelReadsQuery
from astrolift_services.tests.test_model_observations_2214 import (
    caller,
    matrix,
    namespace,
    resource_name,
)
from astrolift_services.tests.test_model_observations_2214 import (
    world as observation_world,
)
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import bind_role, make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    native_urlopen = urllib.request.urlopen
    w = observation_world.__wrapped__(monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", native_urlopen)
    w.env = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=w.cluster, name="production"
    )
    w.subscription = ManagedServiceAttachment.objects.create(
        managed_service=w.model,
        app_environment=w.env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="active",
    )
    w.http_calls = []
    w.reply = matrix([])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 -- standard HTTP handler interface
            length = int(self.headers.get("Content-Length", "0"))
            w.http_calls.append((self.path, self.rfile.read(length)))
            body = w.reply if isinstance(w.reply, bytes) else json.dumps(w.reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    w.cluster.provider_config["prometheus_endpoint"] = f"http://127.0.0.1:{server.server_port}"
    w.cluster.save()
    yield w
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def grant(w, app=None):
    bind_role(w.user, permissions=[Permission.ORG_READ], kind="ORG", scope_id=w.org.pk, slug="meter-org-read")
    return bind_role(
        w.user,
        permissions=[Permission.APP_READ_METRICS],
        kind="APP",
        scope_id=(app or w.medops_app).pk,
        slug="meter-app-metrics",
    )


def read(w, **changes):
    return ModelReadsQuery().astrolift_model_subscription_metrics(
        make_info(w.user),
        **(
            {
                "organization_id": GUID(str(w.org.guid)),
                "service_id": GUID(str(w.model.guid)),
                "subscription_id": GUID(str(w.subscription.guid)),
                "expected_cluster_id": GUID(str(w.cluster.guid)),
                "expected_provider_id": GUID(str(w.cluster.provider_plugin.guid)),
                "start": w.start,
                "end": w.end,
            }
            | changes
        ),
    )


def series(w, key="requests_per_second", value=0, timestamp=None, **labels):
    return {
        "metric": {
            "managed_service": str(w.model.guid),
            "subscription_id": str(w.subscription.guid),
            "namespace": namespace(w, w.model),
            "service": resource_name(w.model),
            "astrolift_measurement": key,
            **labels,
        },
        "values": [[int((timestamp or w.end).timestamp()), str(value)]],
    }


def test_native_http_reports_actual_zero_and_no_data_separately_without_inventing_tokens(world):
    grant(world)
    world.reply = matrix([series(world)])
    with caller(world):
        result = read(world)
    values = {item.key: item for item in result.metrics}
    assert result.scope == "authenticated_subscription"
    assert values["requests_per_second"].state == State.AVAILABLE and values["requests_per_second"].value == 0
    assert values["latency_p95"].state == State.NO_DATA and values["latency_p95"].value is None
    assert all(
        values[key].state == State.UNSUPPORTED and values[key].value is None
        for key in ("input_tokens", "output_tokens", "cost_usd")
    )
    assert len(world.http_calls) == 1
    path, body = world.http_calls[0]
    assert path == "/api/v1/query_range" and len(body) <= 32_768
    assert str(world.subscription.guid).encode() in body and b"subscription_info" in body


def test_other_apps_grant_cannot_read_this_subscription_or_trigger_transport(world):
    grant(world, world.platform_app)
    with caller(world), pytest.raises(PermissionDenied):
        read(world)
    assert not world.http_calls


@pytest.mark.parametrize(
    "field",
    ["organization_id", "service_id", "subscription_id", "expected_cluster_id", "expected_provider_id"],
)
def test_changed_or_foreign_target_refuses_before_transport(world, field):
    grant(world)
    with caller(world):
        try:
            assert read(world, **{field: GUID(str(uuid4()))}) is None
        except PermissionDenied:
            assert field == "subscription_id"
    assert not world.http_calls


@pytest.mark.parametrize(
    "bad", ["subscription", "namespace", "service", "model", "duplicate", "negative", "nan", "body"]
)
def test_untrusted_observations_never_escape_as_attributed_usage(world, bad, caplog):
    grant(world)
    row = series(world, value=-1 if bad == "negative" else "NaN" if bad == "nan" else 1)
    if bad in ("subscription", "namespace", "service", "model"):
        row["metric"][{"subscription": "subscription_id", "model": "managed_service"}.get(bad, bad)] = (
            "PRIVATE_MODEL_BODY_MARKER"
        )
    world.reply = b"x" * 1_048_577 if bad == "body" else matrix([row, row] if bad == "duplicate" else [row])
    with caller(world):
        result = read(world)
    assert all(item.value is None for item in result.metrics)
    assert (
        next(item for item in result.metrics if item.key == "requests_per_second").state == State.UNAVAILABLE
    )
    assert "PRIVATE_MODEL_BODY_MARKER" not in caplog.text


@pytest.mark.parametrize("authentication", ["session", "bearer"])
def test_graphql_dispatch_uses_real_app_grants_and_native_transport(world, authentication):
    grant(world)
    world.reply = matrix([series(world, value=2.5)])
    client = Client()
    headers = {"HTTP_X_PLATFORM": "web", "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid)}
    if authentication == "session":
        client.force_login(world.user)
    else:
        minted = mint_token()
        ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            name="app-metrics-http",
            token_hash=minted.token_hash,
            scopes=["read:apps"],
        )
        headers["HTTP_AUTHORIZATION"] = f"Bearer {minted.plaintext}"
    query = """query($org:GUID!,$model:GUID!,$sub:GUID!,$cluster:GUID!,$provider:GUID!,$start:DateTime!,$end:DateTime!){
      astroliftModelSubscriptionMetrics(organizationId:$org,serviceId:$model,subscriptionId:$sub,expectedClusterId:$cluster,expectedProviderId:$provider,start:$start,end:$end){scope subscriptionId metrics{key state value}}
    }"""
    response = client.post(
        "/app/gql/config/",
        data=json.dumps(
            {
                "query": query,
                "variables": {
                    "org": str(world.org.guid),
                    "model": str(world.model.guid),
                    "sub": str(world.subscription.guid),
                    "cluster": str(world.cluster.guid),
                    "provider": str(world.cluster.provider_plugin.guid),
                    "start": world.start.isoformat(),
                    "end": world.end.isoformat(),
                },
            }
        ),
        content_type="application/json",
        **headers,
    )
    payload = response.json()
    assert not payload.get("errors"), payload
    assert payload["data"]["astroliftModelSubscriptionMetrics"]["scope"] == "authenticated_subscription"
    assert len(world.http_calls) == 1


def test_narrow_read_apps_bearer_can_read_its_app_traffic_without_cluster_admin(world):
    grant(world)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="app-meter",
        token_hash=uuid4().hex,
        scopes=["read:apps"],
    )
    with caller(world, token):
        assert read(world) is not None
    assert len(world.http_calls) == 1


@pytest.mark.parametrize("ceiling", ["empty", "cluster", "revoked", "expired", "foreign-team"])
def test_bearer_ceiling_or_revocation_refuses_before_prometheus(world, ceiling):
    grant(world)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="app-meter",
        token_hash=uuid4().hex,
        scopes=[] if ceiling == "empty" else ["read:clusters"] if ceiling == "cluster" else ["read:apps"],
        is_revoked=ceiling == "revoked",
        expires_at=timezone.now() - timedelta(seconds=1) if ceiling == "expired" else None,
        team=world.platform if ceiling == "foreign-team" else None,
    )
    with caller(world, token):
        try:
            assert read(world) is None
        except (GraphQLError, PermissionDenied):
            pass
    assert not world.http_calls


@pytest.mark.parametrize(
    "change", ["role", "member", "inactive", "policy", "model", "subscription", "environment", "provider"]
)
def test_permission_or_target_withdrawn_during_real_transport_discards_result(world, monkeypatch, change):
    role = grant(world)
    world.reply = matrix([series(world, value=3)])

    def observe_then_withdraw(*args, **kwargs):
        result = subscription_metrics(*args, **kwargs)
        if change == "role":
            role.soft_delete()
        elif change == "member":
            Member.objects.get(user=world.user, scope_kind="ORG", scope_id=world.org.pk).soft_delete()
        elif change == "inactive":
            world.user.is_active = False
            world.user.save()
        elif change == "policy":
            Policy.objects.create(
                organization=world.org,
                name="Withdraw metrics",
                effect="DENY",
                action_pattern="app.read_metrics",
                resource_pattern={"env": "production"},
                conditions=[],
            )
        else:
            {
                "model": world.model,
                "subscription": world.subscription,
                "environment": world.env,
                "provider": world.cluster.provider_plugin,
            }[change].soft_delete()
        return result

    monkeypatch.setattr("astrolift_services.schema.model_reads.subscription_metrics", observe_then_withdraw)
    with caller(world):
        try:
            assert read(world) is None
        except (GraphQLError, PermissionDenied):
            pass
    assert len(world.http_calls) == 1


@pytest.mark.parametrize("change", ["endpoint", "cluster", "stale"])
def test_missing_unavailable_and_stale_are_distinct_from_measured_zero(world, change):
    grant(world)
    if change == "endpoint":
        world.cluster.provider_config = {}
    elif change == "cluster":
        world.cluster.lifecycle = "registered"
    else:
        world.reply = matrix([series(world, value=0, timestamp=world.end - timedelta(minutes=20))])
    world.cluster.save()
    with caller(world):
        result = read(world)
    requests = next(item for item in result.metrics if item.key == "requests_per_second")
    assert (
        requests.state
        == {"endpoint": State.UNCONFIGURED, "cluster": State.UNAVAILABLE, "stale": State.STALE}[change]
    )
    assert requests.value == (0 if change == "stale" else None)
    assert len(world.http_calls) == (1 if change == "stale" else 0)


def test_public_capability_discovery_advertises_surface_without_private_metrics():
    from config.schema_public import schema_public

    assert "astroliftModelSubscriptionMetrics" not in schema_public.as_str()
    result = schema_public.execute_sync("{astroliftServerInfo{capabilities}}")
    assert not result.errors
    assert "models.authenticated_subscription_metrics" in result.data["astroliftServerInfo"]["capabilities"]


@pytest.mark.parametrize("owner", ["organization", "installation_shared", "foreign"])
def test_subscription_traffic_preserves_admitted_cluster_ownership(world, owner):
    grant(world)
    if owner == "foreign":
        from astrolift_identity.models import Organization

        world.cluster.organization = Organization.objects.create(name="Foreign", slug="foreign-metrics-owner")
    else:
        world.cluster.organization = world.org if owner == "organization" else None
    world.cluster.save()
    world.reply = matrix([series(world)])
    if owner == "foreign":
        with caller(world), pytest.raises(PermissionDenied):
            read(world)
        assert world.http_calls == []
    else:
        with caller(world):
            result = read(world)
        assert result is not None and world.http_calls
        observed = next(item for item in result.metrics if item.key == "requests_per_second")
        assert observed.state == State.AVAILABLE and observed.value == 0
