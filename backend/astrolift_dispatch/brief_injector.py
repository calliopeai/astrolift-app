"""Brief retrieval and injection into spawned containers (#50).

When the Dispatch Service spawns a container for an AgentTask, the Brief
must be made available to the running agent. The Brief is a content-addressed
snapshot containing manifest_snapshot, secrets_refs, and context.

Injection strategy:
1. The agent container receives ASTROLIFT_BRIEF_ID and ASTROLIFT_BRIEF_HASH
   as environment variables at launch time.
2. The agent's in-pod runtime (the agent SDK) uses these to fetch the Brief
   from the Controller API: GET /api/agents/v1/briefs/<brief_id>/
3. Secrets listed in brief.secrets_refs are NOT included in the Brief payload
   — they are resolved from the org's secret store at agent-task-execution time
   using the agent's scoped credentials.

This approach keeps secret values out of env vars and out of the Brief blob.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrolift_agents.models import AgentTask, Brief


def brief_env_vars(task: AgentTask) -> list[dict[str, str]]:
    """Return the env vars that inject Brief identity into the agent container.

    Returns a list of K8s env var specs (name + value).
    """
    if not task.brief_id:
        return []

    brief = task.brief
    return [
        {"name": "ASTROLIFT_BRIEF_ID", "value": str(brief.guid)},
        {"name": "ASTROLIFT_BRIEF_HASH", "value": brief.content_hash},
        {"name": "ASTROLIFT_TASK_ID", "value": str(task.guid)},
        {"name": "ASTROLIFT_CONTROLLER_URL", "value": _get_controller_url()},
    ]


def inject_brief_into_job_spec(job_spec: dict[str, Any], task: AgentTask) -> dict[str, Any]:
    """Add Brief environment variables to a K8s Job pod spec.

    Mutates the first container's env list in the pod template spec.
    Returns the updated job_spec.
    """
    env_vars = brief_env_vars(task)
    if not env_vars:
        return job_spec

    spec = job_spec.get("spec", {})
    template = spec.get("template", {})
    pod_spec = template.get("spec", {})
    containers = pod_spec.get("containers", [])

    if not containers:
        return job_spec

    # Inject into primary container (first one)
    existing_env = containers[0].get("env", [])
    containers[0]["env"] = env_vars + existing_env

    pod_spec["containers"] = containers
    template["spec"] = pod_spec
    spec["template"] = template
    job_spec["spec"] = spec

    return job_spec


def serialize_brief_for_env(brief: Brief) -> str:
    """Serialize a Brief to a compact JSON string for env var injection.

    This is an alternative to the ID-based approach: the full Brief
    (minus secret values) is injected as a single large env var.
    Used when the agent can't reach the Controller API (air-gapped installs).

    The serialized form omits secrets_refs values — only names are included.
    """
    return json.dumps(
        {
            "id": str(brief.guid),
            "hash": brief.content_hash,
            "manifest": brief.manifest_snapshot,
            "context": brief.context,
            "secret_names": [ref["name"] for ref in (brief.secrets_refs or []) if isinstance(ref, dict)],
        },
        separators=(",", ":"),
    )


def _get_controller_url() -> str:
    """Return the Controller API URL that agents should call back to."""
    from django.conf import settings

    return getattr(settings, "PLATFORM_API_URL", "")
