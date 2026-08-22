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

from django.conf import settings

_PLATFORM_DEFAULT_IMAGE = "ghcr.io/calliopeai/astrolift-builder:latest"


def resolve_job_image(job, pipeline_run) -> str:
    """The container image a pipeline job runs on.

    Precedence: what the job declared, then the install's
    `PIPELINE_DEFAULT_IMAGE`, then the platform default (#65).

    `pipeline_run` is unused now that the org-level levels are gone. It is
    kept because the signature is the seam an org-level default would come
    back through, and changing it would churn the call site for nothing.
    """
    # 1. What the job asked for.
    if job.container_image:
        return str(job.container_image).strip()

    # 2. The install's override.
    #
    # Two levels used to sit between these, both reading
    # `Organization.extra_data`. That field does not exist on the model, so
    # `getattr` returned None, the dict was always empty, and neither level
    # could ever produce a value. They are removed rather than left as
    # branches that read like configuration an operator could use.
    settings_default = str(getattr(settings, "PIPELINE_DEFAULT_IMAGE", "") or "").strip()
    if settings_default:
        return settings_default

    # 3. The platform default, which is what the runner path already uses
    #    (`runner_views` line 186). Before this, the K8s path fell back to
    #    bare `ubuntu:22.04` instead — no git, no shell tooling, no astro
    #    CLI — so the same job got a working image on a self-hosted runner
    #    and an unusable one in-cluster.
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
