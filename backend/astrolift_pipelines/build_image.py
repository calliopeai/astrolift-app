"""Default build image configuration for pipeline jobs (#103).

The default image is: ghcr.io/calliopeai/astrolift-builder:latest
Contents: Ubuntu LTS + git + bash + curl + CA certs + astro CLI.

Resolution precedence (highest wins):
1. Job-level container (in TOML [[jobs.container]])
2. Pipeline-level default (in TOML [defaults] container)
3. Org install config (pipeline_default_image in org.extra_data)
4. Platform default (PIPELINE_DEFAULT_IMAGE in settings)
5. Hardcoded fallback: ghcr.io/calliopeai/astrolift-builder:latest
"""

from __future__ import annotations

_PLATFORM_DEFAULT_IMAGE = "ghcr.io/calliopeai/astrolift-builder:latest"


def resolve_job_image(job, pipeline_run) -> str:
    """Return the container image to use for a pipeline job.

    Applies the resolution precedence defined in #65 (DSL spec):
    job.container_image > pipeline defaults > org config > platform default.
    """
    # 1. Job-level container
    if job.container_image:
        return job.container_image

    # 2. Pipeline TOML defaults — not stored separately at v1; check org extra_data
    org = pipeline_run.pipeline.organization
    extra = getattr(org, "extra_data", None) or {}

    # 3. Org install config
    org_default = extra.get("pipeline_default_image")
    if org_default:
        return org_default

    # 4. Django settings override
    try:
        from django.conf import settings

        settings_default = getattr(settings, "PIPELINE_DEFAULT_IMAGE", None)
        if settings_default:
            return settings_default
    except Exception:  # noqa: BLE001
        pass

    # 5. Hardcoded platform default
    return _PLATFORM_DEFAULT_IMAGE


def resolve_pull_secret_name(pipeline_run) -> str | None:
    """Return the K8s imagePullSecret name for the pipeline's ServiceAccount.

    The pull secret is created by setPipelineRegistryCredential mutation (#103)
    and attached to the per-pipeline ServiceAccount created by isolation.py.
    Returns None when no registry credentials are configured.
    """

    org = pipeline_run.pipeline.organization
    extra = getattr(org, "extra_data", None) or {}

    if not extra.get("pipeline_registry_credentials"):
        return None

    # Pull secret naming convention: mirrors the ServiceAccount name
    sa_prefix = str(pipeline_run.pipeline.guid).replace("-", "")[:8]
    return f"pipeline-pull-{sa_prefix}"


def get_platform_default_image() -> str:
    """Return the platform default build image."""
    try:
        from django.conf import settings

        return getattr(settings, "PIPELINE_DEFAULT_IMAGE", _PLATFORM_DEFAULT_IMAGE)
    except Exception:  # noqa: BLE001
        return _PLATFORM_DEFAULT_IMAGE
