"""The platform's own model, on by default on every cloud (#2138)."""

from __future__ import annotations

import pytest

from astrolift_agents.services import platform_model as pm

ENV = (
    "ASTROLIFT_PLATFORM_MODEL_PROVIDER",
    "ASTROLIFT_PLATFORM_MODEL_ID",
    "ASTROLIFT_PLATFORM_MODEL_REGION",
    "ASTROLIFT_PLATFORM_MODEL_URL",
    "ANTHROPIC_API_KEY",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "GOOGLE_CLOUD_PROJECT",
    "GCP_PROJECT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_an_aws_install_uses_bedrock_through_its_regions_profile(clean_env):
    clean_env.setenv("AWS_REGION", "us-west-2")

    model, _ = pm.resolve()

    assert model == pm.PlatformModel(
        "bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0", region="us-west-2"
    )


@pytest.mark.parametrize(
    ("region", "prefix"), [("eu-west-1", "eu"), ("ap-southeast-2", "apac"), ("us-east-1", "us")]
)
def test_the_profile_follows_the_geography(region, prefix):
    assert pm.bedrock_profile_for(region).startswith(f"{prefix}.anthropic.")


def test_a_gcp_install_uses_vertex(clean_env):
    clean_env.setenv("GOOGLE_CLOUD_PROJECT", "acme-prod")

    model, _ = pm.resolve()

    assert (model.provider, model.project, model.model) == ("vertex", "acme-prod", pm.VERTEX_DEFAULT)


def test_an_azure_install_uses_its_openai_deployment(clean_env):
    clean_env.setenv("AZURE_OPENAI_ENDPOINT", "https://acme.openai.azure.com/")
    clean_env.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

    model, _ = pm.resolve()

    assert (model.provider, model.model, model.endpoint) == (
        "azure_openai",
        "gpt-4o-mini",
        "https://acme.openai.azure.com",
    )


def test_an_on_prem_install_uses_an_openai_compatible_endpoint(clean_env):
    clean_env.setenv("ASTROLIFT_PLATFORM_MODEL_URL", "http://vllm.models.svc:8000/v1")
    clean_env.setenv("ASTROLIFT_PLATFORM_MODEL_ID", "Qwen/Qwen2.5-7B-Instruct")

    model, _ = pm.resolve()

    assert model.provider == "openai_compatible"


def test_an_anthropic_key_still_wins(clean_env):
    clean_env.setenv("AWS_REGION", "us-west-2")
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    model, _ = pm.resolve()

    assert model.provider == "anthropic"


def test_an_operator_can_turn_it_off(clean_env):
    clean_env.setenv("AWS_REGION", "us-west-2")
    clean_env.setenv("ASTROLIFT_PLATFORM_MODEL_PROVIDER", "off")

    model, reason = pm.resolve()

    assert model is None and reason.startswith("turned off")


def test_nothing_to_go_on_is_reported_not_guessed():
    model, reason = pm.resolve()

    assert model is None and reason


def test_bedrock_answers_through_converse(monkeypatch):
    calls = {}

    class Runtime:
        def converse(self, **kwargs):
            calls.update(kwargs)
            return {"output": {"message": {"content": [{"text": "Go to "}, {"text": "/clusters"}]}}}

    monkeypatch.setattr("boto3.client", lambda service, region_name=None: Runtime())
    model = pm.PlatformModel("bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0", region="us-west-2")

    answer = pm.complete(model, system="s", prompt="where are clusters?")

    assert answer == "Go to /clusters"
    assert calls["modelId"] == model.model
    assert calls["system"] == [{"text": "s"}]


def test_a_provider_failure_is_one_error_type(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("AccessDeniedException")

    monkeypatch.setattr("boto3.client", boom)

    with pytest.raises(pm.PlatformModelError, match="bedrock call failed"):
        pm.complete(pm.PlatformModel("bedrock", "m", region="us-west-2"), system="s", prompt="p")
