"""
LLM token usage + cost attribution per app (#30 extension).

App owners stash LLM API keys (OpenAI, Anthropic, Gemini, AWS
Bedrock, Azure OpenAI) in env vars or secret bundles. The
platform never provisioned those — but they're often the largest
line item on a tenant's bill. This module tracks token-level
usage per (app, provider, model) so cost dashboards can show
'API costs $4,200/month, mostly Claude on the search-rerank app'.

Three pieces:

* **Key detection** — narrow pattern set for the LLM providers
  we care about. Privacy-safe (env-name match by default;
  value-prefix match opt-in per org with fingerprint-only
  recording).
* **Token-level usage tracking** — ``TokenUsage`` dataclass
  per (period, app, provider, model) with input + output token
  counts. Cost is computed from a pricing table.
* **Pricing table + cost calculator** — per-million-token rates
  per (provider, model) with sane defaults seeded; operators
  override via DB rows.

The actual usage-fetch from each LLM provider's API
(OpenAI ``/v1/usage``, Anthropic equivalent, etc.) lives in
plugin packages registered via ``register_usage_source``.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum


class LLMProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    AWS_BEDROCK = "aws_bedrock"
    AZURE_OPENAI = "azure_openai"


# ---- key detection -------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _KeyPattern:
    provider: LLMProvider
    name_hints: tuple[str, ...]
    """Lowercase substrings to look for in env-var NAMES. Privacy-
    safe; env names aren't secrets."""

    value_prefix: re.Pattern[str] | None
    """Regex against the leading bytes of a secret value. Only
    used when ``scan_secret_values=True`` per org."""


_PATTERNS: tuple[_KeyPattern, ...] = (
    _KeyPattern(
        provider=LLMProvider.OPENAI,
        name_hints=("openai_api_key", "openai_key"),
        value_prefix=re.compile(r"^sk-(?!ant-)[A-Za-z0-9_-]{20,}"),
    ),
    _KeyPattern(
        provider=LLMProvider.ANTHROPIC,
        name_hints=(
            "anthropic_api_key",
            "anthropic_key",
            "claude_api_key",
        ),
        value_prefix=re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}"),
    ),
    _KeyPattern(
        provider=LLMProvider.GEMINI,
        name_hints=("gemini_api_key", "google_genai_key", "google_ai_key"),
        # Google API keys: AIza followed by 35 chars
        value_prefix=re.compile(r"^AIza[A-Za-z0-9_-]{35}"),
    ),
    _KeyPattern(
        provider=LLMProvider.AWS_BEDROCK,
        name_hints=("bedrock_api_key", "aws_bedrock_key"),
        # Bedrock uses standard AWS access keys (AKIA...) — caller
        # disambiguates from generic IAM keys via the env var name.
        value_prefix=None,
    ),
    _KeyPattern(
        provider=LLMProvider.AZURE_OPENAI,
        name_hints=(
            "azure_openai_key",
            "azure_openai_api_key",
            "azure_oai_key",
        ),
        # Azure OpenAI uses 32-char hex keys; same shape as
        # several Azure resources, so name-hint is the only
        # reliable signal.
        value_prefix=re.compile(r"^[a-f0-9]{32}$"),
    ),
)


@dataclasses.dataclass(frozen=True, slots=True)
class LLMKeyDetection:
    """One detected LLM key in an app's env."""

    provider: LLMProvider
    env_key: str
    matched_via: str  # 'name_hint' | 'value_prefix'
    fingerprint: str = ""
    """``<first-4>...<last-4>`` for value matches; empty for
    name-only matches."""


def _fingerprint(value: str) -> str:
    if len(value) < 12:
        return ""
    return f"{value[:4]}...{value[-4:]}"


def detect_llm_keys(
    *,
    env_keys: Iterable[str],
    secret_values: Mapping[str, str] | None = None,
    scan_secret_values: bool = False,
) -> tuple[LLMKeyDetection, ...]:
    """Scan an app's env-var NAMES (and optionally values) for LLM
    provider keys.

    Default: name-only matching (cheap, privacy-safe). Operators
    opt into value-prefix scanning by passing
    ``scan_secret_values=True`` plus the actual value map.

    Caller is responsible for never logging the matched values —
    detector only records a 4+4 fingerprint for value matches.
    """
    out: list[LLMKeyDetection] = []
    seen: set[tuple[str, LLMProvider]] = set()

    for env_key in env_keys:
        if not env_key:
            continue
        env_lower = env_key.lower()

        for pattern in _PATTERNS:
            for hint in pattern.name_hints:
                if hint in env_lower:
                    key = (env_key, pattern.provider)
                    if key not in seen:
                        out.append(
                            LLMKeyDetection(
                                provider=pattern.provider,
                                env_key=env_key,
                                matched_via="name_hint",
                            )
                        )
                        seen.add(key)
                    break

        if scan_secret_values and secret_values is not None:
            value = secret_values.get(env_key, "")
            if not value:
                continue
            for pattern in _PATTERNS:
                if pattern.value_prefix is None:
                    continue
                if pattern.value_prefix.match(value):
                    key = (env_key, pattern.provider)
                    fp = _fingerprint(value)
                    # Upgrade an existing name_hint detection to
                    # value_prefix (higher confidence) and add
                    # the fingerprint.
                    upgraded = False
                    for i, d in enumerate(out):
                        if d.env_key == env_key and d.provider == pattern.provider:
                            out[i] = LLMKeyDetection(
                                provider=d.provider,
                                env_key=d.env_key,
                                matched_via="value_prefix",
                                fingerprint=fp,
                            )
                            upgraded = True
                            break
                    if not upgraded:
                        out.append(
                            LLMKeyDetection(
                                provider=pattern.provider,
                                env_key=env_key,
                                matched_via="value_prefix",
                                fingerprint=fp,
                            )
                        )
                        seen.add(key)
                    break

    return tuple(out)


# ---- token usage + pricing -----------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TokenUsage:
    """One billing-period rollup per (app, provider, model).

    ``period_yyyy_mm`` is the billing month (e.g. '2026-05').
    Token counts are sums across the period.

    Two attribution shapes:
      - **App built-in inference**: app_id set, agent_id empty.
        The tenant's app calls the LLM provider as part of its
        feature set; cost attributes to the app.
      - **Platform-run agent**: app_id set to the owning app
        (or 0 for org-level agents), agent_id + agent_run_id
        set. Cost attributes to the agent run for per-run
        budget caps; the monthly rollup aggregates them.
    """

    period_yyyy_mm: str
    org_id: int
    app_id: int
    provider: LLMProvider
    model: str
    input_tokens: int
    output_tokens: int
    agent_id: str = ""
    """Set when tokens were consumed by a platform-run agent.
    Empty for tenant-app inference."""

    agent_run_id: str = ""
    """Set when the rollup is per-run rather than monthly. The
    real-time cost-cap path writes per-run rows; the monthly
    aggregator sums them."""

    def __post_init__(self) -> None:
        if self.org_id <= 0 or self.app_id < 0:
            raise ValueError("org_id must be positive; app_id must be non-negative (0 = org-level agent)")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        if not re.match(r"^\d{4}-\d{2}$", self.period_yyyy_mm):
            raise ValueError(f"period_yyyy_mm must be 'YYYY-MM', got {self.period_yyyy_mm!r}")
        if self.agent_run_id and not self.agent_id:
            raise ValueError("agent_run_id requires agent_id (a run belongs to an agent)")


@dataclasses.dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-million-token rates. USD."""

    provider: LLMProvider
    model: str
    input_per_million_usd: float
    output_per_million_usd: float

    def __post_init__(self) -> None:
        if self.input_per_million_usd < 0 or self.output_per_million_usd < 0:
            raise ValueError("rates must be non-negative")


# Default pricing table (USD per million tokens). Approximate as of
# 2026-Q2; operators override per-org via a DB-backed table when
# they have negotiated rates. Listing only the models we expect
# tenants to actually use; missing models fall through to
# ``DEFAULT_FALLBACK`` so unknown-model usage doesn't get a $0
# free pass (better to overestimate cost than miss it).
DEFAULT_PRICING: tuple[ModelPricing, ...] = (
    # OpenAI
    ModelPricing(LLMProvider.OPENAI, "gpt-4o", 2.50, 10.00),
    ModelPricing(LLMProvider.OPENAI, "gpt-4o-mini", 0.15, 0.60),
    ModelPricing(LLMProvider.OPENAI, "o1", 15.00, 60.00),
    ModelPricing(LLMProvider.OPENAI, "o1-mini", 1.10, 4.40),
    # Anthropic
    ModelPricing(LLMProvider.ANTHROPIC, "claude-opus-4", 15.00, 75.00),
    ModelPricing(LLMProvider.ANTHROPIC, "claude-opus-4-7", 15.00, 75.00),
    ModelPricing(LLMProvider.ANTHROPIC, "claude-sonnet-4", 3.00, 15.00),
    ModelPricing(LLMProvider.ANTHROPIC, "claude-sonnet-4-6", 3.00, 15.00),
    ModelPricing(LLMProvider.ANTHROPIC, "claude-haiku-4-5", 1.00, 5.00),
    # Google
    ModelPricing(LLMProvider.GEMINI, "gemini-1.5-pro", 1.25, 5.00),
    ModelPricing(LLMProvider.GEMINI, "gemini-1.5-flash", 0.075, 0.30),
    ModelPricing(LLMProvider.GEMINI, "gemini-2.0-flash", 0.10, 0.40),
)


# Conservative fallback when we get usage from a provider but don't
# recognize the model string. Picked to be HIGHER than typical so
# unknown-model spend doesn't go silently uncosted; UI flags
# 'estimated' so operators know to add the model to pricing.
DEFAULT_FALLBACK = ModelPricing(
    provider=LLMProvider.OPENAI,  # provider field unused in fallback
    model="<unknown>",
    input_per_million_usd=10.00,
    output_per_million_usd=30.00,
)


def lookup_pricing(
    *,
    provider: LLMProvider,
    model: str,
    overrides: Iterable[ModelPricing] = (),
) -> ModelPricing:
    """Find pricing for (provider, model). ``overrides`` lets the
    caller pass org-specific rates that win over defaults.

    Returns ``DEFAULT_FALLBACK`` (with its high rates) if the model
    isn't in either table — a missing entry should NEVER cost $0
    in a dashboard."""
    for table in (overrides, DEFAULT_PRICING):
        for p in table:
            if p.provider == provider and p.model == model:
                return p
    return DEFAULT_FALLBACK


def cost_for_usage(
    usage: TokenUsage,
    *,
    overrides: Iterable[ModelPricing] = (),
) -> float:
    """Compute USD cost for one TokenUsage rollup."""
    pricing = lookup_pricing(
        provider=usage.provider,
        model=usage.model,
        overrides=overrides,
    )
    input_cost = usage.input_tokens / 1_000_000 * pricing.input_per_million_usd
    output_cost = usage.output_tokens / 1_000_000 * pricing.output_per_million_usd
    return round(input_cost + output_cost, 4)


# ---- usage source registry -----------------------------------------


UsageSourceCallable = Callable[[LLMProvider, int, str], tuple[TokenUsage, ...]]
"""Usage source contract:

  source(provider, org_id, period_yyyy_mm)
    -> tuple of TokenUsage (one per (app, model))

Plugin packages register an implementation that calls the
provider's billing/usage API. Returns empty tuple when the org
hasn't authorized the platform to query their account.
"""


_SOURCES: dict[LLMProvider, UsageSourceCallable] = {}


def register_usage_source(
    *,
    provider: LLMProvider,
    source: UsageSourceCallable,
) -> None:
    _SOURCES[provider] = source


def unregister_usage_source(provider: LLMProvider) -> None:
    _SOURCES.pop(provider, None)


def fetch_usage(
    *,
    provider: LLMProvider,
    org_id: int,
    period_yyyy_mm: str,
) -> tuple[TokenUsage, ...]:
    """Returns an org's per-app usage for one provider in one
    period. Empty tuple when no source is registered (rather
    than None — caller's aggregation code can sum across an empty
    tuple harmlessly)."""
    source = _SOURCES.get(provider)
    if source is None:
        return ()
    return source(provider, org_id, period_yyyy_mm)
