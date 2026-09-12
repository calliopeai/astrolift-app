"""Agent task secret resolution + K8s materialization (#1173).

An :class:`~astrolift_agents.models.AgentEnvironmentSpec` carries
``secret_refs`` — ``[{"uri": ..., "env_var": ...}]`` — that *name* secrets
in the install's secret store. The control plane never persists the
values; the dispatcher resolves them from the per-cluster
``SecretsBackend`` driver at spawn time and materializes them into a
per-task K8s Secret the pod mounts via ``secretKeyRef`` (mirrors the
pipeline secret plumbing in ``astrolift_pipelines.secret_plumbing``).

Write/read symmetry (the property the whole feature turns on): direct agent
values are stored under a single conventional key, :data:`SECRET_VALUE_KEY`,
at the ref's ``uri`` path. Managed-resource bundle refs may select a different
key with ``uri#field``. The portable selector is stripped before the backend
read so the same ref works across cloud stores. Both paths resolve the store
from the *same* cluster (see
:func:`astrolift_agents.services.agent_cluster.resolve_agent_cluster`), so
a value set through the platform lands in the exact store the pod reads at
launch. A plain-string secret pre-created out-of-band also resolves,
because the AWS driver wraps a bare ``SecretString`` under ``"value"``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from _sdk.secrets import resolve_secret_reference

from astrolift_agents.services.agent_package import is_reserved_agent_environment_name

log = logging.getLogger("astrolift_dispatch.agent_secrets")

# The single conventional key each agent secret ref's value is stored
# under. Write and read paths MUST use the same key or resolution breaks.
SECRET_VALUE_KEY = "value"

# Labels stamped on the per-task Secret so it is discoverable + cleaned up
# alongside the task's Job (K8sJobSpawner.stop deletes by name).
_MANAGED_BY = "astrolift-agents"
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def valid_env_var(value: str) -> bool:
    """Whether ``value`` is safe as a process/Kubernetes env-var name."""
    return bool(_ENV_VAR_RE.fullmatch(str(value or "")))


def valid_agent_env_var(value: str) -> bool:
    """Whether a user-controlled variable is syntactically safe and not dispatcher-owned."""
    return valid_env_var(value) and not is_reserved_agent_environment_name(value)


def effective_secret_refs(spec) -> list[dict[str, str]]:
    """Merge manifest refs with persistent operator overrides/tombstones."""
    # Project-resource bindings are defaults. Manifest refs and persistent
    # operator overrides win on collisions so attaching a shared database can
    # never silently replace an explicitly configured credential.
    refs = {row["env_var"]: row for row in project_managed_service_secret_refs(spec)}
    refs.update({row["env_var"]: row for row in normalize_secret_refs(getattr(spec, "secret_refs", None))})
    if spec is None or not getattr(spec, "pk", None):
        return list(refs.values())
    try:
        overrides = spec.secret_binding_overrides.filter(deleted_at__isnull=True).order_by("created_at", "pk")
    except (AttributeError, TypeError):
        return list(refs.values())
    for override in overrides:
        if override.removed:
            refs.pop(override.env_var, None)
        elif override.uri:
            refs[override.env_var] = {"env_var": override.env_var, "uri": override.uri}
    return list(refs.values())


def _project_managed_service_bindings(spec):
    if spec is None or not getattr(spec, "pk", None):
        return []
    try:
        attachments = (
            spec.project_managed_service_attachments.select_related("managed_service")
            .prefetch_related("managed_service__bindings")
            .filter(
                deleted_at__isnull=True,
                managed_service__deleted_at__isnull=True,
                managed_service__status="active",
            )
            .order_by("managed_service__kind", "managed_service__name", "pk")
        )
    except (AttributeError, TypeError):
        return []
    rows = []
    for attachment in attachments:
        for binding in attachment.managed_service.bindings.filter(
            deleted_at__isnull=True,
        ).order_by("env_key"):
            rows.append(binding)
    return rows


def project_managed_service_secret_refs(spec) -> list[dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}
    for binding in _project_managed_service_bindings(spec):
        if binding.is_secret and valid_agent_env_var(binding.env_key):
            refs[binding.env_key] = {
                "env_var": binding.env_key,
                "uri": binding.env_value_ref,
            }
    return list(refs.values())


def project_managed_service_env_vars(spec) -> dict[str, str]:
    values: dict[str, str] = {}
    for binding in _project_managed_service_bindings(spec):
        if not binding.is_secret and valid_agent_env_var(binding.env_key):
            values[binding.env_key] = binding.env_value_ref
    return values


def app_managed_service_bindings(workload) -> list[Any]:
    """The app's managed-service bindings an agent workload inherits (#1700).

    An agent declared in the same repo as an app could not reach that app's
    database or bucket: agent Jobs are spawned into the per-org agents
    namespace with a per-task Secret, and a Kubernetes Secret is
    namespace-scoped, so the ``astrolift-bindings-<slug>`` Secret the app's
    own workloads mount cannot be referenced from there at all. The values
    ride the per-task Secret instead -- the same mechanism the
    project-scoped attachments above already use.

    **Only when the app has exactly one live environment.** Choosing
    between ``production`` and ``staging`` on an agent's behalf is picking
    which database it gets, and there is nothing in an agent workload that
    says which. An ambiguous app inherits nothing and says so.
    """
    app = getattr(workload, "registered_app", None)
    if app is None or not getattr(app, "pk", None):
        return []
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models import ManagedService

    environments = list(
        AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).values_list("pk", "name")[
            :2
        ]
    )
    if len(environments) != 1:
        if environments:
            log.warning(
                "agent_secrets: app %s has %d environments; agent workload %s inherits no "
                "bindings because nothing says which environment it belongs to",
                getattr(app, "slug", "?"),
                len(environments),
                getattr(workload, "slug", "?"),
            )
        return []
    env_pk = environments[0][0]
    rows: list[Any] = []
    services = ManagedService.objects.filter(
        registered_app=app,
        app_environment_id=env_pk,
        deleted_at__isnull=True,
        status="active",
    ).order_by("kind", "name", "pk")
    for service in services:
        rows.extend(service.bindings.filter(deleted_at__isnull=True).order_by("env_key"))
    return rows


def app_managed_service_secret_refs(workload) -> list[dict[str, str]]:
    """The secret half of :func:`app_managed_service_bindings`."""
    refs: dict[str, dict[str, str]] = {}
    for binding in app_managed_service_bindings(workload):
        if binding.is_secret and valid_agent_env_var(binding.env_key):
            refs[binding.env_key] = {
                "env_var": binding.env_key,
                "uri": binding.env_value_ref,
            }
    return list(refs.values())


def app_managed_service_env_vars(workload) -> dict[str, str]:
    """The plain-value half of :func:`app_managed_service_bindings`."""
    values: dict[str, str] = {}
    for binding in app_managed_service_bindings(workload):
        if not binding.is_secret and valid_agent_env_var(binding.env_key):
            values[binding.env_key] = binding.env_value_ref
    return values


def agent_bundle_refs(spec, *, environment: str = "default"):
    """Active bundle attachments for one environment, in precedence order.

    Agent tasks currently launch the ``default`` environment.  Filtering here
    is intentionally fail-closed: an attachment prepared for a future named
    environment must not leak into today's default task pods.
    """
    if spec is None or not getattr(spec, "pk", None):
        return []
    try:
        return list(
            spec.secret_bundle_refs.select_related("secret_bundle")
            .filter(
                environment=environment,
                deleted_at__isnull=True,
                secret_bundle__deleted_at__isnull=True,
            )
            .order_by("position", "created_at", "pk")
        )
    except (AttributeError, TypeError):
        return []


def secret_backend_capabilities(backend) -> dict[str, Any]:
    """Describe the backing provider's disclosure capability explicitly."""
    if backend is None:
        return {
            "provider": "unavailable",
            "can_reveal": False,
            "read_limitation": "No secrets backend is available for this agent.",
        }
    cls = type(backend)
    identity = f"{cls.__module__}.{cls.__name__}".lower()
    provider = str(getattr(backend, "provider_id", "") or "").strip()
    explicit_reveal = getattr(backend, "supports_value_reveal", None)
    explicit_limitation = getattr(backend, "value_reveal_limitation", None)
    if explicit_reveal is not None:
        can_reveal = bool(explicit_reveal)
        return {
            "provider": provider or "external-secret-store",
            "can_reveal": can_reveal,
            "read_limitation": (
                None
                if can_reveal
                else explicit_limitation or f"{provider or 'provider'} does not expose value reads."
            ),
        }
    if "github" in identity:
        return {
            "provider": "github-actions",
            "can_reveal": False,
            "read_limitation": "GitHub Actions secret values are write-only and cannot be read back.",
        }
    provider = provider or "external-secret-store"
    for needle, label in (
        ("aws", "aws-secrets-manager"),
        ("gcp", "gcp-secret-manager"),
        ("keyvault", "azure-key-vault"),
        ("vault", "hashicorp-vault"),
    ):
        if needle in identity:
            provider = label
            break
    can_reveal = callable(getattr(backend, "get", None))
    return {
        "provider": provider,
        "can_reveal": can_reveal,
        "read_limitation": None if can_reveal else f"{provider} does not expose value reads.",
    }


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
    value = resolve_secret_reference(
        backend,
        uri,
        default_key=SECRET_VALUE_KEY,
    )
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
        if valid_agent_env_var(name):
            out.append({"name": name, "value": str(value)})
        elif name:
            log.warning("agent_secrets: ignoring invalid or dispatcher-owned plain env var %r", name)
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


def agent_container_env(spec, secret_name: str, *, workload=None) -> list[dict]:
    """Full container ``env`` for an agent task from its spec: plain env
    vars first, then the ``secretKeyRef`` entries (so a secret ref wins if
    it collides with a plain env var of the same name).

    ``workload`` is the agent's own Workload, when it has one. An agent
    that belongs to an app inherits that app's managed-service bindings
    (#1700); they sit lowest in precedence, so the environment spec and its
    own refs still override on a collision.
    """
    app_env_vars = env_var_entries(app_managed_service_env_vars(workload)) if workload else []
    app_refs = app_managed_service_secret_refs(workload) if workload else []
    if spec is None:
        return app_env_vars + secret_env_entries(secret_name, app_refs)
    refs = effective_secret_refs(spec)
    bundle_refs: list[dict[str, str]] = []
    for attachment in agent_bundle_refs(spec):
        prefix = attachment.prefix or ""
        for key in attachment.secret_bundle.last_known_keys or []:
            env_var = f"{prefix}{key}"
            if valid_agent_env_var(env_var):
                bundle_refs.append({"env_var": env_var, "uri": attachment.secret_bundle.backend_ref})
    entries = (
        app_env_vars
        + env_var_entries(project_managed_service_env_vars(spec))
        + env_var_entries(getattr(spec, "env_vars", None))
        + secret_env_entries(secret_name, app_refs)
        + secret_env_entries(secret_name, bundle_refs)
        + secret_env_entries(secret_name, refs)
    )
    # Kubernetes accepts duplicate env names, but relying on container-runtime
    # ordering for precedence is both opaque and implementation-sensitive.
    # Collapse the list here: later sources win (manifest over project
    # defaults; secrets over plain values) while each name is emitted once.
    by_name: dict[str, dict] = {}
    for entry in entries:
        by_name[entry["name"]] = entry
    return list(by_name.values())


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
    workload=None,
) -> dict | None:
    """Preflight + materialize a spec's secret refs into a K8s Secret manifest.

    Returns the Secret manifest, or ``None`` when there is nothing to
    materialize. Preflights *all* refs first, collecting every
    missing/empty one, and raises :class:`AgentSecretResolutionError`
    listing them so the spawn fails with one readable message instead of
    the pod later crash-looping on an unresolvable ``secretKeyRef``.

    ``workload`` is the agent's own Workload, when it has one: an agent
    that belongs to an app carries that app's managed-service secrets into
    the same per-task Secret (#1700), because the app's own binding Secret
    lives in another namespace and cannot be mounted from here.
    """
    refs = effective_secret_refs(spec)
    bundle_refs = agent_bundle_refs(spec)
    app_refs = app_managed_service_secret_refs(workload) if workload is not None else []
    if not refs and not bundle_refs and not app_refs:
        return None

    spec_slug = str(getattr(spec, "slug", "") or "?")
    try:
        backend = resolve_secrets_backend(cluster)
    except Exception as exc:  # noqa: BLE001 — no secrets driver ⇒ every ref unresolvable
        raise AgentSecretResolutionError(
            spec_slug,
            [f"{r['env_var']} ({r['uri']})" for r in app_refs + refs]
            + [f"bundle:{r.secret_bundle.slug}" for r in bundle_refs],
            reason=str(exc),
        ) from exc

    resolved: dict[str, str] = {}
    missing: list[str] = []
    # Bundles merge in attachment order. A later bundle wins, then direct
    # per-agent refs override every bundle on collision.
    for attachment in bundle_refs:
        bundle = attachment.secret_bundle
        try:
            payload = backend.get(bundle.backend_ref)
        except Exception as exc:  # noqa: BLE001
            log.warning("agent_secrets: bundle read failed for %s: %s", bundle.slug, exc)
            payload = None
        if payload is None:
            missing.append(f"bundle:{bundle.slug} ({bundle.backend_ref})")
            continue
        keys: list[str] = []
        for key, value in payload.items():
            env_var = f"{attachment.prefix or ''}{key}"
            if not valid_agent_env_var(env_var):
                missing.append(f"bundle:{bundle.slug} invalid or dispatcher-owned env var {env_var!r}")
                continue
            value = str(value)
            if not value:
                missing.append(f"bundle:{bundle.slug} empty value for {key}")
                continue
            keys.append(str(key))
            resolved[env_var] = value
        normalized_keys = sorted(set(keys))
        if normalized_keys != list(bundle.last_known_keys or []):
            bundle.last_known_keys = normalized_keys
            from django.utils import timezone

            bundle.last_key_enum_at = timezone.now()
            bundle.save(update_fields=["last_known_keys", "last_key_enum_at", "updated_at", "version"])
    # App bindings first, so a spec's own ref still wins on a collision.
    for ref in app_refs + refs:
        uri, env_var = ref["uri"], ref["env_var"]
        if not valid_agent_env_var(env_var):
            missing.append(f"{env_var} ({uri}; invalid or dispatcher-owned env var name)")
            continue
        try:
            value = read_secret_value(backend, uri)
        except Exception as exc:  # noqa: BLE001 — read failure ⇒ treat as unresolvable
            log.warning(
                "agent_secrets: read failed for %s (%s): %s",
                env_var,
                uri,
                exc.__class__.__name__,
            )
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
    except Exception:  # noqa: BLE001 — never reflect provider response bodies
        backend_error = "secret store unavailable; inspect the provider audit log"

    capabilities = secret_backend_capabilities(backend)

    out: list[dict[str, Any]] = []
    for ref in normalized:
        env_var, uri = ref["env_var"], ref["uri"]
        if backend is None:
            out.append(
                {
                    "env_var": env_var,
                    "uri": uri,
                    "exists": False,
                    "error": backend_error,
                    **capabilities,
                }
            )
            continue
        try:
            value = read_secret_value(backend, uri)
            out.append(
                {
                    "env_var": env_var,
                    "uri": uri,
                    "exists": bool(value),
                    "error": None,
                    **capabilities,
                }
            )
        except Exception:  # noqa: BLE001 — never reflect provider response bodies
            out.append(
                {
                    "env_var": env_var,
                    "uri": uri,
                    "exists": False,
                    "error": "secret presence check failed; inspect the provider audit log",
                    **capabilities,
                }
            )
    return out
