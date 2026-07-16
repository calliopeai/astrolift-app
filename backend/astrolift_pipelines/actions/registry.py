"""Action registry — maps versioned ``uses`` strings to BuiltinAction instances.

Lookup is by the canonical ``<name>@<version>`` key (e.g.
``"astrolift/git-checkout@v1"``).  The registry also accepts bare
``<name>`` keys for forward-compat resolution (resolves to the latest
registered version of that name).

Usage::

    from astrolift_pipelines.actions.registry import resolve_action

    action = resolve_action("astrolift/git-checkout@v1")   # or raises
    steps  = action.render_steps(with_params, env, context)
"""

from __future__ import annotations

from astrolift_pipelines.actions import ActionInputError, BuiltinAction  # noqa: F401
from astrolift_pipelines.actions.builtins import (
    AstroliftDeployAction,
    DockerBuildAction,
    GitCheckoutAction,
    KubectlApplyAction,
)


class UnknownActionError(ValueError):
    """Raised when ``uses`` does not match any registered built-in."""


# Versioned registry — keyed by ``"<name>@<version>"``.
_REGISTRY: dict[str, BuiltinAction] = {
    "astrolift/git-checkout@v1": GitCheckoutAction(),
    "astrolift/docker-build@v1": DockerBuildAction(),
    "astrolift/kubectl-apply@v1": KubectlApplyAction(),
    "astrolift/astrolift-deploy@v1": AstroliftDeployAction(),
}

# Latest-version aliases — keyed by bare ``"<name>"`` without ``@version``.
# Updated by hand when a new major version is published.
_LATEST: dict[str, str] = {
    "astrolift/git-checkout": "astrolift/git-checkout@v1",
    "astrolift/docker-build": "astrolift/docker-build@v1",
    "astrolift/kubectl-apply": "astrolift/kubectl-apply@v1",
    "astrolift/astrolift-deploy": "astrolift/astrolift-deploy@v1",
}


def resolve_action(uses: str) -> BuiltinAction:
    """Return the BuiltinAction for the given ``uses`` string.

    Accepts both ``"name@version"`` and bare ``"name"`` (resolves to
    the current latest version).

    Raises ``UnknownActionError`` if the key is not found, which the
    dispatcher converts into a step-level failure with a clear message
    before the job even starts.
    """
    if uses in _REGISTRY:
        return _REGISTRY[uses]

    # Try bare-name alias.
    versioned = _LATEST.get(uses)
    if versioned and versioned in _REGISTRY:
        return _REGISTRY[versioned]

    known = sorted(_REGISTRY.keys())
    raise UnknownActionError(f"Unknown built-in action: '{uses}'. " f"Known actions: {', '.join(known)}.")


def list_actions() -> list[BuiltinAction]:
    """Return all registered built-in action instances (one per version)."""
    return list(_REGISTRY.values())
