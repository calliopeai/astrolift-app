"""Built-in action stdlib for Astrolift pipelines.

A BuiltinAction is a versioned, first-party step implementation.
Pipeline TOML refers to built-ins via ``uses = "astrolift/git-checkout@v1"``.
At dispatch the spawner resolves ``uses`` to an implementation here and
renders the concrete shell steps to execute.

Built-ins are NOT external container images — they are code shipped with
Astrolift and validated at parse time against their input schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InputSpec:
    """Schema for a single action input parameter."""

    required: bool = False
    default: Any = None
    description: str = ""


class ActionInputError(ValueError):
    """Raised when required inputs are missing or invalid."""


class BuiltinAction:
    """Abstract base for all built-in pipeline actions.

    Subclasses must declare:
      - ``name``: canonical name without the version suffix
        (e.g. ``"astrolift/git-checkout"``).
      - ``description``: one-line summary.
      - ``inputs``: mapping of parameter name to InputSpec.

    And implement ``render_steps``.
    """

    name: str
    description: str
    inputs: dict[str, InputSpec] = {}

    def validate_inputs(self, with_params: dict) -> dict:
        """Validate ``with_params`` against the declared input schema.

        Returns a merged dict of provided + default values.
        Raises ``ActionInputError`` on missing required inputs.
        """
        resolved: dict[str, Any] = {}
        for param_name, spec in self.inputs.items():
            if param_name in with_params:
                resolved[param_name] = with_params[param_name]
            elif spec.required:
                raise ActionInputError(
                    f"Action '{self.name}': required input '{param_name}' is missing."
                )
            else:
                resolved[param_name] = spec.default
        return resolved

    def render_steps(
        self,
        with_params: dict,
        env: dict,
        context: dict,
    ) -> list[dict]:
        """Return a list of shell-command step dicts to execute.

        Each step dict has at minimum:
          ``{"run": "<shell command string>"}``
        and optionally ``{"name": "...", "env": {...}}``.

        Implementors must call ``self.validate_inputs(with_params)``
        first to resolve defaults and catch missing required fields.
        """
        raise NotImplementedError
