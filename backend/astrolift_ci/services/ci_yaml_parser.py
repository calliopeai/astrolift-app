"""
YAML parser for .astrolift/ci.yaml CI pipeline definitions (#870).

Expected format::

    ci:
      - name: "test"
        runs-on: "ubuntu-latest"
        steps:
          - name: "Checkout"
            uses: "actions/checkout@v4"
          - name: "Run tests"
            run: "pytest"

``parse_ci_config``    parses raw YAML text into a plain dict.
``validate_ci_config`` checks structure and returns a list of error messages
                       (empty list = valid).
"""

from __future__ import annotations

import yaml

# Recognised built-in action identifiers (the part before '@').
BUILTIN_ACTIONS: frozenset[str] = frozenset(
    [
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "actions/setup-go",
        "actions/cache",
        "astrolift/docker-build-push",
        "astrolift/deploy",
    ]
)


class CiYamlParseError(ValueError):
    """Raised when the YAML is syntactically invalid."""


def parse_ci_config(yaml_content: str) -> dict:
    """Parse a .astrolift/ci.yaml string and return the resulting dict.

    Raises ``CiYamlParseError`` if the YAML cannot be parsed at all.
    """
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise CiYamlParseError(f"Invalid YAML: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise CiYamlParseError("Top-level YAML value must be a mapping")
    return data


def validate_ci_config(config: dict) -> list[str]:
    """Validate a parsed CI config dict.

    Returns a list of human-readable error strings.  An empty list means
    the config is valid.
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        errors.append("Config must be a mapping")
        return errors

    if "ci" not in config:
        errors.append("Missing required top-level key: 'ci'")
        return errors

    jobs = config["ci"]
    if not isinstance(jobs, list):
        errors.append("'ci' must be a list of job definitions")
        return errors

    if len(jobs) == 0:
        errors.append("'ci' must contain at least one job")

    for job_idx, job in enumerate(jobs):
        prefix = f"ci[{job_idx}]"

        if not isinstance(job, dict):
            errors.append(f"{prefix}: job must be a mapping")
            continue

        if "name" not in job:
            errors.append(f"{prefix}: missing required field 'name'")

        if "steps" not in job:
            errors.append(f"{prefix}: missing required field 'steps'")
            continue

        steps = job["steps"]
        if not isinstance(steps, list):
            errors.append(f"{prefix}: 'steps' must be a list")
            continue

        if len(steps) == 0:
            errors.append(f"{prefix}: 'steps' must contain at least one step")

        for step_idx, step in enumerate(steps):
            step_prefix = f"{prefix}.steps[{step_idx}]"

            if not isinstance(step, dict):
                errors.append(f"{step_prefix}: step must be a mapping")
                continue

            has_uses = "uses" in step
            has_run = "run" in step

            if not has_uses and not has_run:
                errors.append(f"{step_prefix}: step must have either 'uses' or 'run'")

            if has_uses and has_run:
                errors.append(f"{step_prefix}: step cannot have both 'uses' and 'run'")

            if has_uses:
                uses_val = step["uses"]
                if not isinstance(uses_val, str):
                    errors.append(f"{step_prefix}: 'uses' must be a string")

    return errors
