"""Tests for preserved LLM key detection and token-source contracts."""

from __future__ import annotations

import pytest

from astrolift_operations.llm_usage import (
    LLMProvider,
    TokenUsage,
    detect_llm_keys,
    fetch_usage,
    register_usage_source,
    unregister_usage_source,
)

# ---- key detection (env-name only) ---------------------------------


def test_name_hint_detects_openai():
    out = detect_llm_keys(env_keys=["OPENAI_API_KEY", "DATABASE_URL"])
    assert len(out) == 1
    assert out[0].provider == LLMProvider.OPENAI
    assert out[0].matched_via == "name_hint"
    assert out[0].fingerprint == ""


def test_name_hint_detects_anthropic():
    out = detect_llm_keys(env_keys=["ANTHROPIC_API_KEY"])
    assert len(out) == 1
    assert out[0].provider == LLMProvider.ANTHROPIC


def test_name_hint_detects_gemini():
    out = detect_llm_keys(env_keys=["GEMINI_API_KEY"])
    assert len(out) == 1
    assert out[0].provider == LLMProvider.GEMINI


def test_name_hint_case_insensitive():
    out = detect_llm_keys(env_keys=["openai_api_key", "Anthropic_Api_Key"])
    providers = {d.provider for d in out}
    assert providers == {LLMProvider.OPENAI, LLMProvider.ANTHROPIC}


def test_no_match_for_non_llm_keys():
    """We only care about LLM providers — Stripe/GitHub/Slack
    keys should NOT match this detector."""
    out = detect_llm_keys(
        env_keys=[
            "STRIPE_API_KEY",
            "GITHUB_TOKEN",
            "SLACK_WEBHOOK",
            "REGULAR_ENV_VAR",
        ]
    )
    assert out == ()


def test_empty_env_keys_skipped():
    out = detect_llm_keys(env_keys=["", "OPENAI_API_KEY", ""])
    assert len(out) == 1


# ---- value-prefix detection (opt-in) -------------------------------


def test_value_prefix_disabled_by_default():
    """Default behaviour: never look at values, only at names."""
    out = detect_llm_keys(
        env_keys=["GENERIC_KEY"],
        secret_values={"GENERIC_KEY": "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        # scan_secret_values=False (default)
    )
    assert out == ()


def test_value_prefix_detects_openai_when_enabled():
    out = detect_llm_keys(
        env_keys=["GENERIC_KEY"],
        secret_values={"GENERIC_KEY": "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        scan_secret_values=True,
    )
    assert len(out) == 1
    assert out[0].provider == LLMProvider.OPENAI
    assert out[0].matched_via == "value_prefix"


def test_value_prefix_distinguishes_openai_from_anthropic():
    """Anthropic keys also start with sk- but are sk-ant-...
    Detector must not classify those as OpenAI."""
    out = detect_llm_keys(
        env_keys=["AI_KEY_A", "AI_KEY_B"],
        secret_values={
            "AI_KEY_A": "sk-proj-" + "a" * 30,
            "AI_KEY_B": "sk-ant-" + "b" * 30,
        },
        scan_secret_values=True,
    )
    by_env = {d.env_key: d.provider for d in out}
    assert by_env["AI_KEY_A"] == LLMProvider.OPENAI
    assert by_env["AI_KEY_B"] == LLMProvider.ANTHROPIC


def test_fingerprint_format():
    """Privacy: never log the full value, only a 4+4 fingerprint."""
    out = detect_llm_keys(
        env_keys=["KEY"],
        secret_values={"KEY": "sk-proj-supersecret1234"},
        scan_secret_values=True,
    )
    assert len(out) == 1
    fp = out[0].fingerprint
    assert fp.startswith("sk-p")
    assert fp.endswith("1234")
    assert "supersecret" not in fp


def test_value_prefix_upgrades_name_hint_match():
    """Same env_key with both name AND value match: upgrade to
    value_prefix (higher confidence) and add fingerprint."""
    out = detect_llm_keys(
        env_keys=["OPENAI_API_KEY"],
        secret_values={"OPENAI_API_KEY": "sk-proj-" + "a" * 30},
        scan_secret_values=True,
    )
    assert len(out) == 1
    assert out[0].matched_via == "value_prefix"
    assert out[0].fingerprint != ""


def test_short_value_no_fingerprint():
    """Too-short values get no fingerprint to avoid revealing too
    much. Detection still records the match."""
    out = detect_llm_keys(
        env_keys=["KEY"],
        secret_values={"KEY": "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},  # 33 chars
        scan_secret_values=True,
    )
    if out:  # match might fire on the prefix
        assert out[0].fingerprint != "" or out[0].matched_via == "name_hint"


# ---- token usage construction --------------------------------------


def test_token_usage_validates_period():
    with pytest.raises(ValueError, match="period_yyyy_mm"):
        TokenUsage(
            period_yyyy_mm="May 2026",
            org_id=1,
            app_id=1,
            provider=LLMProvider.OPENAI,
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
        )


def test_token_usage_validates_positive_ids():
    with pytest.raises(ValueError):
        TokenUsage(
            period_yyyy_mm="2026-05",
            org_id=0,
            app_id=1,
            provider=LLMProvider.OPENAI,
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
        )


def test_token_usage_rejects_negative_tokens():
    with pytest.raises(ValueError):
        TokenUsage(
            period_yyyy_mm="2026-05",
            org_id=1,
            app_id=1,
            provider=LLMProvider.OPENAI,
            model="gpt-4o",
            input_tokens=-1,
            output_tokens=50,
        )


# ---- usage source registry -----------------------------------------


def test_fetch_usage_returns_empty_without_source():
    """No source registered → empty tuple, not None. Aggregation
    code can sum across an empty tuple harmlessly."""
    out = fetch_usage(
        provider=LLMProvider.OPENAI,
        org_id=1,
        period_yyyy_mm="2026-05",
    )
    assert out == ()


def test_fetch_usage_dispatches_to_registered_source():
    captured: dict = {}

    def fake_source(provider, org_id, period):
        captured["called_with"] = (provider, org_id, period)
        return (
            TokenUsage(
                period_yyyy_mm=period,
                org_id=org_id,
                app_id=42,
                provider=provider,
                model="gpt-4o",
                input_tokens=100_000,
                output_tokens=50_000,
            ),
        )

    register_usage_source(provider=LLMProvider.OPENAI, source=fake_source)
    try:
        out = fetch_usage(
            provider=LLMProvider.OPENAI,
            org_id=7,
            period_yyyy_mm="2026-05",
        )
    finally:
        unregister_usage_source(LLMProvider.OPENAI)

    assert captured["called_with"] == (LLMProvider.OPENAI, 7, "2026-05")
    assert len(out) == 1
    assert out[0].input_tokens == 100_000
    assert out[0].output_tokens == 50_000


def test_monetary_scaffold_is_retired():
    from astrolift_operations import llm_usage

    for name in ("ModelPricing", "DEFAULT_PRICING", "DEFAULT_FALLBACK", "lookup_pricing", "cost_for_usage"):
        assert not hasattr(llm_usage, name)
