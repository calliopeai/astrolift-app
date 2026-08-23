"""Pipeline secret plumbing — resolution, K8s materialization, and redaction (#84).

Secrets declared in a pipeline's ``[secrets]`` TOML block are resolved
from the org's tenant secret store at job dispatch time and materialized
as K8s Secrets for the duration of the job pod.

Design constraints:
- Secret values never appear in Temporal workflow input/history
- Secret values never appear in the evaluated job config that's logged
- Secret values are mounted into the pod via K8s Secret, not env-var interpolation
- The K8s Secret is deleted after Job completion (see cleanup hooks)
- Secret redaction at log ingest: any line containing a known secret value
  is replaced with *** before the log reaches durable storage

Secret scope at v1: all secrets declared in [secrets] are available to all
steps in the pipeline. Per-job scoping is explicitly a v2 concern.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_pipelines.models import JobRun, PipelineRun

logger = logging.getLogger(__name__)

# Sentinel used in evaluated configs/logs in place of the actual value.
SECRET_REDACTION_MARKER = "***"


class SecretResolutionError(Exception):
    """Raised when one or more pipeline secrets cannot be resolved."""


def resolve_pipeline_secrets(pipeline_run: PipelineRun, secret_names: list[str]) -> dict[str, str]:
    """Resolve a list of secret names from the org's tenant secret store.

    Returns a mapping of {secret_name: plaintext_value}. Raises
    ``SecretResolutionError`` when any declared secret is missing.

    The return value must be treated as a secret bundle:
    - Never log, never serialize to Temporal, never store in DB
    - Pass directly to materialize_job_secrets() for K8s mounting
    """
    org = pipeline_run.pipeline.organization
    resolved: dict[str, str] = {}
    missing: list[str] = []

    for name in secret_names:
        value = _read_org_secret(org, name)
        if value is None:
            missing.append(name)
        else:
            resolved[name] = value

    if missing:
        raise SecretResolutionError(
            f"Pipeline '{pipeline_run.pipeline.name}' declared secrets not found in org store: "
            + ", ".join(missing)
        )

    return resolved


def _read_org_secret(org, name: str) -> str | None:
    """Read a single secret value from the org's secret backend.

    Returns None if the secret doesn't exist in the store.
    """
    try:
        from astrolift_lifecycle.services.secrets import read_org_secret

        return read_org_secret(org, name)
    except Exception:  # noqa: BLE001
        return None


def materialize_job_secrets(
    job_run: JobRun,
    secret_bundle: dict[str, str],
    *,
    namespace: str,
    cluster,
) -> str:
    """Write a per-job K8s Secret containing the resolved secret values.

    The secret is named ``pipeline-job-<job_run_guid>-secrets`` and is
    created in the job's namespace. Returns the K8s Secret name.

    The caller is responsible for deleting the secret after job completion
    (see cleanup_job_secrets()).
    """
    secret_name = f"pipeline-job-{job_run.guid}-secrets"

    # Each secret value is base64-encoded by the K8s API automatically
    # when we set it in the `stringData` field.
    k8s_secret_manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": namespace,
            "labels": {
                "astrolift.dev/pipeline-job-run": str(job_run.guid),
                "astrolift.dev/managed-by": "astrolift-pipelines",
            },
        },
        "type": "Opaque",
        "stringData": secret_bundle,
    }

    try:
        _apply_k8s_secret(cluster, k8s_secret_manifest, namespace)
        logger.info("pipelines.secrets: materialized K8s secret %s in %s", secret_name, namespace)
    except Exception as exc:
        raise SecretResolutionError(
            f"Failed to materialize secrets for job run {job_run.guid}: {exc}"
        ) from exc

    return secret_name


def cleanup_job_secrets(
    job_run: JobRun,
    *,
    namespace: str,
    cluster,
) -> None:
    """Delete the K8s Secret created for a job run.

    Called after Job completion (success, failure, or cancellation).
    Failures here are logged but not re-raised — secret cleanup must
    not block the job state transition.
    """
    secret_name = f"pipeline-job-{job_run.guid}-secrets"
    try:
        _delete_k8s_secret(cluster, secret_name, namespace)
        logger.info("pipelines.secrets: deleted K8s secret %s from %s", secret_name, namespace)
    except Exception:  # noqa: BLE001
        logger.warning(
            "pipelines.secrets: could not delete K8s secret %s — manual cleanup may be needed",
            secret_name,
        )


def _apply_k8s_secret(cluster, manifest: dict, namespace: str) -> None:
    """Apply a K8s Secret manifest through the cluster driver.

    Was three separate breaks (#1614). ``core.cluster_observability`` has no
    ``get_dynamic_client``, so this raised ImportError before doing anything;
    behind that, the ``client.resources.get(...)`` / ``.create`` / ``.patch``
    shape is the raw ``kubernetes.dynamic`` API, and the wrapper this repo
    actually builds (``providers/_sdk/k8s_dynamic_client.py``) has no
    ``.resources`` attribute at all. So even a working accessor would not have
    made these lines run.

    ``apply_manifests`` is the shape every other caller uses, and it is
    create-or-update already, which is what the create-then-patch dance was
    reaching for. Copied from ``secret_rotation._refresh_in_cluster_sync``,
    which does exactly this against a real cluster.
    """
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    result = driver.apply_manifests(ctx.slug, namespace, [manifest])
    if not result.ok:
        raise SecretResolutionError(
            f"could not apply pipeline secret {manifest['metadata']['name']!r} "
            f"to {namespace!r} on cluster {cluster.slug!r}: " + "; ".join(str(e) for e in result.errors),
        )


def _delete_k8s_secret(cluster, name: str, namespace: str) -> None:
    """Delete a K8s Secret by name, through the cluster driver.

    ``delete_manifests`` treats not-found as success per the SDK contract, so
    only real failures land in ``errors``. That matters here: cleanup runs on
    a path that may have partially failed, and a missing secret is the
    outcome cleanup wants.
    """
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    stub = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace},
    }
    result = driver.delete_manifests(ctx.slug, namespace, [stub])
    if result.errors:
        raise SecretResolutionError(
            f"could not delete pipeline secret {name!r} from {namespace!r} "
            f"on cluster {cluster.slug!r}: " + "; ".join(str(e) for e in result.errors),
        )


def make_env_from_refs(secret_name: str, secret_keys: list[str]) -> list[dict]:
    """Build K8s envFrom + env entries that mount the secret into the container.

    Returns a list of env var specs (suitable for pod spec containers[].env)
    that reference each key from the K8s Secret.
    """
    return [
        {
            "name": key,
            "valueFrom": {
                "secretKeyRef": {
                    "name": secret_name,
                    "key": key,
                }
            },
        }
        for key in secret_keys
    ]


# ---------------------------------------------------------------------------
# Log redaction
# ---------------------------------------------------------------------------


class SecretRedactor:
    """Redacts known secret values from log lines at ingest time.

    Thread-safe; values are compared in length-descending order so that
    longer secrets (which may contain shorter ones as substrings) are
    redacted first.
    """

    def __init__(self, known_values: list[str]) -> None:
        # Sort by length descending — longer secrets first to avoid partial redaction
        self._values = sorted(known_values, key=len, reverse=True)

    def redact(self, line: str) -> str:
        """Return the line with all known secret values replaced by ***."""
        for value in self._values:
            if value and value in line:
                line = line.replace(value, SECRET_REDACTION_MARKER)
        return line

    def redact_lines(self, text: str) -> str:
        """Redact all lines in a multi-line log block."""
        return "\n".join(self.redact(line) for line in text.splitlines())


def build_redactor_for_job(secret_bundle: dict[str, str]) -> SecretRedactor:
    """Build a SecretRedactor from a secret bundle.

    The redactor is constructed at job dispatch time (when secret values
    are known) and passed to the log streaming layer.
    """
    return SecretRedactor(list(secret_bundle.values()))


# ---------------------------------------------------------------------------
# Which secrets a job needs (#1529)
# ---------------------------------------------------------------------------

# The reference syntax was already decided and already shipping: the GHA
# converter rewrites `${{ secrets.X }}` to `${secrets.X}` and passes it
# through into step `run` and `env` values. What was missing was anything
# that read those references back out, so `resolve_pipeline_secrets` took a
# name list no caller could construct.
#
# The names are derived from the references rather than declared separately.
# Requiring a declaration would mean every pipeline converted from GitHub
# Actions arrives referencing secrets it does not declare — broken on
# arrival and needing a hand edit — because the converter emits references
# and no declaration. Deriving them makes a converted pipeline run as
# converted.
#
# An explicit block is a superset and can be added later without changing
# this: the union of declared and referenced names is still a name list.
_SECRET_REF = re.compile(r"\$\{secrets\.([A-Za-z_][A-Za-z0-9_]*)\}")


def secret_names_in(*texts: str | None) -> list[str]:
    """Every distinct ``${secrets.NAME}`` in the given strings, sorted."""
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(_SECRET_REF.findall(text))
    return sorted(found)


def secret_names_for_job(job, steps) -> list[str]:
    """Every secret name this job's definition refers to.

    Covers the step's script, and both of its string-valued maps: a
    registry credential is as likely to arrive through `with` as through
    `run`, and missing one would fail the job with an unresolved
    reference rather than anything that names the cause.
    """
    texts: list[str | None] = []
    for step in steps:
        texts.append(step.run)
        texts.append(step.uses)
        texts.extend(str(v) for v in (step.env or {}).values())
        texts.extend(str(v) for v in (step.with_params or {}).values())
    return secret_names_in(*texts)
