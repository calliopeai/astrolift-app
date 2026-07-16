"""Tests for the ``upsert_agent_environment_spec`` management command."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Spec Org", slug="spec-cmd-org")


def test_creates_spec_with_config_and_refs(org):
    call_command(
        "upsert_agent_environment_spec",
        "--org",
        "spec-cmd-org",
        "--slug",
        "emr-bug-triage",
        "--agent-type",
        "claude",
        "--image-tag",
        "acct.dkr.ecr.us-west-2.amazonaws.com/astrolift/agent-claude:latest",
        "--config-repo",
        "steadymd/smd-agents",
        "--manifest-path",
        "agents/emr-bug-triage/astrolift.toml",
        "--env",
        "EMR_SERVICE_BASE_URL=https://emr.prd.smdinfra.net",
        "--secret",
        "EMR_AGENT_TOKEN=smd-emr-agent-token",
    )
    spec = AgentEnvironmentSpec.objects.get(organization=org, slug="emr-bug-triage")
    assert spec.agent_type == "claude"
    assert spec.config_repo == "steadymd/smd-agents"
    assert spec.config_manifest_path == "agents/emr-bug-triage/astrolift.toml"
    assert spec.name == "emr-bug-triage"  # defaults to slug
    assert spec.env_vars == {"EMR_SERVICE_BASE_URL": "https://emr.prd.smdinfra.net"}
    assert spec.secret_refs == [{"env_var": "EMR_AGENT_TOKEN", "uri": "smd-emr-agent-token"}]


def test_idempotent_full_replace(org):
    args = [
        "--org",
        "spec-cmd-org",
        "--slug",
        "a",
        "--agent-type",
        "claude",
        "--config-repo",
        "steadymd/smd-agents",
    ]
    call_command("upsert_agent_environment_spec", *args)
    call_command("upsert_agent_environment_spec", *args)
    # One row, not two — matched on (org, slug).
    assert AgentEnvironmentSpec.objects.filter(organization=org, slug="a").count() == 1


def test_unknown_org_errors():
    with pytest.raises(CommandError, match="organization not found"):
        call_command(
            "upsert_agent_environment_spec", "--org", "nope", "--slug", "a", "--agent-type", "claude"
        )


def test_bad_env_pair_errors(org):
    with pytest.raises(CommandError, match="KEY=VALUE"):
        call_command(
            "upsert_agent_environment_spec",
            "--org",
            "spec-cmd-org",
            "--slug",
            "a",
            "--agent-type",
            "claude",
            "--env",
            "NOPE",
        )


def test_invalid_agent_type_rejected(org):
    with pytest.raises((CommandError, SystemExit)):
        call_command(
            "upsert_agent_environment_spec", "--org", "spec-cmd-org", "--slug", "a", "--agent-type", "gpt5"
        )
