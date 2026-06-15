"""Agent runtime catalog — maps a short runtime name to its public image.

The 12 agent runtimes are published to Docker Hub as
``docker.io/calliopeai/astrolift-agent-<name>`` (plus a ``-vnc`` watchable
variant of each). Rather than have every :class:`AgentEnvironmentSpec`
carry a free-form image string, an operator can select a runtime by name
and the dispatcher resolves it to the public image at spawn time.

Resolution precedence at spawn (see ``spawners.k8s_job._render_agent_job``):

1. An explicit ``AgentEnvironmentSpec.image_tag`` always wins — a pinned
   private/ECR image overrides the catalog.
2. Otherwise the spec's ``runtime`` name resolves through this catalog to
   ``<registry>/astrolift-agent-<name>:<tag>``.
3. Otherwise the dispatcher falls back to the workload's primary container
   image.

The ``-vnc`` watchable variant is *not* a separate catalog entry: it is
composed from whichever base image was resolved, by the spawner's
``_vnc_image`` helper. ``resolve_runtime_image(name) -> base``; the spawner
then applies ``-vnc`` when the task is VNC-enabled.

Registry / tag are env-var configurable to match the codebase's env-driven
infra config convention (cf. ``PIPELINE_ARTIFACT_LOCAL_PATH`` for the blob
store); they are not Django settings:

* ``ASTROLIFT_AGENT_REGISTRY`` — image namespace (default ``docker.io/calliopeai``)
* ``ASTROLIFT_AGENT_RUNTIME_TAG`` — default tag when a runtime resolves
  without an explicit pin (default ``latest``)
"""

from __future__ import annotations

import os

# The published runtime short-names. Each maps to
# ``<registry>/astrolift-agent-<name>`` with a ``-vnc`` watchable variant.
# ``base`` is the shared base layer (selectable for a bare environment).
RUNTIME_NAMES: tuple[str, ...] = (
    "claude",
    "codex",
    "opencode",
    "mistral",
    "deepseek",
    "claude-code",
    "codex-cli",
    "aider",
    "goose",
    "openhands",
    "calliope-cli",
    "base",
)

# Default public Docker Hub namespace the runtimes are published under.
_DEFAULT_REGISTRY = "docker.io/calliopeai"
# Default image tag used when a runtime is selected without an explicit pin.
_DEFAULT_TAG = "latest"

# Image repository stem: ``<registry>/astrolift-agent-<name>``.
_IMAGE_STEM = "astrolift-agent"


def registry_namespace() -> str:
    """Return the configured image namespace for the agent runtimes.

    Defaults to the public Docker Hub namespace; override via the
    ``ASTROLIFT_AGENT_REGISTRY`` env var for a mirror or private registry.
    """
    return os.environ.get("ASTROLIFT_AGENT_REGISTRY", _DEFAULT_REGISTRY).rstrip("/")


def default_tag() -> str:
    """Return the default tag used when a runtime resolves without a pin."""
    return os.environ.get("ASTROLIFT_AGENT_RUNTIME_TAG", _DEFAULT_TAG)


def is_known_runtime(name: str) -> bool:
    """True when ``name`` is a catalog runtime short-name."""
    return name in RUNTIME_NAMES


def runtime_repo(name: str) -> str:
    """Return the (untagged) image repository for runtime ``name``.

    e.g. ``claude`` -> ``docker.io/calliopeai/astrolift-agent-claude``.
    Raises ``KeyError`` for an unknown runtime so a misconfigured spec
    fails loudly rather than spawning a nonexistent image.
    """
    if name not in RUNTIME_NAMES:
        raise KeyError(f"unknown agent runtime: {name!r}")
    return f"{registry_namespace()}/{_IMAGE_STEM}-{name}"


def resolve_runtime_image(name: str, *, tag: str | None = None) -> str:
    """Return the fully-qualified base image ref for runtime ``name``.

    ``tag`` overrides the default tag (``ASTROLIFT_AGENT_RUNTIME_TAG`` or
    ``latest``). The returned ref is the *base* image; the spawner applies
    the ``-vnc`` variant separately when the task is VNC-enabled.

    Raises ``KeyError`` for an unknown runtime.
    """
    repo = runtime_repo(name)
    return f"{repo}:{tag or default_tag()}"


def catalog_entries() -> list[dict[str, str]]:
    """Return the catalog as a list of ``{"name", "image"}`` dicts.

    Used by the GraphQL read surface to list selectable runtimes. The
    ``image`` is the default-tagged base ref; the watchable ``-vnc``
    variant is derived at spawn, not listed here.
    """
    return [{"name": name, "image": resolve_runtime_image(name)} for name in RUNTIME_NAMES]
