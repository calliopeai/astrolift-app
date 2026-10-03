"""Exact preview log contracts against PostgreSQL and the real Kubernetes driver."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db.models import F
from django.utils import timezone
from graphql import GraphQLError
from k8s_native.cluster import K8sNativeClusterDriver
from kubernetes import client
from urllib3.response import HTTPResponse

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_drivers.registry import PluginManifest, plugins
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle import preview_log_access
from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_lifecycle.schema.subscriptions import LifecycleSubscription
from astrolift_operations.models import AppLogExport
from astrolift_operations.schema.mutations import ExportAppLogsInput, OperationsMutation
from astrolift_registry.models import AppTeamAccess
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db(transaction=True)

SURFACES = ("single", "plural", "export")
PROOF_FIELDS = (
    "preview_id",
    "expected_environment_id",
    "if_match_preview_version",
    "if_match_environment_version",
)


@pytest.fixture
def preview_world(monkeypatch, settings, tmp_path):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))
    monkeypatch.setattr(preview_log_access, "GUARD_INTERVAL_SECONDS", 0.02)
    settings.MEDIA_ROOT = str(tmp_path)
    world = ScopeWorld("preview-logs2257")
    actor = make_user("preview-logs2257")
    member = Member.objects.create(user=actor, scope_kind="ORG", scope_id=world.org.pk)
    app = world.platform_app
    binding = bind_role(
        actor,
        permissions=[Permission.APP_READ_LOGS, Permission.APP_LOG_EXPORT],
        kind="APP",
        scope_id=app.pk,
        slug="preview-logs2257-reader",
    )
    plugin = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if plugin is None:
        ProviderPlugin.objects.bulk_create(
            [ProviderPlugin(name="Kubernetes", slug="k8s_native", plugin_version="0.1.0")]
        )
        plugin = ProviderPlugin.objects.get(slug="k8s_native")
    if not any(p.plugin_id == "k8s_native" for p in plugins.list()):
        plugins.register(
            PluginManifest("k8s_native", "Kubernetes", "0.1.0", {"cluster": K8sNativeClusterDriver})
        )
    assert plugins.get("k8s_native", "cluster") is K8sNativeClusterDriver

    def cluster(suffix, region):
        return TenantCluster.objects.create(
            organization=world.org,
            name=suffix,
            slug=f"preview-logs2257-{suffix}",
            provider_plugin=plugin,
            endpoint=f"https://{suffix}.kubernetes.invalid",
            auth_method="service_account_token",
            auth_config={"token": "test-only-token"},
            region=region,
            lifecycle="managed",
        )

    production_cluster = cluster("production", "us-east-1")
    preview_cluster = cluster("review", "us-west-2")
    app.default_tenant_cluster = production_cluster
    app.save(update_fields=["default_tenant_cluster"])
    production = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=production_cluster,
        name="production",
        k8s_namespace=app.k8s_namespace,
    )
    environment = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=preview_cluster,
        name="arbitrary-review-target",
        k8s_namespace="isolated-preview-logs2257",
    )
    preview = PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=environment,
        pr_number=42,
        branch="same-branch",
        namespace=environment.k8s_namespace,
        hostname="production.example.invalid",
        status="running",
    )
    return SimpleNamespace(
        world=world,
        app=app,
        actor=actor,
        member=member,
        binding=binding,
        production=production,
        production_cluster=production_cluster,
        environment=environment,
        cluster=preview_cluster,
        preview=preview,
        media=tmp_path,
    )


@pytest.fixture
def kube_transport(monkeypatch):
    calls = {"discoveries": [], "opens": [], "responses": [], "idle": False}

    def list_pods(self, **kwargs):
        calls["discoveries"].append((self.api_client.configuration.host, kwargs))
        return client.V1PodList(
            items=[
                client.V1Pod(
                    metadata=client.V1ObjectMeta(name="web-0", labels={"astrolift.dev/workload": "web"}),
                    spec=client.V1PodSpec(containers=[client.V1Container(name="web")]),
                    status=client.V1PodStatus(
                        phase="Running",
                        container_statuses=[
                            client.V1ContainerStatus(
                                name="web",
                                image="test.invalid/web:fixture",
                                image_id="fixture",
                                ready=True,
                                restart_count=0,
                                state=client.V1ContainerState(running=client.V1ContainerStateRunning()),
                            )
                        ],
                    ),
                )
            ]
        )

    class IdleResponse(HTTPResponse):
        def readline(self, *args, **kwargs):
            if not self.delivered:
                self.delivered = True
                return b"2026-10-02T01:00:00Z first line\n"
            self.idle_reads += 1
            return b""

    def read_log(self, **kwargs):
        calls["opens"].append((self.api_client.configuration.host, kwargs))
        response_type = IdleResponse if calls["idle"] else HTTPResponse
        response = response_type(
            body=io.BytesIO(b"2026-10-02T01:00:00Z first line\n2026-10-02T01:00:01Z second line\n"),
            preload_content=False,
        )
        response.delivered = False
        response.idle_reads = 0
        response.released = False
        response.release_conn = lambda: setattr(response, "released", True)
        calls["responses"].append(response)
        return response

    monkeypatch.setattr(client.CoreV1Api, "list_namespaced_pod", list_pods)
    monkeypatch.setattr(client.CoreV1Api, "read_namespaced_pod_log", read_log)
    return calls


def proof(target):
    return {
        "preview_id": str(target.preview.guid),
        "expected_environment_id": str(target.environment.guid),
        "if_match_preview_version": target.preview.version,
        "if_match_environment_version": target.environment.version,
    }


def stream(target, surface, selectors, *, follow=False, token=None, **kwargs):
    info = make_info(target.actor)
    info.context._ws_tenant = TenantContext(
        organization_id=target.world.org.pk, actor_user_id=target.actor.pk
    )
    info.context._ws_api_token = token
    common = dict(info=info, app_slug=target.app.slug, follow=follow, **selectors)
    if surface == "single":
        return LifecycleSubscription().astrolift_on_app_log(
            **common, pod_name=kwargs.pop("pod_name", "web-0"), **kwargs
        )
    return LifecycleSubscription().astrolift_on_app_logs(**common, **kwargs)


def invoke(target, surface, selectors, *, token=None, **kwargs):
    with as_tenant(target.world, target.actor):
        if surface == "export":
            marker = set_current_api_token(token)
            try:
                return OperationsMutation().export_astrolift_app_logs(
                    make_info(target.actor),
                    ExportAppLogsInput(
                        app_slug=target.app.slug,
                        format="NDJSON",
                        pod_name=kwargs.pop("pod_name", "web-0"),
                        **selectors,
                        **kwargs,
                    ),
                )
            finally:
                reset_current_api_token(marker)

        async def collect():
            return [line.message async for line in stream(target, surface, selectors, token=token, **kwargs)]

        return asyncio.run(collect())


def refused(target, surface, selectors, transport, **kwargs):
    try:
        result = invoke(target, surface, selectors, **kwargs)
    except GraphQLError as exc:
        assert exc.extensions["code"] in {"PRECONDITION", "PERMISSION_DENIED"}
        assert exc.message in {preview_log_access.TARGET_ERROR, preview_log_access.ACCESS_ERROR}
    else:
        if surface == "export":
            assert not result.ok
            assert result.errors[0].code in {"PRECONDITION", "PERMISSION_DENIED"}
        else:
            assert result == []
    assert transport["opens"] == []
    assert not AppLogExport.objects.exists()
    assert list(Path(target.media).rglob("*.ndjson")) == []


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("mask", range(1, 15))
def test_partial_proof_never_opens_logs_or_creates_artifact(preview_world, kube_transport, surface, mask):
    complete = proof(preview_world)
    selectors = {key: complete[key] for index, key in enumerate(PROOF_FIELDS) if mask & (1 << index)}
    refused(preview_world, surface, selectors, kube_transport)
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", SURFACES)
def test_complete_proof_routes_only_actual_fk_despite_production_defaults(
    preview_world, kube_transport, surface
):
    target = preview_world
    result = invoke(target, surface, proof(target), workload_slug="web", container="web")
    if surface == "export":
        assert result.ok, result.errors
        export = AppLogExport.objects.get(guid=result.data.id)
        assert export.environment_name == target.environment.name
        assert export.source_snapshot["kind"] == "preview_logs_v1"
        assert export.source_snapshot["target"]["environment_id"] == str(target.environment.guid)
        assert export.source_snapshot["target"]["cluster_id"] == str(target.cluster.guid)
        assert export.source_snapshot["tenant"]["actor_user_id"] == target.actor.pk
        assert export.source_snapshot["token_id"] is None
        assert export.requested_by_id == target.actor.pk
        assert export.row_count == 2
        assert b"first line" in (target.media / export.relative_path).read_bytes()
    else:
        assert result == ["first line", "second line"]
    assert kube_transport["opens"]
    for host, kwargs in kube_transport["opens"] + kube_transport["discoveries"]:
        assert host == target.cluster.endpoint
        assert kwargs["namespace"] == target.environment.k8s_namespace
    assert all(response.released for response in kube_transport["responses"])


@pytest.mark.parametrize("surface", ("single", "plural"))
@pytest.mark.parametrize("change", ("cluster", "namespace"))
def test_capture_never_adopts_source_drift_after_admission(
    preview_world, kube_transport, monkeypatch, surface, change
):
    target = preview_world
    selectors = proof(target)
    original_capture = preview_log_access.PreviewLogAuthority.capture
    captures = []

    def capture_after_drift(cls, reviewed, permission):
        captures.append(reviewed)
        assert reviewed.namespace == target.environment.k8s_namespace
        assert reviewed.cluster_version == target.cluster.version
        if change == "cluster":
            TenantCluster.objects.filter(pk=target.cluster.pk).update(
                version=F("version") + 1, endpoint="https://rebound.kubernetes.invalid"
            )
        else:
            AppEnvironment.objects.filter(pk=target.environment.pk).update(k8s_namespace="rebound-preview")
            PreviewEnvironment.objects.filter(pk=target.preview.pk).update(namespace="rebound-preview")
        return original_capture(reviewed, permission)

    monkeypatch.setattr(preview_log_access.PreviewLogAuthority, "capture", classmethod(capture_after_drift))
    with pytest.raises(GraphQLError) as error:
        invoke(target, surface, selectors)
    assert error.value.extensions == {"code": "PRECONDITION"}
    assert error.value.message == preview_log_access.TARGET_ERROR
    assert len(captures) == 1
    assert kube_transport["discoveries"] == kube_transport["opens"] == []


@pytest.mark.parametrize("surface", SURFACES)
def test_absent_proof_preserves_ordinary_production_logs(preview_world, kube_transport, surface):
    target = preview_world
    result = invoke(target, surface, {})
    if surface == "export":
        assert result.ok, result.errors
        assert AppLogExport.objects.get(guid=result.data.id).source_snapshot == {}
    else:
        assert result == ["first line", "second line"]
    assert kube_transport["opens"]
    assert all(
        host == target.production_cluster.endpoint and kwargs["namespace"] == target.app.k8s_namespace
        for host, kwargs in kube_transport["opens"]
    )


@pytest.mark.parametrize("surface", ("plural", "export"))
def test_preview_environment_name_alone_cannot_authorize_logs(preview_world, kube_transport, surface):
    refused(preview_world, surface, {}, kube_transport, environment_name=preview_world.environment.name)
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("field", PROOF_FIELDS)
def test_missing_identity_or_stale_review_never_uses_default(preview_world, kube_transport, surface, field):
    selectors = proof(preview_world)
    selectors[field] = str(uuid4()) if field.endswith("id") else selectors[field] - 1
    refused(preview_world, surface, selectors, kube_transport)
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("field", ("preview_id", "expected_environment_id"))
def test_malformed_identity_has_static_refusal_before_provider_io(
    preview_world, kube_transport, surface, field
):
    selectors = proof(preview_world)
    selectors[field] = "not-a-uuid"
    refused(preview_world, surface, selectors, kube_transport)
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", SURFACES)
def test_legacy_default_cannot_route_into_an_unreviewed_preview(preview_world, kube_transport, surface):
    target = preview_world
    target.app.default_tenant_cluster = target.cluster
    target.app.k8s_namespace = target.environment.k8s_namespace
    target.app.save(update_fields=["default_tenant_cluster", "k8s_namespace"])
    refused(target, surface, {}, kube_transport)
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize(
    "change",
    (
        "deleted-preview",
        "deleted-environment",
        "torn-down-status",
        "torn-down-timestamp",
        "foreign-app",
        "foreign-tenant",
        "foreign-environment",
        "foreign-cluster",
        "inactive-cluster",
        "deleted-cluster",
        "missing-namespace",
        "default-namespace",
        "different-namespace",
        "replaced-environment",
    ),
)
def test_unavailable_source_never_substitutes_a_live_target(preview_world, kube_transport, surface, change):
    target = preview_world
    selectors = proof(target)
    if change == "deleted-preview":
        target.preview.soft_delete()
    elif change == "deleted-environment":
        target.environment.soft_delete()
    elif change == "torn-down-status":
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(status="torn_down")
    elif change == "torn-down-timestamp":
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(torn_down_at=timezone.now())
    elif change == "foreign-app":
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(registered_app=target.world.medops_app)
    elif change == "foreign-tenant":
        foreign = ScopeWorld("preview-logs2257-foreign")
        foreign.platform_app.slug = target.app.slug
        foreign.platform_app.save(update_fields=["slug"])
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(registered_app=foreign.platform_app)
    elif change == "foreign-environment":
        AppEnvironment.objects.filter(pk=target.environment.pk).update(registered_app=target.world.medops_app)
    elif change == "foreign-cluster":
        foreign = ScopeWorld("preview-logs2257-foreign")
        TenantCluster.objects.filter(pk=target.cluster.pk).update(organization=foreign.org)
    elif change == "inactive-cluster":
        TenantCluster.objects.filter(pk=target.cluster.pk).update(is_active=False)
    elif change == "deleted-cluster":
        target.cluster.soft_delete()
    elif change == "missing-namespace":
        AppEnvironment.objects.filter(pk=target.environment.pk).update(k8s_namespace="")
    elif change == "default-namespace":
        AppEnvironment.objects.filter(pk=target.production.pk).update(k8s_namespace="moved-production")
        AppEnvironment.objects.filter(pk=target.environment.pk).update(k8s_namespace=target.app.k8s_namespace)
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(namespace=target.app.k8s_namespace)
    elif change == "different-namespace":
        AppEnvironment.objects.filter(pk=target.environment.pk).update(k8s_namespace="other-preview")
    else:
        target.environment.soft_delete()
        replacement = AppEnvironment.objects.create(
            registered_app=target.app,
            tenant_cluster=target.cluster,
            name=target.environment.name,
            k8s_namespace=target.environment.k8s_namespace,
        )
        assert replacement.guid != target.environment.guid
    refused(target, surface, selectors, kube_transport)
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", ("single", "export"))
@pytest.mark.parametrize(
    "kwargs", ({"pod_name": "production-pod"}, {"workload_slug": "other"}, {"container": "other"})
)
def test_unbound_pod_workload_and_container_refuse_before_log_open(
    preview_world, kube_transport, surface, kwargs
):
    refused(preview_world, surface, proof(preview_world), kube_transport, **kwargs)
    assert kube_transport["discoveries"]


@pytest.mark.parametrize("kwargs", ({"workload_slug": "other"}, {"container": "other"}))
def test_plural_filters_never_open_a_different_workload_or_container(preview_world, kube_transport, kwargs):
    refused(preview_world, "plural", proof(preview_world), kube_transport, **kwargs)
    assert kube_transport["discoveries"]


@pytest.mark.parametrize("surface", ("plural", "export"))
def test_environment_name_cannot_override_complete_fk_proof(preview_world, kube_transport, surface):
    refused(preview_world, surface, proof(preview_world), kube_transport, environment_name="production")
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("fact", ("environment", "region"))
def test_preview_admission_uses_persisted_abac_context(preview_world, kube_transport, surface, fact):
    target = preview_world
    Policy.objects.create(
        organization=target.world.org,
        name="Refuse reviewed log context",
        slug="preview-logs2257-deny",
        effect="DENY",
        scope_level="ORG",
        action_pattern="app.log_export" if surface == "export" else "app.read_logs",
        resource_pattern={"env": [target.environment.name]}
        if fact == "environment"
        else {"region": [target.cluster.region]},
    )
    refused(target, surface, proof(target), kube_transport)
    assert kube_transport["discoveries"] == []


def revoke(target, change, token):
    if change == "role":
        target.binding.soft_delete()
    elif change == "member":
        Member.objects.filter(pk=target.member.pk).update(is_active=False)
    elif change == "token":
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
    elif change == "expired-token":
        ApiToken.objects.filter(pk=token.pk).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    elif change == "token-scopes":
        ApiToken.objects.filter(pk=token.pk).update(scopes=["app.read"])
    elif change == "preview":
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(status="torn_down")
    elif change == "environment":
        target.environment.soft_delete()
    elif change == "preview-version":
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(version=F("version") + 1)
    elif change == "environment-version":
        AppEnvironment.objects.filter(pk=target.environment.pk).update(version=F("version") + 1)
    elif change == "app-version":
        type(target.app).objects.filter(pk=target.app.pk).update(version=F("version") + 1)
    elif change == "cluster":
        TenantCluster.objects.filter(pk=target.cluster.pk).update(is_active=False)
    elif change == "cluster-version":
        TenantCluster.objects.filter(pk=target.cluster.pk).update(version=F("version") + 1)
    elif change == "namespace":
        AppEnvironment.objects.filter(pk=target.environment.pk).update(k8s_namespace="rebound-preview")
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(namespace="rebound-preview")
    else:
        Policy.objects.create(
            organization=target.world.org,
            name="Revoke live logs",
            slug="preview-logs2257-live-deny",
            effect="DENY",
            scope_level="ORG",
            action_pattern="app.read_logs",
            resource_pattern={"env": [target.environment.name]},
        )


@pytest.mark.parametrize("surface", ("single", "plural"))
@pytest.mark.parametrize(
    "change",
    (
        "role",
        "member",
        "token",
        "expired-token",
        "token-scopes",
        "preview",
        "environment",
        "preview-version",
        "environment-version",
        "app-version",
        "cluster",
        "cluster-version",
        "namespace",
        "policy",
    ),
)
def test_idle_follow_revalidates_source_and_access_then_releases_connection(
    preview_world, kube_transport, surface, change
):
    target = preview_world
    minted = mint_token()
    token = ApiToken.objects.create(
        user=target.actor,
        organization=target.world.org,
        name="Preview log reader",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    assert_idle_termination(target, kube_transport, surface, token, lambda: revoke(target, change, token))


def assert_idle_termination(target, kube_transport, surface, token, revoke_callback):
    kube_transport["idle"] = True

    async def run():
        source = stream(target, surface, proof(target), follow=True, token=token, container="web")
        pending = None
        try:
            assert (await asyncio.wait_for(anext(source), timeout=3)).message == "first line"
            pending = asyncio.create_task(anext(source))
            async with asyncio.timeout(3):
                while not any(response.idle_reads for response in kube_transport["responses"]):
                    await asyncio.sleep(0.01)
            assert not pending.done()
            await sync_to_async(revoke_callback)()
            try:
                await asyncio.wait_for(pending, timeout=3)
            except GraphQLError as exc:
                assert exc.extensions["code"] in {"PRECONDITION", "PERMISSION_DENIED"}
                assert exc.message in {preview_log_access.TARGET_ERROR, preview_log_access.ACCESS_ERROR}
            except StopAsyncIteration:
                pass
            else:
                pytest.fail("A revoked idle source produced another log line")
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await source.aclose()

    with as_tenant(target.world, target.actor):
        asyncio.run(run())
    assert kube_transport["responses"]
    assert all(response.released for response in kube_transport["responses"])
    assert len(kube_transport["opens"]) == 1


def team_token(target, team):
    minted = mint_token()
    return ApiToken.objects.create(
        user=target.actor,
        organization=target.world.org,
        team=team,
        name="Team preview log reader",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("shared", (False, True))
def test_team_bearer_admits_owned_and_shared_apps_without_a_selected_team(
    preview_world, kube_transport, surface, shared
):
    target = preview_world
    team = target.world.medops if shared else target.world.platform
    if shared:
        AppTeamAccess.objects.create(registered_app=target.app, team=team, access_level="owner")
    token = team_token(target, team)
    result = invoke(target, surface, proof(target), token=token, container="web")
    if surface == "export":
        assert result.ok, result.errors
        export = AppLogExport.objects.get(guid=result.data.id)
        assert export.source_snapshot["token_id"] == token.pk
        assert export.source_snapshot["tenant"]["team_id"] is None
        assert export.source_snapshot["token_team_id"] == team.pk
    else:
        assert result == ["first line", "second line"]
    assert kube_transport["opens"]
    assert all(response.released for response in kube_transport["responses"])


@pytest.mark.parametrize("surface", SURFACES)
def test_team_bearer_never_uses_users_grant_on_an_unshared_sibling(preview_world, kube_transport, surface):
    target = preview_world
    token = team_token(target, target.world.medops)
    refused(target, surface, proof(target), kube_transport, token=token)
    assert kube_transport["discoveries"] == []


@pytest.mark.parametrize("surface", ("single", "plural"))
@pytest.mark.parametrize("change", ("token-team", "share"))
def test_idle_team_bearer_stops_after_original_ceiling_or_share_changes(
    preview_world, kube_transport, surface, change
):
    target = preview_world
    share = AppTeamAccess.objects.create(
        registered_app=target.app, team=target.world.medops, access_level="owner"
    )
    token = team_token(target, target.world.medops)

    def revoke_access():
        if change == "token-team":
            ApiToken.objects.filter(pk=token.pk).update(team=target.world.platform)
        else:
            share.soft_delete()

    assert_idle_termination(target, kube_transport, surface, token, revoke_access)
