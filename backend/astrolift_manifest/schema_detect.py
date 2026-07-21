"""Classify an ``astrolift.toml`` body as an app manifest vs an agent
config-repo library (#1172).

Two distinct schemas share the ``astrolift.toml`` filename:

* an **app manifest** — a deployable app, keyed by a top-level ``name``
  and/or one or more ``[[workloads]]`` blocks (parsed by
  :mod:`astrolift_manifest.parser`); and
* an **agent config repo** — a reusable skills/tools *library*, keyed by
  ``astrolift_version`` plus ``[skills.*]`` / ``[tools.*]`` tables and
  carrying neither a top-level ``name`` nor ``[[workloads]]`` (consumed by
  ``astrolift_agents.services.skill_importer`` and the agent-fleet
  onboarding path).

The register-app wizard and the resync path both need to tell these apart
so a config repo gets routed to agent onboarding / skill import instead of
dead-ending as a "broken app manifest". This is a cheap, tolerant
classifier: it never raises, and an unparseable or unrecognised body is
``"unknown"`` rather than an error.
"""

from __future__ import annotations

import tomllib


def detect_toml_schema(toml_text: str) -> str:
    """Return the schema family of an ``astrolift.toml`` body.

    One of:

    * ``"app"`` — has a top-level ``name`` or at least one ``[[workloads]]``
      block. Checked first, so a file that carries *both* a ``name`` and
      ``[skills.*]`` tables classifies as an app (its deployable surface
      wins the tie).
    * ``"agent_config"`` — parses as TOML, has ``astrolift_version`` plus a
      top-level ``[skills.*]`` or ``[tools.*]`` table, and has neither a
      top-level ``name`` nor ``[[workloads]]``.
    * ``"unknown"`` — anything else, including a body that doesn't parse as
      TOML.

    Never raises: a decode error (or any unexpected parse failure) is
    reported as ``"unknown"`` so callers can branch on the result without a
    try/except.
    """
    try:
        data = tomllib.loads(toml_text)
    except Exception:  # noqa: BLE001 — a classifier must never raise; bad TOML is "unknown"
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"

    name = data.get("name")
    has_name = isinstance(name, str) and bool(name.strip())
    # ``[[workloads]]`` parses to a non-empty list; a bare/empty value is not
    # a deployable surface.
    has_workloads = isinstance(data.get("workloads"), list) and bool(data["workloads"])
    if has_name or has_workloads:
        return "app"

    has_version = "astrolift_version" in data
    # The library schema uses ``[skills.<slug>]`` / ``[tools.<slug>]`` — both
    # parse to a dict (table). The app schema's ``skills`` is an *array*, so
    # requiring a dict here keeps an app manifest from matching.
    has_skill_or_tool_table = isinstance(data.get("skills"), dict) or isinstance(data.get("tools"), dict)
    if has_version and has_skill_or_tool_table:
        return "agent_config"

    return "unknown"
