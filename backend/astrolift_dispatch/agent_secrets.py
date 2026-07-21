"""Agent task secret resolution + K8s materialization (#1173).

An :class:`~astrolift_agents.models.AgentEnvironmentSpec` carries
``secret_refs`` — ``[{"uri": ..., "env_var": ...}]`` — that *name* secrets
in the install's secret store. The control plane never persists the
values; the dispatcher resolves them from the per-cluster
``SecretsBackend`` driver at spawn time and materializes them into a
per-task K8s Secret the pod mounts via ``secretKeyRef`` (mirrors the
pipeline secret plumbing in ``astrolift_pipelines.secret_plumbing``).

Write/read symmetry (the property the whole feature turns on): every
value is stored under a single conventional key, :data:`SECRET_VALUE_KEY`,
at the ref's ``uri`` path. ``setAgentSecretValue`` writes
``upsert(uri, {"value": <value>})``; this reader reads
``get(uri)["value"]``. The two paths MUST agree on that key, and both
resolve the store from the *same* cluster (see
:func:`astrolift_agents.services.agent_cluster.resolve_agent_cluster`), so
a value set through the platform lands in the exact store the pod reads at
launch. A plain-string secret pre-created out-of-band also resolves,
because the AWS driver wraps a bare ``SecretString`` under ``"value"``.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("astrolift_dispatch.agent_secrets")

# The single conventional key each agent secret ref's value is stored
# under. Write and read paths MUST use the same key or resolution breaks.
SECRET_VALUE_KEY = "value"

# Labels stamped on the per-task Secret so it is discoverable + cleaned up
# alongside the task's Job (K8sJobSpawner.stop deletes by name).
_MANAGED_BY = "astrolift-agents"


class AgentSecretResolutionError(Exception):
    """Raised when one or more of a spec's secret refs cannot be resolved.

    Collects *every* unresolved ref (not just the first) so a failed spawn
    surfaces a single readable message listing all of them — mirrors
    ``astrolift_pipelines.secret_plumbing.SecretResolutionError``.
    """

    def __init__(self, spec_slug: str, missing: list[str], *, reason: str | None = None) -> None:
        self.spec_slug = spec_slug
        self.missing = list(missing)
        msg = (
            f"agent environment spec {spec_slug!r} has "
            f"{len(self.missing)} unresolved secret ref(s): {', '.join(self.missing)}"
        )
        if reason:
            msg = f"{msg} ({reason})"
        super().__init__(msg)


def normalize_secret_refs(raw: Any) -> list[dict[str, str]]:
    """Coerce a stored ``secret_refs`` payload to ``[{"uri", "env_var"}]``.

    Tolerant of the JSONField's loose shape: entries missing a non-empty
    ``uri`` or ``env_var`` (or not dict-shaped) are skipped rather than
    raising, so a malformed row degrades to "no such ref" instead of a
    spawn-time crash.
    """
    out: list[dict[str, str]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        uri = str(entry.get("uri") or "").strip()
        env_var = str(entry.get("env_var") or "").strip()
        if uri and env_var:
            out.append({"uri": uri, "env_var": env_var})
    return out


def resolve_secrets_backend(cluster) -> Any:
    """Resolve the cluster's ``secrets`` driver (``SecretsBackend``).

    Raises ``core.app_deploy.AppDeployError`` when the cluster's provider
    plugin registers no ``secrets`` driver.
    """
    from core.app_deploy import driver_for_capability

    return driver_for_capability(cluster, "secrets")


# ---------------------------------------------------------------------------
# Store contract — the one place the {"value": ...} shape + uri-as-path lives
# ---------------------------------------------------------------------------


def write_secret_value(backend, uri: str, value: str) -> None:
    """Write ``value`` to ``uri`` in the store (create-or-update).

    ``upsert`` covers "create the shell + set the first version" and
    "rotate an existing value" identically, so this is the whole write
    path for set/rotate.
    """
    backend.upsert(uri, {SECRET_VALUE_KEY: value})


def read_secret_value(backend, uri: str) -> str | None:
    """Read the value stored at ``uri``.

    Returns ``None`` when the secret is absent OR present-but-empty (an
    empty shell with no current version) — both are "not usable at spawn"
    and the preflight treats them the same.
    """
    payload = backend.get(uri)
    if not payload:
        return None
    value = payload.get(SECRET_VALUE_KEY)
    return value or None


def delete_secret_value(backend, uri: str) -> None:
    """Delete the secret stored at ``uri`` (soft-delete window per driver)."""
    backend.delete(uri)


# ---------------------------------------------------------------------------
# Pod env + per-task Secret materialization
# ---------------------------------------------------------------------------


def task_secret_name(job_name: str) -> str:
    """Deterministic per-task Secret name derived from the Job name.

    Both the render path (emits ``secretKeyRef`` entries pointing here) and
    the spawn path (creates + later deletes this Secret) compute the name
    the same way, so they always agree.
    """
    return f"{job_name}-secrets"


def env_var_entries(env_vars: Any) -> list[dict[str, str]]:
    """Plain container ``env`` entries for the spec's non-secret env vars."""
    out: list[dict[str, str]] = []
    for key, value in (env_vars or {}).items():
        name = str(key).strip()
        if name:
            out.append({"name": name, "value": str(value)})
    return out


def secret_env_entries(secret_name: str, refs: list[dict[str, str]]) -> list[dict]:
    """``secretKeyRef`` env entries binding each ref's env var to a key in
    the per-task Secret (mirrors ``secret_plumbing.make_env_from_refs``).

    The Secret stores each value under a key equal to the env var, so the
    ref env var is both the container env name and the Secret key.
    """
    return [
        {
            "name": ref["env_var"],
            "valueFrom": {
                "secretKeyRef": {
                    "name": secret_name,
                    "key": ref["env_var"],
                }
            },
        }
        for ref in refs
    ]


def agent_container_env(spec, secret_name: str) -> list[dict]:
    """Full container ``env`` for an agent task from its spec: plain env
    vars first, then the ``secretKeyRef`` entries (so a secret ref wins if
    it collides with a plain env var of the same name)."""
    if spec is None:
        return []
    refs = normalize_secret_refs(getattr(spec, "secret_refs", None))
    return env_var_entries(getattr(spec, "env_vars", None)) + secret_env_entries(secret_name, refs)


def build_task_secret_manifest(
    *,
    secret_name: str,
    namespace: str,
    task_guid: str,
    resolved: dict[str, str],
) -> dict:
    """K8s Secret manifest holding the resolved values for one task.

    ``stringData`` keys are the env vars; K8s base64-encodes them. Labeled
    per-task + managed-by so ``K8sJobSpawner.stop`` can delete it with the
    Job and an operator can find it. Plaintext values live only in this
    manifest (applied straight to the store's cluster), never in the pod
    spec.
    """
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": namespace,
            "labels": {
                "astrolift.dev/task-id": str(task_guid),
                "astrolift.dev/workload-kind": "agent",
                "astrolift.dev/managed-by": _MANAGED_BY,
            },
        },
        "type": "Opaque",
        "stringData": dict(resolved),
    }


def resolve_task_secret_manifest(
    *,
    cluster,
    spec,
    secret_name: str,
    namespace: str,
    task_guid: str,
) -> dict | None:
    """Preflight + materialize a spec's secret refs into a K8s Secret manifest.

    Returns the Secret manifest, or ``None`` when the spec declares no
    refs. Preflights *all* refs first, collecting every missing/empty one,
    and raises :class:`AgentSecretResolutionError` listing them so the
    spawn fails with one readable message instead of the pod later
    crash-looping on an unresolvable ``secretKeyRef``.
    """
    refs = normalize_secret_refs(getattr(spec, "secret_refs", None))
    if not refs:
        return None

    spec_slug = str(getattr(spec, "slug", "") or "?")
    try:
        backend = resolve_secrets_backend(cluster)
    except Exception as exc:  # noqa: BLE001 — no secrets driver ⇒ every ref unresolvable
        raise AgentSecretResolutionError(
            spec_slug,
            [f"{r['env_var']} ({r['uri']})" for r in refs],
            reason=str(exc),
        ) from exc

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for ref in refs:
        uri, env_var = ref["uri"], ref["env_var"]
        try:
            value = read_secret_value(backend, uri)
        except Exception as exc:  # noqa: BLE001 — read failure ⇒ treat as unresolvable
            log.warning("agent_secrets: read failed for %s (%s): %s", env_var, uri, exc)
            value = None
        if not value:
            missing.append(f"{env_var} ({uri})")
        else:
            resolved[env_var] = value

    if missing:
        raise AgentSecretResolutionError(spec_slug, missing)

    return build_task_secret_manifest(
        secret_name=secret_name,
        namespace=namespace,
        task_guid=task_guid,
        resolved=resolved,
    )


def probe_ref_statuses(*, cluster, refs: Any) -> list[dict[str, Any]]:
    """Per-ref presence probe for the status query — metadata only.

    Returns ``[{"env_var", "uri", "exists", "error"}]``. Never raises:
    a driver/backend failure lands as ``exists=False`` + an ``error``
    string (mirrors ``bundle_keys`` swallow-and-report) so the status
    surface degrades gracefully instead of 500-ing. Values are never read
    into the return.
    """
    normalized = normalize_secret_refs(refs)
    backend = None
    backend_error: str | None = None
    try:
        backend = resolve_secrets_backend(cluster) if cluster is not None else None
        if backend is None and cluster is None:
            backend_error = "org has no managed cluster to resolve a secret store"
    except Exception as exc:  # noqa: BLE001 — resolution failure must not 500 the status page
        backend_error = str(exc)

    out: list[dict[str, Any]] = []
    for ref in normalized:
        env_var, uri = ref["env_var"], ref["uri"]
        if backend is None:
            out.append({"env_var": env_var, "uri": uri, "exists": False, "error": backend_error})
            continue
        try:
            value = read_secret_value(backend, uri)
            out.append({"env_var": env_var, "uri": uri, "exists": bool(value), "error": None})
        except Exception as exc:  # noqa: BLE001 — swallow into exists=false + error
            out.append({"env_var": env_var, "uri": uri, "exists": False, "error": str(exc)})
    return out
