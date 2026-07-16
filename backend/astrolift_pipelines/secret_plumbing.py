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
    """Apply a K8s Secret manifest via the cluster's dynamic client."""
    from core.cluster_observability import get_dynamic_client

    client = get_dynamic_client(cluster)
    api = client.resources.get(api_version="v1", kind="Secret")
    try:
        api.create(body=manifest, namespace=namespace)
    except Exception:  # noqa: BLE001 — may already exist, try patch
        api.patch(
            name=manifest["metadata"]["name"],
            body=manifest,
            namespace=namespace,
            content_type="application/merge-patch+json",
        )


def _delete_k8s_secret(cluster, name: str, namespace: str) -> None:
    """Delete a K8s Secret by name."""
    from core.cluster_observability import get_dynamic_client

    client = get_dynamic_client(cluster)
    api = client.resources.get(api_version="v1", kind="Secret")
    api.delete(name=name, namespace=namespace)


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
