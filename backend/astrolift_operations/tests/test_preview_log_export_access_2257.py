"""Real requester/credential/source checks for exact preview artifacts."""

from __future__ import annotations

import asyncio
import datetime as dt
from urllib.parse import urlsplit

import pytest
from asgiref.sync import sync_to_async
from django.db.models import F
from django.test import Client
from django.utils import timezone

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Policy
from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_observability.tests import test_preview_live_logs_2257 as contracts
from astrolift_observability.tests.test_preview_live_logs_2257 import (
    invoke,
    proof,
    revoke,
)
from astrolift_operations.models import AppLogExport
from core.tests.utils.scope_world import make_user

preview_world = contracts.preview_world
kube_transport = contracts.kube_transport

pytestmark = pytest.mark.django_db(transaction=True)


def credential(target, *, scopes=None):
    minted = mint_token()
    token = ApiToken.objects.create(
        user=target.actor,
        organization=target.world.org,
        name="Exact export requester",
        token_hash=minted.token_hash,
        scopes=scopes or ["write:apps"],
    )
    return token, minted.plaintext


def export_for(target, token=None):
    result = invoke(target, "export", proof(target), token=token, container="web")
    assert result.ok, [(error.code, error.message) for error in result.errors]
    export = AppLogExport.objects.get(guid=result.data.id)
    return export, urlsplit(result.data.download_url).path


def download(target, path, *, actor=None, bearer=None):
    client = Client()
    if actor is not None:
        client.force_login(actor)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(target.world.org.guid)}
    if bearer is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {bearer}"
    return client.get(path, **headers)


def body(response):
    async def read():
        return b"".join([chunk async for chunk in response.streaming_content])

    try:
        return asyncio.run(read())
    finally:
        response.close()


@pytest.mark.parametrize("origin_bearer", [False, True])
@pytest.mark.parametrize("request_bearer", [False, True])
def test_download_requires_requester_and_preserves_original_authority(
    preview_world, kube_transport, origin_bearer, request_bearer
):
    target = preview_world
    token, plaintext = credential(target)
    export, path = export_for(target, token if origin_bearer else None)
    response = download(
        target,
        path,
        actor=None if request_bearer else target.actor,
        bearer=plaintext if request_bearer else None,
    )
    if request_bearer and not origin_bearer:
        assert response.status_code == 404
        assert AppLogExport.objects.get(pk=export.pk).consumed_at is None
        return
    assert response.status_code == 200
    assert response.is_async
    assert b"first line" in body(response)
    assert AppLogExport.objects.get(pk=export.pk).consumed_at is not None


@pytest.mark.parametrize("identity", ["anonymous", "other-user", "other-token", "other-org"])
def test_capability_link_alone_never_widens_preview_authority(preview_world, kube_transport, identity):
    target = preview_world
    token, _plaintext = credential(target)
    export, path = export_for(target, token)
    actor, bearer = target.actor, None
    if identity == "anonymous":
        actor = None
    elif identity == "other-user":
        actor = make_user("other-export-requester2257")
    elif identity == "other-token":
        _other, bearer = credential(target)
        actor = None
    if identity == "other-org":
        client = Client()
        client.force_login(target.actor)
        response = client.get(
            path, HTTP_X_ASTROLIFT_ORGANIZATION=str(contracts.ScopeWorld("other-org2257").org.guid)
        )
    else:
        response = download(target, path, actor=actor, bearer=bearer)
    assert response.status_code == 404
    assert AppLogExport.objects.get(pk=export.pk).consumed_at is None


@pytest.mark.parametrize(
    "change",
    [
        "role",
        "member",
        "token",
        "expired-token",
        "deleted-token",
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
        "expiry",
        "snapshot",
        "deleted-artifact",
        "replacement",
    ],
)
def test_download_rechecks_actual_source_and_present_access(preview_world, kube_transport, change):
    target = preview_world
    token, _plaintext = credential(target)
    export, path = export_for(target, token)
    if change == "policy":
        Policy.objects.create(
            organization=target.world.org,
            name="Refuse preview artifact",
            slug="preview-export2257-deny",
            effect="DENY",
            scope_level="ORG",
            action_pattern="app.log_export",
            resource_pattern={"env": [target.environment.name]},
        )
    elif change == "expiry":
        AppLogExport.objects.filter(pk=export.pk).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    elif change == "snapshot":
        AppLogExport.objects.filter(pk=export.pk).update(source_snapshot={"kind": "unknown"})
    elif change == "deleted-artifact":
        AppLogExport.objects.filter(pk=export.pk).delete()
    elif change == "deleted-token":
        token.soft_delete()
    elif change == "replacement":
        PreviewEnvironment.objects.filter(pk=target.preview.pk).update(app_environment=target.production)
    else:
        revoke(target, change, token)
    response = download(target, path, actor=target.actor)
    assert response.status_code == 404
    row = AppLogExport.objects.filter(pk=export.pk).first()
    assert row is None or row.consumed_at is None
    assert len(kube_transport["opens"]) == 1  # No provider read at download.


@pytest.mark.parametrize(
    "change", ["role", "member", "token", "preview", "environment", "snapshot", "expiry"]
)
def test_download_refuses_further_chunks_after_access_or_source_changes(
    preview_world, kube_transport, change
):
    target = preview_world
    token, _plaintext = credential(target)
    export, path = export_for(target, token)
    absolute = target.media / export.relative_path
    absolute.write_bytes(b"x" * (64 * 1024 * 3))
    response = download(target, path, actor=target.actor)
    assert response.status_code == 200

    async def read():
        chunks = response.streaming_content
        assert len(await anext(chunks)) == 64 * 1024
        if change == "snapshot":
            await sync_to_async(AppLogExport.objects.filter(pk=export.pk).update)(source_snapshot={})
        elif change == "expiry":
            await sync_to_async(AppLogExport.objects.filter(pk=export.pk).update)(expires_at=timezone.now())
        else:
            await sync_to_async(revoke)(target, change, token)
        with pytest.raises(StopAsyncIteration):
            await anext(chunks)

    try:
        asyncio.run(read())
    finally:
        response.close()


def test_known_legacy_preview_artifact_does_not_gain_inferred_proof(preview_world, kube_transport):
    target = preview_world
    export, path = export_for(target)
    AppLogExport.objects.filter(pk=export.pk).update(source_snapshot={})
    assert download(target, path, actor=target.actor).status_code == 404
    assert download(target, path).status_code == 404


def test_legacy_ordinary_artifact_retains_capability_link(preview_world, kube_transport):
    target = preview_world
    result = invoke(target, "export", {}, environment_name="production")
    assert result.ok
    export = AppLogExport.objects.get(guid=result.data.id)
    assert export.source_snapshot == {}
    response = download(target, urlsplit(result.data.download_url).path)
    assert response.status_code == 200
    try:
        assert b"first line" in b"".join(response.streaming_content)
    finally:
        response.close()


def test_scope_changes_during_materialization_leave_no_artifact_or_receipt(
    preview_world, kube_transport, monkeypatch
):
    target = preview_world
    from kubernetes import client

    original = client.CoreV1Api.read_namespaced_pod_log

    def read_log(api, **kwargs):
        response = original(api, **kwargs)
        original_read = response.read

        def read(*args, **read_kwargs):
            raw = original_read(*args, **read_kwargs)
            PreviewEnvironment.objects.filter(pk=target.preview.pk).update(version=F("version") + 1)
            return raw

        response.read = read
        return response

    monkeypatch.setattr(client.CoreV1Api, "read_namespaced_pod_log", read_log)
    result = invoke(target, "export", proof(target))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert not AppLogExport.objects.exists()
    assert list(target.media.rglob("*.ndjson")) == []
    assert all(response.released for response in kube_transport["responses"])


@pytest.mark.parametrize("surface", ["single", "plural", "export"])
@pytest.mark.parametrize("boundary", ["discovery", "log-open"])
def test_provider_failure_is_static_without_body_in_diagnostics(
    preview_world, kube_transport, monkeypatch, caplog, surface, boundary
):
    from graphql import GraphQLError
    from kubernetes import client

    marker = "sensitive-provider-marker2257"

    def fail(*args, **kwargs):
        raise RuntimeError(marker)

    method = "list_namespaced_pod" if boundary == "discovery" else "read_namespaced_pod_log"
    monkeypatch.setattr(client.CoreV1Api, method, fail)
    try:
        result = invoke(preview_world, surface, proof(preview_world))
    except GraphQLError as exc:
        assert exc.extensions["code"] == "PRECONDITION"
        assert marker not in exc.message
    else:
        assert not result.ok
        assert result.errors[0].code == "PRECONDITION"
        assert marker not in result.errors[0].message
    assert marker not in caplog.text
    assert not AppLogExport.objects.exists()
    assert list(preview_world.media.rglob("*.ndjson")) == []


def test_exact_export_read_deadline_closes_provider_without_artifact(
    preview_world, kube_transport, monkeypatch
):
    import time

    from kubernetes import client

    from astrolift_lifecycle import preview_log_access

    original = client.CoreV1Api.read_namespaced_pod_log

    def open_logs(api, **kwargs):
        response = original(api, **kwargs)
        read = response.read

        def slow_read(*args, **read_kwargs):
            time.sleep(0.1)
            return read(*args, **read_kwargs)

        response.read = slow_read
        return response

    monkeypatch.setattr(client.CoreV1Api, "read_namespaced_pod_log", open_logs)
    monkeypatch.setattr(preview_log_access, "EXPORT_TIMEOUT_SECONDS", 0.02)
    result = invoke(preview_world, "export", proof(preview_world))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert not AppLogExport.objects.exists()
    assert list(preview_world.media.rglob("*.ndjson")) == []
    assert all(response.released for response in kube_transport["responses"])


def test_graphql_export_accepts_exact_input_and_returns_metadata_only(preview_world, kube_transport):
    from config.schema import schema
    from core.tests.utils.scope_world import as_tenant, make_info

    target = preview_world
    inputs = {
        "appSlug": target.app.slug,
        "podName": "web-0",
        "format": "TXT",
        "previewId": str(target.preview.guid),
        "expectedEnvironmentId": str(target.environment.guid),
        "ifMatchPreviewVersion": target.preview.version,
        "ifMatchEnvironmentVersion": target.environment.version,
    }
    document = """mutation Export($input: ExportAppLogsInput!) {
      exportAstroliftAppLogs(input: $input) {
        ok errors { code } data { id status rowCount byteCount sha256 }
      }
    }"""
    with as_tenant(target.world, target.actor):
        result = schema.execute_sync(
            document, variable_values={"input": inputs}, context_value=make_info(target.actor).context
        )
    assert not result.errors
    payload = result.data["exportAstroliftAppLogs"]
    assert payload["ok"]
    assert payload["data"]["rowCount"] == 2
    assert len(payload["data"]["sha256"]) == 64
    assert kube_transport["opens"][0][1]["namespace"] == target.environment.k8s_namespace


@pytest.mark.parametrize("partial", [False, True])
def test_graphql_live_proof_and_idle_terminal_are_real_execution_results(
    preview_world, kube_transport, partial
):
    from config.schema import schema
    from core.tenancy import TenantContext
    from core.tests.utils.scope_world import make_info

    target = preview_world
    kube_transport["idle"] = True
    context = make_info(target.actor).context
    context._ws_tenant = TenantContext(organization_id=target.world.org.pk, actor_user_id=target.actor.pk)
    document = """subscription Tail($app: String!, $preview: GUID!, $environment: GUID, $pv: Int!, $ev: Int!) {
      astroliftOnAppLogs(appSlug: $app, previewId: $preview,
        expectedEnvironmentId: $environment, ifMatchPreviewVersion: $pv,
        ifMatchEnvironmentVersion: $ev) { podName message }
    }"""
    variables = {
        "app": target.app.slug,
        "preview": str(target.preview.guid),
        "environment": None if partial else str(target.environment.guid),
        "pv": target.preview.version,
        "ev": target.environment.version,
    }

    async def run():
        source = await schema.subscribe(document, variable_values=variables, context_value=context)
        try:
            first = await asyncio.wait_for(anext(source), timeout=3)
            if partial:
                assert first.errors[0].extensions["code"] == "PRECONDITION"
                assert not kube_transport["opens"]
            else:
                assert first.data["astroliftOnAppLogs"]["message"] == "first line"
                pending = asyncio.create_task(anext(source))
                await sync_to_async(PreviewEnvironment.objects.filter(pk=target.preview.pk).update)(
                    status="torn_down"
                )
                terminal = await asyncio.wait_for(pending, timeout=3)
                assert terminal.errors[0].extensions["code"] == "PRECONDITION"
                with pytest.raises(StopAsyncIteration):
                    await anext(source)
                assert all(response.released for response in kube_transport["responses"])
        finally:
            await source.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("status", ["failed", "expired"])
def test_unready_preview_artifact_is_initial_opaque_miss(preview_world, kube_transport, status):
    export, path = export_for(preview_world)
    AppLogExport.objects.filter(pk=export.pk).update(status=status)
    response = download(preview_world, path, actor=preview_world.actor)
    assert response.status_code == 404
    assert AppLogExport.objects.get(pk=export.pk).consumed_at is None


def test_deleted_preview_does_not_turn_old_artifact_into_public_capability(preview_world, kube_transport):
    target = preview_world
    export, path = export_for(target)
    AppLogExport.objects.filter(pk=export.pk).update(source_snapshot={})
    target.preview.soft_delete()
    assert download(target, path).status_code == 404
    assert download(target, path, actor=target.actor).status_code == 404


@pytest.mark.parametrize("surface", ["plural", "export"])
def test_deleted_preview_still_requires_exact_proof(preview_world, kube_transport, surface):
    target = preview_world
    target.preview.soft_delete()
    contracts.refused(target, surface, {}, kube_transport, environment_name=target.environment.name)
