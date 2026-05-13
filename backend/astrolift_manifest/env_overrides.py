"""
Per-environment override resolution + workload tolerations
(#139, spec 05 §5-6).

Pure-Python policy. The manifest renderer consults this to:

* Merge ``[environments.<name>]`` blocks over workload defaults
  with deep-merge semantics for nested config (env vars,
  resource limits, annotations).
* Parse + validate ``[[workloads.<name>.tolerations]]`` arrays
  per the k8s constraint set, then render to PodSpec.tolerations.

Pairs with #117 env_injection (which does runtime env-var
precedence) — this module is for static manifest-time merge of
environment overrides BEFORE rendering.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import StrEnum


class EnvOverrideError(ValueError):
    pass


# ---- deep merge ----------------------------------------------------


def deep_merge(
    *,
    base: Mapping,
    override: Mapping,
) -> dict:
    """Recursive merge: dict-into-dict deep-merges, anything else
    in ``override`` replaces the value in ``base``.

    Lists are NOT element-merged — they replace in full. Operators
    expressing 'add a single env var' must use a dict (env_vars)
    not a list. Spec 05 §5 calls this out: 'env_vars: dict' is
    why we use mappings everywhere.
    """
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], Mapping) and isinstance(value, Mapping):
            out[key] = deep_merge(base=out[key], override=value)
        else:
            out[key] = value
    return out


def resolve_environment_overrides(
    *,
    workload_defaults: Mapping,
    environment_overrides: Mapping,
    env_name: str,
    registered_envs: tuple[str, ...],
) -> dict:
    """Merge ``[environments.<env_name>]`` block over workload
    defaults.

    Validates env_name is registered — typos in [environments.preod]
    must surface at render time, not silently ship a manifest
    that's missing the prod overrides.
    """
    if env_name not in registered_envs:
        raise EnvOverrideError(
            f"environment {env_name!r} not in registered envs {sorted(registered_envs)}; check for typo"
        )
    if not environment_overrides:
        return dict(workload_defaults)
    return deep_merge(base=workload_defaults, override=environment_overrides)


# ---- k8s tolerations -----------------------------------------------


class TolerationOperator(StrEnum):
    """K8s allows two: Equal (matches if value matches),
    Exists (matches any value, value field forbidden)."""

    EQUAL = "Equal"
    EXISTS = "Exists"


class TolerationEffect(StrEnum):
    """K8s taint effects."""

    NO_SCHEDULE = "NoSchedule"
    PREFER_NO_SCHEDULE = "PreferNoSchedule"
    NO_EXECUTE = "NoExecute"


@dataclasses.dataclass(frozen=True, slots=True)
class Toleration:
    """K8s toleration. Field constraints per the k8s API:

    * ``operator=Exists`` ⇒ ``value`` must be empty
    * ``operator=Equal`` ⇒ ``value`` may be empty (matches taints
      with empty value)
    * ``effect=""`` ⇒ matches ALL effects
    * ``toleration_seconds`` only valid when ``effect=NoExecute``
    """

    key: str
    operator: TolerationOperator
    effect: str = ""  # "" | NoSchedule | PreferNoSchedule | NoExecute
    value: str = ""
    toleration_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.key and self.operator == TolerationOperator.EQUAL:
            # Empty key with Equal = matches all taints with the
            # given value. K8s allows it but it's almost always a
            # mistake; reject for safety. Operator can use
            # operator=Exists with empty key to match all.
            raise EnvOverrideError(
                "toleration with operator=Equal requires non-empty key "
                "(use operator=Exists with empty key to match all)"
            )

        if self.operator == TolerationOperator.EXISTS and self.value:
            raise EnvOverrideError("toleration with operator=Exists must not set value")

        if self.effect and self.effect not in (
            "NoSchedule",
            "PreferNoSchedule",
            "NoExecute",
        ):
            raise EnvOverrideError(
                f"toleration effect {self.effect!r} not one of "
                "{'', 'NoSchedule', 'PreferNoSchedule', 'NoExecute'}"
            )

        if self.toleration_seconds is not None:
            if self.effect != "NoExecute":
                raise EnvOverrideError("toleration_seconds only valid when effect=NoExecute")
            if self.toleration_seconds < 0:
                raise EnvOverrideError("toleration_seconds must be non-negative")


def parse_toleration(raw: Mapping) -> Toleration:
    """Project a TOML ``[[workloads.<name>.tolerations]]`` entry
    onto the typed dataclass. Raises EnvOverrideError on any
    constraint violation."""
    if not isinstance(raw, Mapping):
        raise EnvOverrideError("toleration entry must be a mapping")

    op_raw = raw.get("operator", "Equal")
    try:
        operator = TolerationOperator(op_raw)
    except ValueError as exc:
        raise EnvOverrideError(f"toleration operator {op_raw!r} must be 'Equal' or 'Exists'") from exc

    sec = raw.get("toleration_seconds")
    if sec is not None and not isinstance(sec, int):
        raise EnvOverrideError("toleration_seconds must be an int (seconds)")

    return Toleration(
        key=str(raw.get("key", "")),
        operator=operator,
        effect=str(raw.get("effect", "")),
        value=str(raw.get("value", "")),
        toleration_seconds=sec,
    )


def render_tolerations_for_pod(
    tolerations: tuple[Toleration, ...],
) -> list[dict]:
    """Render to the k8s PodSpec.tolerations shape. Empty fields
    are omitted (k8s uses absence to mean default)."""
    out: list[dict] = []
    for t in tolerations:
        entry: dict = {"operator": t.operator.value}
        if t.key:
            entry["key"] = t.key
        if t.effect:
            entry["effect"] = t.effect
        if t.value and t.operator == TolerationOperator.EQUAL:
            entry["value"] = t.value
        if t.toleration_seconds is not None:
            entry["tolerationSeconds"] = t.toleration_seconds
        out.append(entry)
    return out
