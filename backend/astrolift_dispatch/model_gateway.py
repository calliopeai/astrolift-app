"""Model gateway mode for agent task and box pods (#1851).

An environment spec with ``model_gateway`` sends its agent's model traffic
through the Zentinelle gateway in the pod's own cluster (#1887) instead of
handing the pod a provider key. Every run gets its own agent key, minted with
the organization's Zentinelle install credential for that run alone
(calliopeai/zentinelle#400). The gateway authenticates it (``X-Zentinelle-Key``),
evaluates policy and swaps in the provider key the organization's tenant
stored in Zentinelle, so the provider key never enters the pod. The runner half
is the astrolift-agents image contract v7: the runtime sends the header and
refuses to boot when it cannot.

Opting in makes the gateway required. Every missing piece (the install flag,
the organization's connection, the cluster's gateway, a runtime that can send
the key) refuses the run with one sentence, and a failed mint fails it. Nothing
falls back to provider keys.

The pod's env follows one rule. The dispatcher's gateway wiring wins over every
other source: the key, the routed providers' base URLs and credentials, the
header variables a runner adds the key to (:data:`RESERVED_ENV_NAMES`, plus a
runtime's own switches such as goose's provider) are dropped from the spec,
its bindings, bundles and the Brief, and provider credentials under those
names are not even read from the secret store. Any other name that carries a
model provider's credential or endpoint (:data:`REFUSED_ENV_PATTERNS`) refuses
the run, whichever source delivers it: the agent network fence (#1850) allows
the public internet, so such a name would let the agent call a provider past
the gateway. A secret under an unrelated name is still delivered.

A key lives as long as its run: a task's for its timeout plus a margin,
renewed when a question extends its deadline, and a box's for its idle window,
renewed by the reaper while the box stays up, never past the lifetime
Zentinelle caps keys at. Stop and finish revoke it through the connection that
minted it, best effort with retries; the expiry is the backstop.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("astrolift_dispatch.model_gateway")

GATEWAY_KEY_ENV = "ASTROLIFT_MODEL_GATEWAY_KEY"

#: How long a key outlives the deadline it was minted for, so a run that ends
#: on time never meets an expired key.
KEY_TTL_MARGIN_SECONDS = 15 * 60
#: Zentinelle's ceiling for one key lifetime (#400).
MAX_KEY_TTL_SECONDS = 30 * 24 * 3600
#: A box's key window is its idle timeout, but no shorter than this, so a
#: renewal never has to land within a few reaper sweeps.
BOX_KEY_MIN_WINDOW_SECONDS = 3600
#: The window of a box that is never reaped for idleness (idle timeout 0).
NEVER_REAPED_BOX_KEY_WINDOW_SECONDS = 24 * 3600

#: Revocation is attempted this many times; the key's expiry covers the rest.
REVOKE_ATTEMPTS = 3
_RETRY_DELAYS_SECONDS = (0.25, 0.5)

_PROVIDER_BASE_URL_ENV = {"anthropic": "ANTHROPIC_BASE_URL", "openai": "OPENAI_BASE_URL"}
# Anthropic's SDKs append /v1/messages to the base URL, OpenAI's append
# /chat/completions or /responses, so the OpenAI base carries the /v1. Goose
# 1.37 derives v1/chat/completions from the same /v1.
_PROVIDER_BASE_PATH = {"anthropic": "", "openai": "/v1"}

#: The providers each catalog runtime calls, and so the base URLs it gets.
#: Per the astrolift-agents gateway support matrix: goose only through its
#: openai provider, calliope-cli only through anthropic; the multi-provider
#: CLIs and the bare base image get both.
RUNTIME_PROVIDERS: dict[str, tuple[str, ...]] = {
    "claude": ("anthropic",),
    "claude-code": ("anthropic",),
    "calliope-cli": ("anthropic",),
    "codex": ("openai",),
    "codex-cli": ("openai",),
    "goose": ("openai",),
    "aider": ("anthropic", "openai"),
    "opencode": ("anthropic", "openai"),
    "base": ("anthropic", "openai"),
}

#: Env a runtime needs to take the gateway at all, written by the dispatcher.
#: The goose runner refuses the gateway on any provider but openai, the only
#: one that sends custom headers, and gets its endpoint from OPENAI_BASE_URL.
RUNTIME_WIRING: dict[str, tuple[tuple[str, str], ...]] = {
    "goose": (("GOOSE_PROVIDER", "openai"),),
}

#: Catalog runtimes that cannot run behind the gateway, and why.
UNSUPPORTED_RUNTIMES: dict[str, str] = {
    "openhands": "runtime openhands cannot send the gateway key (its CLI builds its model client from "
    "LLM_* variables only)",
    "mistral": "no Zentinelle gateway route reaches Mistral yet",
    "deepseek": "no Zentinelle gateway route reaches DeepSeek yet",
}

#: Without a catalog runtime (a pinned image), the spec's agent type says
#: which provider it calls.
AGENT_TYPE_PROVIDERS: dict[str, tuple[str, ...]] = {"claude": ("anthropic",), "codex": ("openai",)}

#: The gateway takes these over in every gateway run, so they are dropped
#: from every other source: the key, the routed providers' base URLs and
#: credentials, and the header variables a runner adds the key to (an
#: explicit X-Zentinelle-Key entry there would win over it).
RESERVED_ENV_NAMES = frozenset(
    {
        GATEWAY_KEY_ENV,
        "ANTHROPIC_BASE_URL",
        "OPENAI_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "OPENAI_CUSTOM_HEADERS",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
    }
)

_PROVIDERS = r"(?:ANTHROPIC|OPENAI|AZURE_OPENAI|OPENROUTER|GROQ|XAI|MISTRAL|DEEPSEEK|GEMINI|GOOGLE|LLM)"

#: Names that carry a model provider's credential or endpoint, or switch a
#: runtime to a provider, other than the wiring the dispatcher writes. One of
#: them anywhere in a gateway pod's env refuses the run: the agent network
#: fence allows the public internet, so it would reach a provider past the
#: gateway. Matched against the whole name.
REFUSED_ENV_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        # Keys of the providers the gateway does not route, and litellm's generic one.
        r"(?:OPENROUTER|AZURE_OPENAI|GROQ|XAI|MISTRAL|DEEPSEEK|GEMINI|GOOGLE|LLM)_API_KEY",
        # aider's per-provider keys and endpoints.
        r"AIDER_\w+_API_(?:KEY|BASE)",
        # Bedrock and Vertex credentials. A gateway spec cannot also be a
        # managed-model spec, which is what would bring AWS credentials legitimately.
        r"AWS_BEARER_TOKEN_BEDROCK|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY",
        r"GOOGLE_APPLICATION_CREDENTIALS",
        # Provider endpoints. goose reads OPENAI_HOST ahead of OPENAI_BASE_URL.
        _PROVIDERS + r"_(?:BASE_URL|HOST|API_BASE|BASE_PATH|ENDPOINT)",
        # Switches that send a runtime to another provider.
        r"GOOSE_PROVIDER|CLAUDE_CODE_USE_BEDROCK|CLAUDE_CODE_USE_VERTEX",
    )
)

MODEL_GATEWAY_MANAGED_CONFLICT = (
    "this environment spec sends model traffic through the Zentinelle gateway, which does not proxy "
    "Bedrock or Vertex; turn off model_gateway or managed_model"
)


class ModelGatewayError(Exception):
    """A gateway-mode run cannot start. The message is the sentence the operator sees.

    ``maybe_minted`` marks a mint whose outcome is unknown (no answer, a
    server error, an unusable answer): a key may exist and is revoked.
    """

    def __init__(self, message: str, *, maybe_minted: bool = False) -> None:
        super().__init__(message)
        self.maybe_minted = maybe_minted


def _refusal(spec_slug: str, reason: str) -> ModelGatewayError:
    return ModelGatewayError(
        f"environment spec {spec_slug} sends its model traffic through the Zentinelle gateway, but {reason}"
    )


def refused_env_name(name: str) -> bool:
    return any(pattern.fullmatch(name) for pattern in REFUSED_ENV_PATTERNS)


def gateway_base_url(provider: str) -> str:
    from astrolift_operations.zentinelle_connect import GATEWAY_NAME, GATEWAY_NAMESPACE, GATEWAY_PORT

    return f"http://{GATEWAY_NAME}.{GATEWAY_NAMESPACE}.svc:{GATEWAY_PORT}{_PROVIDER_BASE_PATH[provider]}"


def runtime_providers(spec) -> tuple[str, ...]:
    """The providers the spec's runtime calls, or a refusal when it cannot use the gateway."""
    runtime = (getattr(spec, "runtime", "") or "").strip()
    if runtime in UNSUPPORTED_RUNTIMES:
        raise _refusal(spec.slug, UNSUPPORTED_RUNTIMES[runtime])
    if runtime:
        providers = RUNTIME_PROVIDERS.get(runtime)
        if providers is None:
            raise _refusal(spec.slug, f"runtime {runtime!r} is not one the gateway knows how to wire")
        return providers
    providers = AGENT_TYPE_PROVIDERS.get(str(getattr(spec, "agent_type", "") or ""))
    if providers is None:
        raise _refusal(spec.slug, f"agent type {spec.agent_type!r} names no provider the gateway serves")
    return providers


def _clamp_ttl(seconds: int) -> int:
    return max(1, min(int(seconds), MAX_KEY_TTL_SECONDS))


def task_key_ttl(task) -> int:
    """A task's key lives for its timeout, the Job's own deadline, plus the margin."""
    return _clamp_ttl(max(1, int(getattr(task, "timeout_seconds", 300) or 300)) + KEY_TTL_MARGIN_SECONDS)


def box_key_ttl(box) -> int:
    """A box's key window: its idle timeout (at least an hour), plus the margin.

    A box ends once idle for its timeout, so a key the reaper stopped renewing
    (the box is gone, or the control plane is down) outlives it by at most
    about one idle window.
    """
    idle = int(getattr(box, "idle_timeout_seconds", 0) or 0)
    window = NEVER_REAPED_BOX_KEY_WINDOW_SECONDS if idle == 0 else max(idle, BOX_KEY_MIN_WINDOW_SECONDS)
    return _clamp_ttl(window + KEY_TTL_MARGIN_SECONDS)


def task_agent_id(task) -> str:
    """The Zentinelle agent a task's key belongs to: one per run, never reused by another."""
    return f"astrolift-task-{str(task.guid).replace('-', '')}"


def box_agent_id(box) -> str:
    return f"astrolift-box-{str(box.guid).replace('-', '')}"


def redact(text: str, key) -> str:
    """Drop a minted key, raw or base64, from text bound for a log line or an error."""
    if key is None:
        return text
    encoded = base64.b64encode(key.api_key.encode("utf-8")).decode("ascii")
    return text.replace(key.api_key, "[redacted]").replace(encoded, "[redacted]")


def with_gateway_key(
    secret_manifest: dict | None, key, *, secret_name: str, namespace: str, owner_guid: str
) -> dict:
    """The run's Secret with the gateway key in it, created when the run had no other secret."""
    from astrolift_dispatch.agent_secrets import build_task_secret_manifest

    if secret_manifest is None:
        secret_manifest = build_task_secret_manifest(
            secret_name=secret_name, namespace=namespace, task_guid=owner_guid, resolved={}
        )
    secret_manifest.setdefault("stringData", {})[GATEWAY_KEY_ENV] = key.api_key
    return secret_manifest


@dataclass(frozen=True)
class ModelGateway:
    """What a gateway-mode run needs: the organization's connection, and how to wire the runtime."""

    connection: Any
    providers: tuple[str, ...]
    spec_slug: str
    runtime_env: tuple[tuple[str, str], ...] = ()

    def env(self, secret_name: str) -> list[dict]:
        env: list[dict] = [
            {"name": _PROVIDER_BASE_URL_ENV[provider], "value": gateway_base_url(provider)}
            for provider in self.providers
        ]
        env.extend({"name": name, "value": value} for name, value in self.runtime_env)
        env.append(
            {
                "name": GATEWAY_KEY_ENV,
                "valueFrom": {"secretKeyRef": {"name": secret_name, "key": GATEWAY_KEY_ENV}},
            }
        )
        return env

    @property
    def owned_env_names(self) -> frozenset[str]:
        """Every name the dispatcher's wiring takes over in this run."""
        return RESERVED_ENV_NAMES | {name for name, _value in self.runtime_env}

    def wire_container(self, container: dict, *, secret_name: str) -> None:
        """Refuse another provider's credential or endpoint, then write the wiring over every other source.

        Raises :class:`ModelGatewayError` naming the refused variables (never their values).
        """
        env = container.get("env") or []
        owned = self.owned_env_names
        refused = sorted(
            {name for entry in env if (name := entry.get("name")) not in owned and refused_env_name(name)}
        )
        if refused:
            raise _refusal(
                self.spec_slug,
                "its pod would also get another model provider's credential or endpoint, which reaches "
                f"the provider past the gateway: {', '.join(refused)}. Remove them from the spec, its "
                "secret bundles, its app's managed services and the agent's [environment], or turn off "
                "model_gateway",
            )
        container["env"] = [entry for entry in env if entry.get("name") not in owned] + self.env(secret_name)

    def mint(self, *, agent_id: str, ttl_seconds: int, name: str, deployment_id: str):
        from astrolift_operations.zentinelle_connect import ZentinelleConnectError, mint_agent_key

        try:
            return mint_agent_key(
                connection=self.connection,
                agent_id=agent_id,
                ttl_seconds=_clamp_ttl(ttl_seconds),
                name=name,
                deployment_id=deployment_id,
            )
        except ZentinelleConnectError as exc:
            # No answer, a server error, or an answer without a usable key:
            # Zentinelle may have minted one all the same.
            unknown = exc.status is None or exc.status >= 500 or 200 <= exc.status < 300
            raise ModelGatewayError(
                f"Zentinelle did not mint the run's gateway key, so it was not started: {exc.message}",
                maybe_minted=unknown,
            ) from None


def key_covers(key, seconds: int) -> bool:
    """Whether a minted key still works ``seconds`` from now (Zentinelle may cap its lifetime)."""
    from django.utils import timezone

    return key.expires_at is None or key.expires_at >= timezone.now() + timedelta(seconds=seconds)


def resolve_model_gateway(*, cluster, organization, spec) -> ModelGateway:
    """Check every piece a gateway-mode run needs, before anything is created or minted."""
    from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
    from astrolift_operations.zentinelle_connect import gateway_feature_enabled

    if getattr(spec, "managed_model", False):
        raise _refusal(
            spec.slug,
            "it also turns on managed_model, and the gateway does not proxy Bedrock or Vertex; turn one of them off",
        )
    providers = runtime_providers(spec)
    if not gateway_feature_enabled():
        raise _refusal(spec.slug, "the gateway is off for this install (ZENTINELLE_GATEWAY_ENABLED)")
    connection = ZentinelleConnection.objects.filter(organization_id=organization.pk).first()
    if connection is None:
        raise _refusal(spec.slug, f"organization {organization.slug} is not connected to Zentinelle")
    if connection.status != ZentinelleConnection.Status.CONNECTED:
        raise _refusal(
            spec.slug,
            f"Zentinelle no longer accepts organization {organization.slug}'s install credential; "
            "disconnect and connect again",
        )
    cluster_pk = getattr(cluster, "pk", None)
    gateway = ZentinelleClusterGateway.objects.filter(cluster_id=cluster_pk).first() if cluster_pk else None
    slug = getattr(cluster, "slug", "?")
    if gateway is None:
        raise _refusal(
            spec.slug, f"cluster {slug} runs no Zentinelle gateway; register the cluster with Zentinelle"
        )
    if gateway.connection_id != connection.pk:
        # One gateway per cluster, scoped to the tenants of the connection
        # that registered it: another organization's tenants.
        raise _refusal(
            spec.slug,
            f"the gateway on cluster {slug} was registered through another organization's Zentinelle connection",
        )
    if not gateway.gateway_enabled:
        raise _refusal(spec.slug, f"the gateway on cluster {slug} is turned off")
    if not gateway.gateway_deployed:
        raise _refusal(spec.slug, f"the gateway on cluster {slug} is not deployed")
    runtime = (getattr(spec, "runtime", "") or "").strip()
    return ModelGateway(
        connection=connection,
        providers=providers,
        spec_slug=spec.slug,
        runtime_env=RUNTIME_WIRING.get(runtime, ()),
    )


def _minting_connection(connection_id):
    """The connection that minted a run's key, even if it has since been disconnected."""
    from astrolift_operations.models import ZentinelleConnection

    if connection_id is None:
        return None
    return ZentinelleConnection.all_objects.filter(pk=connection_id).first()


def _live(connection) -> bool:
    return (
        connection is not None
        and connection.deleted_at is None
        and connection.status == connection.Status.CONNECTED
    )


def renew_run_key(
    *, connection_id, agent_id: str, ttl_seconds: int
) -> tuple[datetime | None, datetime | None]:
    """Let a run's live key work for ``ttl_seconds`` more, as the install that minted it.

    Returns its new expiry and its lifetime end. Raises
    :class:`ModelGatewayError`: the caller decides whether a run can
    continue without the renewal.
    """
    from astrolift_operations.zentinelle_connect import ZentinelleConnectError, renew_agent_key

    connection = _minting_connection(connection_id)
    if not _live(connection):
        raise ModelGatewayError(
            f"cannot renew gateway key {agent_id}: the Zentinelle connection that minted it is no longer "
            "connected, and only that install may renew it"
        )
    try:
        return renew_agent_key(connection=connection, agent_id=agent_id, ttl_seconds=_clamp_ttl(ttl_seconds))
    except ZentinelleConnectError as exc:
        raise ModelGatewayError(f"Zentinelle did not renew gateway key {agent_id}: {exc.message}") from None


def revoke_run_key(*, connection_id, agent_id: str, expect_missing: bool = False) -> bool:
    """Revoke a run's key as the install that minted it, best effort. Never raises.

    Returns whether Zentinelle confirmed the key dead. Anything else (the
    minting connection disconnected, Zentinelle not knowing the agent, the
    install refused) is logged as a warning and left to the key's expiry.
    ``expect_missing`` is for a mint that may not have happened, where an
    unknown agent is the expected answer.
    """
    from astrolift_operations.zentinelle_connect import Revocation, ZentinelleConnectError, revoke_agent_key
    from core.mutations import ErrorCode

    try:
        connection = _minting_connection(connection_id)
    except Exception:  # noqa: BLE001 - stop paths must not fail on revocation
        log.warning("model gateway: could not read the connection to revoke %s", agent_id, exc_info=True)
        return False
    if not _live(connection):
        log.warning(
            "model gateway: %s not revoked here: the Zentinelle connection that minted it is no longer "
            "connected (a disconnect Zentinelle was told about terminated its agents; after a forced one "
            "the key stops at its expiry)",
            agent_id,
        )
        return False
    reason = ""
    for attempt in range(REVOKE_ATTEMPTS):
        try:
            outcome = revoke_agent_key(connection=connection, agent_id=agent_id)
        except ZentinelleConnectError as exc:
            reason = exc.message
            if exc.code != ErrorCode.INTERNAL:
                break
        except Exception as exc:  # noqa: BLE001 - stop paths must not fail on revocation
            reason = f"{type(exc).__name__}: {exc}"
        else:
            if outcome == Revocation.REVOKED:
                log.info("model gateway: revoked %s", agent_id)
                return True
            if outcome == Revocation.UNKNOWN_AGENT and expect_missing:
                return True
            reason = {
                Revocation.UNKNOWN_AGENT: "Zentinelle knows no such agent for the install that minted it",
                Revocation.INSTALL_REFUSED: "Zentinelle no longer accepts the install that minted it",
            }[outcome]
            break
        if attempt < len(_RETRY_DELAYS_SECONDS):
            time.sleep(_RETRY_DELAYS_SECONDS[attempt])
    log.warning("model gateway: %s not revoked (%s); the key stops at its expiry", agent_id, reason)
    return False
