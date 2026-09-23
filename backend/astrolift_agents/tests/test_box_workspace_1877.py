"""Opt-in workspace setup for agent boxes (#1877).

``box_workspace`` on an environment spec makes a box boot the agent's payload
and run the runner's workspace setup before its session starts. The box half
lives with the rest of the box tests in ``test_agent_box.py``. Here: the
spec field on the GraphQL surface, and the task path's payload env, which
the box now shares.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test import override_settings

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    CreateAgentEnvironmentSpecInput,
    UpdateAgentEnvironmentSpecInput,
)
from astrolift_dispatch.brief_injector import brief_env_vars
from astrolift_identity.models import Organization
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Box Workspace Org", slug="box-workspace-org")


# ---- the spec field --------------------------------------------------


def _create(info, org, **fields):
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        return AgentsMutation().create_agent_environment_spec(
            info,
            input=CreateAgentEnvironmentSpecInput(name="Spec", slug="spec", agent_type="claude", **fields),
            org_id=str(org.guid),
        )


def _update(info, org, **fields):
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        return AgentsMutation().update_agent_environment_spec(
            info, slug="spec", input=UpdateAgentEnvironmentSpecInput(**fields)
        )


def test_a_spec_leaves_the_box_bare_unless_it_asks(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)

    result = _create(info, org)

    assert result.ok is True, result.errors
    assert result.data.box_workspace is False
    assert AgentEnvironmentSpec.objects.get(organization=org, slug="spec").box_workspace is False


def test_create_threads_box_workspace(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)

    result = _create(info, org, box_workspace=True)

    assert result.ok is True, result.errors
    assert result.data.box_workspace is True
    assert AgentEnvironmentSpec.objects.get(organization=org, slug="spec").box_workspace is True


def test_update_turns_it_on_and_off_without_touching_the_rest(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    AgentEnvironmentSpec.objects.create(
        organization=org, name="Spec", slug="spec", agent_type="claude", run_as_non_root=True
    )

    on = _update(info, org, box_workspace=True)
    assert on.ok is True, on.errors
    assert on.data.box_workspace is True
    assert on.data.run_as_non_root is True

    untouched = _update(info, org, name="Renamed")
    assert untouched.data.box_workspace is True

    off = _update(info, org, box_workspace=False)
    assert off.data.box_workspace is False
    assert AgentEnvironmentSpec.objects.get(organization=org, slug="spec").box_workspace is False


def test_update_is_denied_without_the_grant(permission_resolver, info, org):
    AgentEnvironmentSpec.objects.create(organization=org, name="Spec", slug="spec", agent_type="claude")

    result = _update(info, org, box_workspace=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert AgentEnvironmentSpec.objects.get(organization=org, slug="spec").box_workspace is False


# ---- the task path, now shared with the box --------------------------


def _task(org, *, storage_key, snapshot, timeout_seconds=300):
    brief = SimpleNamespace(
        guid="b-1877", content_hash="c" * 64, storage_key=storage_key, manifest_snapshot=snapshot
    )
    return SimpleNamespace(
        guid="t-1877",
        organization=org,
        timeout_seconds=timeout_seconds,
        brief_id=1,
        brief=brief,
        dispatch_input=None,
        callback_token_hash="",
    )


_PAYLOAD = {"payload_sha256": "cd" * 32, "manifest_path": "agents/x/astrolift.toml", "requires_payload": True}


@pytest.fixture
def minted(monkeypatch):
    import astrolift_pipelines.artifact_store as artifact_store

    calls: list[dict] = []

    def presigned_download_url(*, org, blob_key, expires_in=900):
        calls.append({"blob_key": blob_key, "expires_in": expires_in})
        return f"https://blobs.example.net/{blob_key}"

    monkeypatch.setattr(artifact_store, "presigned_download_url", presigned_download_url)
    return calls


@override_settings(PLATFORM_API_URL="https://astro.example.net")
def test_a_task_still_receives_its_payload(org, minted):
    env = {e["name"]: e["value"] for e in brief_env_vars(_task(org, storage_key="p.zip", snapshot=_PAYLOAD))}

    assert env["ASTROLIFT_PAYLOAD_URL"] == "https://blobs.example.net/p.zip"
    assert env["ASTROLIFT_PAYLOAD_HASH"] == "sha256:" + "cd" * 32
    assert env["ASTROLIFT_MANIFEST_PATH"] == "agents/x/astrolift.toml"
    # A task's URL covers its run: a 15-minute floor, the timeout plus slack above it.
    assert minted == [{"blob_key": "p.zip", "expires_in": 900}]


@override_settings(PLATFORM_API_URL="https://astro.example.net")
def test_a_long_task_gets_a_url_that_outlives_it(org, minted):
    brief_env_vars(_task(org, storage_key="p.zip", snapshot=_PAYLOAD, timeout_seconds=7200))

    assert minted == [{"blob_key": "p.zip", "expires_in": 7800}]


@override_settings(PLATFORM_API_URL="https://astro.example.net")
def test_a_task_that_requires_a_missing_bundle_still_fails(org, minted):
    with pytest.raises(RuntimeError, match="requires a payload bundle"):
        brief_env_vars(_task(org, storage_key="", snapshot=_PAYLOAD))
    assert minted == []


@override_settings(PLATFORM_API_URL="https://astro.example.net")
def test_an_optional_bundle_that_cannot_be_signed_is_skipped(org, monkeypatch):
    import astrolift_pipelines.artifact_store as artifact_store

    def unavailable(**_kwargs):
        raise RuntimeError("no blob store")

    monkeypatch.setattr(artifact_store, "presigned_download_url", unavailable)
    snapshot = {**_PAYLOAD, "requires_payload": False}

    env = {e["name"] for e in brief_env_vars(_task(org, storage_key="p.zip", snapshot=snapshot))}

    assert "ASTROLIFT_PAYLOAD_URL" not in env
    assert "AGENT_PROMPT" in env
