"""VNC snapshot URL provisioning + injection into spawned agent pods.

The ``-vnc`` agent images run a pod-side uploader that PUTs a JPEG of the
live framebuffer every ``ASTROLIFT_SNAPSHOT_INTERVAL`` seconds to
``ASTROLIFT_SNAPSHOT_URL`` (a no-op when that var is unset). At spawn time,
for a VNC-enabled task, the dispatcher:

1. Mints a presigned PUT URL for the task's snapshot blob key, reusing the
   same blob store the pipeline payload/artifact system uses
   (``astrolift_agents.snapshot_store``).
2. Freezes the snapshot blob key onto the AgentTask so a later gallery
   surface can mint a presigned GET for the latest frame.
3. Injects ``ASTROLIFT_SNAPSHOT_URL`` + ``ASTROLIFT_SNAPSHOT_INTERVAL`` into
   the agent container's env.

Non-VNC tasks are a no-op — no key is frozen and no env is injected. If the
install has no blob store configured the injection is skipped (logged) so a
VNC run still starts; the uploader simply no-ops with an unset URL.

Mirrors ``astrolift_dispatch.brief_injector``: a thin env-injection seam the
K8s spawner calls after rendering the base Job manifest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from providers._sdk.blob_store import BlobStoreNotConfiguredError

if TYPE_CHECKING:
    from astrolift_agents.models import AgentTask

logger = logging.getLogger(__name__)

# Default seconds between pod-side framebuffer snapshots. The -vnc uploader
# reads this from ASTROLIFT_SNAPSHOT_INTERVAL; 5s balances freshness vs blob
# write volume.
DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 5


def snapshot_env_vars(task: AgentTask) -> list[dict[str, str]]:
    """Mint the snapshot PUT URL + interval env vars for a VNC task.

    Returns an empty list for non-VNC tasks, or when no blob store is
    configured for the org's install (the run still starts; the uploader
    no-ops with an unset URL). Side effect: freezes ``task.snapshot_key``
    when a URL is minted, so the gallery can resolve the GET later.
    """
    if not getattr(task, "vnc_enabled", False):
        return []

    from astrolift_agents.snapshot_store import (
        presigned_snapshot_upload_url,
        snapshot_blob_key,
    )

    org = task.organization
    task_guid = str(task.guid)

    try:
        put_url = presigned_snapshot_upload_url(org=org, task_guid=task_guid)
    except BlobStoreNotConfiguredError:
        logger.info(
            "snapshot_injector: no blob store configured for task %s — "
            "skipping snapshot URL (uploader will no-op)",
            task_guid,
        )
        return []

    # Freeze the key on the task so the gallery can mint a presigned GET even
    # after the spec is edited/deleted.
    key = snapshot_blob_key(task_guid)
    if task.snapshot_key != key:
        task.snapshot_key = key
        task.save(update_fields=["snapshot_key", "updated_at", "version"])

    return [
        {"name": "ASTROLIFT_SNAPSHOT_URL", "value": put_url},
        {
            "name": "ASTROLIFT_SNAPSHOT_INTERVAL",
            "value": str(DEFAULT_SNAPSHOT_INTERVAL_SECONDS),
        },
    ]


def inject_snapshot_into_job_spec(job_spec: dict[str, Any], task: AgentTask) -> dict[str, Any]:
    """Add snapshot env vars to a K8s Job pod spec's primary container.

    Mirrors ``inject_brief_into_job_spec``: mutates the first container's env
    list. No-op (returns unchanged) for non-VNC tasks or when no env vars are
    produced. Returns the updated ``job_spec``.
    """
    env_vars = snapshot_env_vars(task)
    if not env_vars:
        return job_spec

    spec = job_spec.get("spec", {})
    template = spec.get("template", {})
    pod_spec = template.get("spec", {})
    containers = pod_spec.get("containers", [])
    if not containers:
        return job_spec

    existing_env = containers[0].get("env", [])
    containers[0]["env"] = existing_env + env_vars

    pod_spec["containers"] = containers
    template["spec"] = pod_spec
    spec["template"] = template
    job_spec["spec"] = spec
    return job_spec
