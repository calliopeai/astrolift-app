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

In gateway mode the pod takes none of :data:`RESERVED_ENV_NAMES` from a spec,
a binding, a bundle or the Brief: provider credentials are not even read from
the secret store, and the dispatcher's wiring goes in last, so no configuration
can point a runtime elsewhere or substitute another key. A secret under any
other name is still delivered; provider keys belong in Zentinelle, and the
agent network fence (#1850) is what bounds egress.

A key lives as long as its run: a task's for its timeout plus a margin,
renewed when a question extends its deadline, and a box's for its idle window,
renewed by the reaper while the box stays up. Stop and finish revoke it, best
effort with retries; the expiry is the backstop.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime
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
# /chat/completions or /responses, so the OpenAI base carries the /v1.
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

#: Never taken from a spec, binding, bundle or Brief in gateway mode: the
#: gateway wiring (the key, every base URL and header variable a runtime would
#: follow instead, the switches that send Claude Code to Bedrock or Vertex)
#: and the model provider credentials the runtimes read.
RESERVED_ENV_NAMES = frozenset(
    {
        GATEWAY_KEY_ENV,
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_CUSTOM_HEADERS",
        "DEEPSEEK_BASE_URL",
        "MISTRAL_BASE_URL",
        "LLM_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MISTRAL_API_KEY",
        "DEEPSEEK_API_KEY",
        "LLM_API_KEY",
    }
)


MODEL_GATEWAY_MANAGED_CONFLICT = (
    "this environment spec sends model traffic through the Zentinelle gateway, which does not proxy "
    "Bedrock or Vertex; turn off model_gateway or managed_model"
)


class ModelGatewayError(Exception):
    """A gateway-mode run cannot start. The message is the sentence the operator sees."""


def _refusal(spec, reason: str) -> ModelGatewayError:
    return ModelGatewayError(
        f"environment spec {spec.slug} sends its model traffic through the Zentinelle gateway, but {reason}"
    )


def gateway_base_url(provider: str) -> str:
    from astrolift_operations.zentinelle_connect import GATEWAY_NAME, GATEWAY_NAMESPACE, GATEWAY_PORT

    return f"http://{GATEWAY_NAME}.{GATEWAY_NAMESPACE}.svc:{GATEWAY_PORT}{_PROVIDER_BASE_PATH[provider]}"


def runtime_providers(spec) -> tuple[str, ...]:
    """The providers the spec's runtime calls, or a refusal when it cannot use the gateway."""
    runtime = (getattr(spec, "runtime", "") or "").strip()
    if runtime in UNSUPPORTED_RUNTIMES:
        raise _refusal(spec, UNSUPPORTED_RUNTIMES[runtime])
    if runtime:
        providers = RUNTIME_PROVIDERS.get(runtime)
        if providers is None:
            raise _refusal(spec, f"runtime {runtime!r} is not one the gateway knows how to wire")
        return providers
    providers = AGENT_TYPE_PROVIDERS.get(str(getattr(spec, "agent_type", "") or ""))
    if providers is None:
        raise _refusal(spec, f"agent type {spec.agent_type!r} names no provider the gateway serves")
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
    """What a gateway-mode run needs: the organization's connection, and which providers to route."""

    connection: Any
    providers: tuple[str, ...]

    def env(self, secret_name: str) -> list[dict]:
        env: list[dict] = [
            {"name": _PROVIDER_BASE_URL_ENV[provider], "value": gateway_base_url(provider)}
            for provider in self.providers
        ]
        env.append(
            {
                "name": GATEWAY_KEY_ENV,
                "valueFrom": {"secretKeyRef": {"name": secret_name, "key": GATEWAY_KEY_ENV}},
            }
        )
        return env

    def wire_container(self, container: dict, *, secret_name: str) -> None:
        """Drop every reserved name from the container env, then write the gateway wiring last."""
        kept = [entry for entry in container.get("env") or [] if entry.get("name") not in RESERVED_ENV_NAMES]
        container["env"] = kept + self.env(secret_name)

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
            raise ModelGatewayError(
                f"Zentinelle did not mint the run's gateway key, so it was not started: {exc.message}"
            ) from None


def resolve_model_gateway(*, cluster, organization, spec) -> ModelGateway:
    """Check every piece a gateway-mode run needs, before anything is created or minted."""
    from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
    from astrolift_operations.zentinelle_connect import gateway_feature_enabled

    if getattr(spec, "managed_model", False):
        raise _refusal(
            spec,
            "it also turns on managed_model, and the gateway does not proxy Bedrock or Vertex; turn one of them off",
        )
    providers = runtime_providers(spec)
    if not gateway_feature_enabled():
        raise _refusal(spec, "the gateway is off for this install (ZENTINELLE_GATEWAY_ENABLED)")
    connection = ZentinelleConnection.objects.filter(organization_id=organization.pk).first()
    if connection is None:
        raise _refusal(spec, f"organization {organization.slug} is not connected to Zentinelle")
    if connection.status != ZentinelleConnection.Status.CONNECTED:
        raise _refusal(
            spec,
            f"Zentinelle no longer accepts organization {organization.slug}'s install credential; "
            "disconnect and connect again",
        )
    cluster_pk = getattr(cluster, "pk", None)
    gateway = ZentinelleClusterGateway.objects.filter(cluster_id=cluster_pk).first() if cluster_pk else None
    slug = getattr(cluster, "slug", "?")
    if gateway is None:
        raise _refusal(
            spec, f"cluster {slug} runs no Zentinelle gateway; register the cluster with Zentinelle"
        )
    if gateway.connection_id != connection.pk:
        # One gateway per cluster, scoped to the tenants of the connection
        # that registered it: another organization's tenants.
        raise _refusal(
            spec,
            f"the gateway on cluster {slug} was registered through another organization's Zentinelle connection",
        )
    if not gateway.gateway_enabled:
        raise _refusal(spec, f"the gateway on cluster {slug} is turned off")
    if not gateway.gateway_deployed:
        raise _refusal(spec, f"the gateway on cluster {slug} is not deployed")
    return ModelGateway(connection=connection, providers=providers)


def _live_connection(organization_id):
    from astrolift_operations.models import ZentinelleConnection

    return ZentinelleConnection.objects.filter(organization_id=organization_id).first()


def renew_run_key(*, organization_id, agent_id: str, ttl_seconds: int) -> datetime | None:
    """Let a run's live key work for ``ttl_seconds`` from now. Returns its new expiry.

    Raises :class:`ModelGatewayError`: the caller decides whether a run can
    continue without the renewal.
    """
    from astrolift_operations.zentinelle_connect import ZentinelleConnectError, renew_agent_key

    connection = _live_connection(organization_id)
    if connection is None:
        raise ModelGatewayError(
            f"cannot renew gateway key {agent_id}: the organization is not connected to Zentinelle"
        )
    try:
        return renew_agent_key(connection=connection, agent_id=agent_id, ttl_seconds=_clamp_ttl(ttl_seconds))
    except ZentinelleConnectError as exc:
        raise ModelGatewayError(f"Zentinelle did not renew gateway key {agent_id}: {exc.message}") from None


def revoke_run_key(*, organization_id, agent_id: str) -> None:
    """Revoke a run's key, best effort, retrying transient failures. Never raises.

    A key that cannot be revoked stops at its expiry.
    """
    from astrolift_operations.zentinelle_connect import ZentinelleConnectError, revoke_agent_key
    from core.mutations import ErrorCode

    try:
        connection = _live_connection(organization_id)
    except Exception:  # noqa: BLE001 - stop paths must not fail on revocation
        log.warning("model gateway: could not read the connection to revoke %s", agent_id, exc_info=True)
        return
    if connection is None or connection.status != connection.Status.CONNECTED:
        # A disconnected or revoked install took its agents with it.
        return
    reason = ""
    for attempt in range(REVOKE_ATTEMPTS):
        try:
            revoke_agent_key(connection=connection, agent_id=agent_id)
        except ZentinelleConnectError as exc:
            reason = exc.message
            if exc.code != ErrorCode.INTERNAL:
                break
        except Exception as exc:  # noqa: BLE001 - stop paths must not fail on revocation
            reason = f"{type(exc).__name__}: {exc}"
        else:
            log.info("model gateway: revoked %s", agent_id)
            return
        if attempt < len(_RETRY_DELAYS_SECONDS):
            time.sleep(_RETRY_DELAYS_SECONDS[attempt])
    log.warning("model gateway: could not revoke %s (%s); the key stops at its expiry", agent_id, reason)
