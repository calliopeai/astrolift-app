"""Managed-model (Vertex auto-wire) tests for GKEClusterDriver.

``agent_model_env`` returns the Vertex env a Claude Code runner reads
(CLAUDE_CODE_USE_VERTEX + region + project + Sonnet/Haiku ids). The
workload-identity mint is not implemented for GKE yet, so
``ensure_agent_model_identity`` inherits the base default and fails fast —
the managed-model path is honest about what's wired.
"""

from __future__ import annotations

import pytest

from _sdk.cluster import ManagedModelNotSupportedError
from gcp.cluster_gke import GKEClusterDriver, GKEConfig


def _driver(*, project_id="acme", location="us-central1"):
    return GKEClusterDriver(
        config=GKEConfig(
            project_id=project_id,
            location=location,
            cluster_name="prod",
            container_client=object(),
        ),
        k8s_client_factory=lambda **kw: object(),
    )


def test_agent_model_env_vertex_defaults():
    env = _driver().agent_model_env(region="us-central1", provider_config={})
    assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert env["CLOUD_ML_REGION"] == "us-central1"
    assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "acme"
    assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4@20250514"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "claude-3-5-haiku@20241022"


def test_agent_model_env_region_falls_back_to_location():
    env = _driver(location="europe-west1").agent_model_env(region="", provider_config={})
    assert env["CLOUD_ML_REGION"] == "europe-west1"


def test_agent_model_env_project_and_model_overrides():
    env = _driver(project_id="").agent_model_env(
        region="us-central1",
        provider_config={
            "project_id": "override-proj",
            "vertex_model_id": "claude-x@1",
            "vertex_small_fast_model_id": "claude-y@2",
        },
    )
    assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "override-proj"
    assert env["ANTHROPIC_MODEL"] == "claude-x@1"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "claude-y@2"


def test_agent_model_env_requires_a_project():
    with pytest.raises(ManagedModelNotSupportedError):
        _driver(project_id="").agent_model_env(region="us-central1", provider_config={})


def test_ensure_agent_model_identity_not_supported_on_gke():
    # GKE Workload Identity binding isn't wired yet — the inherited base
    # default fails fast with an actionable message rather than pretending.
    with pytest.raises(ManagedModelNotSupportedError):
        _driver().ensure_agent_model_identity(
            namespace="ns",
            service_account="astrolift-agent-model",
            provider_config={},
        )
