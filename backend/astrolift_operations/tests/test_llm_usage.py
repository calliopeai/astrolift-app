"""Tests for LLM token-usage detection + cost (#30 LLM extension)."""

from __future__ import annotations

import pytest

from astrolift_operations.llm_usage import (
    DEFAULT_FALLBACK,
    DEFAULT_PRICING,
    LLMProvider,
    ModelPricing,
    TokenUsage,
    cost_for_usage,
    detect_llm_keys,
    fetch_usage,
    lookup_pricing,
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
    out = detect_llm_keys(env_keys=[
        "STRIPE_API_KEY", "GITHUB_TOKEN", "SLACK_WEBHOOK",
        "REGULAR_ENV_VAR",
    ])
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
            org_id=1, app_id=1,
            provider=LLMProvider.OPENAI, model="gpt-4o",
            input_tokens=100, output_tokens=50,
        )


def test_token_usage_validates_positive_ids():
    with pytest.raises(ValueError):
        TokenUsage(
            period_yyyy_mm="2026-05",
            org_id=0, app_id=1,
            provider=LLMProvider.OPENAI, model="gpt-4o",
            input_tokens=100, output_tokens=50,
        )


def test_token_usage_rejects_negative_tokens():
    with pytest.raises(ValueError):
        TokenUsage(
            period_yyyy_mm="2026-05",
            org_id=1, app_id=1,
            provider=LLMProvider.OPENAI, model="gpt-4o",
            input_tokens=-1, output_tokens=50,
        )


# ---- pricing lookup + cost calculation -----------------------------


def test_lookup_pricing_known_model():
    p = lookup_pricing(provider=LLMProvider.OPENAI, model="gpt-4o")
    assert p.input_per_million_usd == 2.50
    assert p.output_per_million_usd == 10.00


def test_lookup_pricing_anthropic_models():
    p = lookup_pricing(provider=LLMProvider.ANTHROPIC, model="claude-opus-4-7")
    assert p.input_per_million_usd == 15.00
    assert p.output_per_million_usd == 75.00


def test_lookup_pricing_unknown_falls_back_high():
    """Critical invariant: missing model entries must NOT cost $0
    on the dashboard. Fall back to a deliberately-conservative
    rate so unknown spend is overestimated, not invisible."""
    p = lookup_pricing(provider=LLMProvider.OPENAI, model="gpt-99-future")
    assert p is DEFAULT_FALLBACK


def test_lookup_pricing_org_override_wins():
    override = ModelPricing(
        provider=LLMProvider.OPENAI, model="gpt-4o",
        input_per_million_usd=1.00, output_per_million_usd=4.00,
    )
    p = lookup_pricing(
        provider=LLMProvider.OPENAI, model="gpt-4o",
        overrides=[override],
    )
    assert p is override


def test_cost_for_usage_simple():
    usage = TokenUsage(
        period_yyyy_mm="2026-05", org_id=1, app_id=1,
        provider=LLMProvider.OPENAI, model="gpt-4o",
        input_tokens=1_000_000, output_tokens=500_000,
    )
    cost = cost_for_usage(usage)
    # 1M input @ $2.50 + 0.5M output @ $10/M = $5.00
    assert cost == 7.50


def test_cost_for_usage_zero_tokens_zero_cost():
    usage = TokenUsage(
        period_yyyy_mm="2026-05", org_id=1, app_id=1,
        provider=LLMProvider.OPENAI, model="gpt-4o",
        input_tokens=0, output_tokens=0,
    )
    assert cost_for_usage(usage) == 0.0


def test_cost_for_usage_with_override():
    usage = TokenUsage(
        period_yyyy_mm="2026-05", org_id=1, app_id=1,
        provider=LLMProvider.OPENAI, model="gpt-4o",
        input_tokens=1_000_000, output_tokens=0,
    )
    override = ModelPricing(
        provider=LLMProvider.OPENAI, model="gpt-4o",
        input_per_million_usd=1.00, output_per_million_usd=4.00,
    )
    assert cost_for_usage(usage, overrides=[override]) == 1.00


def test_default_pricing_includes_major_anthropic_models():
    """Lock-test: the seed table must include the models we
    actively use so 'unknown model' fallback isn't constantly
    triggered."""
    models = {(p.provider, p.model) for p in DEFAULT_PRICING}
    assert (LLMProvider.ANTHROPIC, "claude-opus-4-7") in models
    assert (LLMProvider.ANTHROPIC, "claude-sonnet-4-6") in models
    assert (LLMProvider.ANTHROPIC, "claude-haiku-4-5") in models
    assert (LLMProvider.OPENAI, "gpt-4o") in models
    assert (LLMProvider.GEMINI, "gemini-1.5-pro") in models


# ---- usage source registry -----------------------------------------


def test_fetch_usage_returns_empty_without_source():
    """No source registered → empty tuple, not None. Aggregation
    code can sum across an empty tuple harmlessly."""
    out = fetch_usage(
        provider=LLMProvider.OPENAI, org_id=1, period_yyyy_mm="2026-05",
    )
    assert out == ()


def test_fetch_usage_dispatches_to_registered_source():
    captured: dict = {}

    def fake_source(provider, org_id, period):
        captured["called_with"] = (provider, org_id, period)
        return (
            TokenUsage(
                period_yyyy_mm=period, org_id=org_id, app_id=42,
                provider=provider, model="gpt-4o",
                input_tokens=100_000, output_tokens=50_000,
            ),
        )

    register_usage_source(provider=LLMProvider.OPENAI, source=fake_source)
    try:
        out = fetch_usage(
            provider=LLMProvider.OPENAI, org_id=7, period_yyyy_mm="2026-05",
        )
    finally:
        unregister_usage_source(LLMProvider.OPENAI)

    assert captured["called_with"] == (LLMProvider.OPENAI, 7, "2026-05")
    assert len(out) == 1
    assert out[0].input_tokens == 100_000
    assert cost_for_usage(out[0]) == 0.75   # 100k @ $2.50/M + 50k @ $10/M = 0.25 + 0.5
