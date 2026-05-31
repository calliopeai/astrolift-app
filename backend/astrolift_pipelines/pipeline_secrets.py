"""Pipeline secret management — CRUD for pipeline-scoped secrets (#100).

Pipeline secrets are stored in the org's tenant secret store (same backend
as app secrets — AWS SM, GCP SM, Azure KV, Vault, etc.) under a
pipeline-namespaced key:

    Pipeline secrets namespace: astrolift/pipelines/{pipeline_guid}/secrets/{name}

This keeps them distinct from app secrets and allows per-pipeline access control.

The GraphQL mutations here are separate from the artifact store's resolve path —
they operate on the org's secret backend as a key-value store.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_pipelines.models import Pipeline

logger = logging.getLogger(__name__)


def _secret_key(pipeline: "Pipeline", name: str) -> str:
    """Return the secret store key for a pipeline secret."""
    return f"astrolift/pipelines/{pipeline.guid}/secrets/{name}"


def set_pipeline_secret(pipeline: "Pipeline", name: str, value: str) -> None:
    """Write a secret value to the org's secret store for a pipeline.

    Raises ValueError if the name is invalid (empty, contains slashes, etc.)
    """
    name = name.strip()
    if not name:
        raise ValueError("Secret name must not be empty")
    if "/" in name:
        raise ValueError(f"Secret name must not contain slashes: {name!r}")

    key = _secret_key(pipeline, name)

    try:
        from astrolift_lifecycle.services.secrets import write_org_secret
        write_org_secret(pipeline.organization, key, value)
        logger.info("pipeline.secrets: set secret %s for pipeline %s", name, pipeline.name)
    except Exception as exc:
        raise RuntimeError(f"Failed to write pipeline secret {name!r}: {exc}") from exc


def delete_pipeline_secret(pipeline: "Pipeline", name: str) -> None:
    """Remove a secret from the org's secret store for a pipeline."""
    key = _secret_key(pipeline, name)
    try:
        from astrolift_lifecycle.services.secrets import delete_org_secret
        delete_org_secret(pipeline.organization, key)
        logger.info("pipeline.secrets: deleted secret %s for pipeline %s", name, pipeline.name)
    except Exception as exc:
        raise RuntimeError(f"Failed to delete pipeline secret {name!r}: {exc}") from exc


def get_pipeline_secret_names(pipeline: "Pipeline") -> list[str]:
    """Return the list of secret names set for a pipeline.

    Does NOT return values — only names. Use set_pipeline_secret to update.
    """
    prefix = f"astrolift/pipelines/{pipeline.guid}/secrets/"
    try:
        from astrolift_lifecycle.services.secrets import list_org_secrets
        keys = list_org_secrets(pipeline.organization, prefix=prefix)
        return [k.removeprefix(prefix) for k in keys]
    except Exception:  # noqa: BLE001
        return []


def check_pipeline_secret_exists(pipeline: "Pipeline", name: str) -> bool:
    """Return True if a secret is set for this pipeline."""
    key = _secret_key(pipeline, name)
    try:
        from astrolift_lifecycle.services.secrets import read_org_secret
        return read_org_secret(pipeline.organization, key) is not None
    except Exception:  # noqa: BLE001
        return False
