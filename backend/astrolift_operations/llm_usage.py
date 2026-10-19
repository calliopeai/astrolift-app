"""Pure LLM key detection and token-usage source contracts (#30 extension).

Key detection inspects environment names by default; optional value-prefix
matching records only fingerprints. Existing token DTOs and source registration
remain available without any pricing table, monetary calculator or ledger.
No production usage source is wired by this module; Zentinelle owns token
observations, and these pure contracts do not prove live collection.
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


# ---- token usage ---------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts per (period, app, provider, model), optionally agent/run.

    The legacy ``period_yyyy_mm`` field names a calendar month. An app_id of
    zero represents an organization-level agent; run identity requires agent
    identity. This DTO does not price usage or persist a rollup.
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
    """Set when the source reports per-run rather than monthly counts."""

    def __post_init__(self) -> None:
        if self.org_id <= 0 or self.app_id < 0:
            raise ValueError("org_id must be positive; app_id must be non-negative (0 = org-level agent)")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        if not re.match(r"^\d{4}-\d{2}$", self.period_yyyy_mm):
            raise ValueError(f"period_yyyy_mm must be 'YYYY-MM', got {self.period_yyyy_mm!r}")
        if self.agent_run_id and not self.agent_id:
            raise ValueError("agent_run_id requires agent_id (a run belongs to an agent)")


# ---- usage source registry -----------------------------------------


UsageSourceCallable = Callable[[LLMProvider, int, str], tuple[TokenUsage, ...]]
"""Usage source contract:

  source(provider, org_id, period_yyyy_mm)
    -> tuple of TokenUsage (one per (app, model))

Plugin packages register an implementation that calls the
provider's token-usage API. Returns empty tuple when the org
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
