"""Tests for per-env override resolution + tolerations (#139, spec 05 §5-6)."""

from __future__ import annotations

import pytest

from astrolift_manifest.env_overrides import (
    EnvOverrideError,
    Toleration,
    TolerationOperator,
    deep_merge,
    parse_toleration,
    render_tolerations_for_pod,
    resolve_environment_overrides,
)

# ---- deep_merge ----------------------------------------------------


def test_deep_merge_dict_into_dict():
    base = {"resources": {"cpu_request": "100m", "memory": "256Mi"}}
    override = {"resources": {"cpu_request": "500m"}}
    out = deep_merge(base=base, override=override)
    assert out == {"resources": {"cpu_request": "500m", "memory": "256Mi"}}


def test_deep_merge_scalar_override_replaces():
    base = {"replicas": 1}
    override = {"replicas": 5}
    assert deep_merge(base=base, override=override) == {"replicas": 5}


def test_deep_merge_lists_replace_not_concat():
    """Lists are NOT element-merged. Spec 05 §5: anything mergeable
    should be expressed as a dict (env_vars), so lists carry
    semantic 'this is the full set'."""
    base = {"args": ["serve", "--port", "8080"]}
    override = {"args": ["migrate"]}
    assert deep_merge(base=base, override=override) == {"args": ["migrate"]}


def test_deep_merge_adds_keys_from_override():
    base = {"a": 1}
    override = {"b": 2}
    assert deep_merge(base=base, override=override) == {"a": 1, "b": 2}


def test_deep_merge_dict_replaces_scalar():
    """Override widening from scalar to dict — override wins."""
    base = {"config": "simple-string"}
    override = {"config": {"nested": "value"}}
    assert deep_merge(base=base, override=override) == {
        "config": {"nested": "value"},
    }


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1}}
    override = {"a": {"c": 2}}
    out = deep_merge(base=base, override=override)
    assert out == {"a": {"b": 1, "c": 2}}
    assert base == {"a": {"b": 1}}  # untouched
    assert override == {"a": {"c": 2}}  # untouched


# ---- environment override resolution -------------------------------


def test_resolve_with_no_overrides_returns_defaults():
    out = resolve_environment_overrides(
        workload_defaults={"replicas": 1},
        environment_overrides={},
        env_name="prod",
        registered_envs=("prod", "staging"),
    )
    assert out == {"replicas": 1}


def test_resolve_applies_environment_block():
    out = resolve_environment_overrides(
        workload_defaults={
            "replicas": 1,
            "resources": {"cpu_request": "100m"},
        },
        environment_overrides={
            "replicas": 5,
            "resources": {"memory": "1Gi"},
        },
        env_name="prod",
        registered_envs=("prod", "staging"),
    )
    assert out["replicas"] == 5
    assert out["resources"] == {"cpu_request": "100m", "memory": "1Gi"}


def test_resolve_rejects_unregistered_env_name():
    """Typo defense: '[environments.preod]' must surface at
    render time, not silently ship without prod overrides."""
    with pytest.raises(EnvOverrideError, match="not in registered envs"):
        resolve_environment_overrides(
            workload_defaults={},
            environment_overrides={},
            env_name="preod",  # typo
            registered_envs=("prod", "staging"),
        )


# ---- toleration construction ---------------------------------------


def test_toleration_equal_with_key_and_value():
    t = Toleration(
        key="dedicated",
        operator=TolerationOperator.EQUAL,
        value="batch",
        effect="NoSchedule",
    )
    assert t.key == "dedicated"


def test_toleration_exists_with_no_value():
    t = Toleration(
        key="node.kubernetes.io/unreachable",
        operator=TolerationOperator.EXISTS,
        effect="NoExecute",
        toleration_seconds=300,
    )
    assert t.toleration_seconds == 300


def test_toleration_exists_rejects_value():
    """K8s API: when operator=Exists, value field is forbidden."""
    with pytest.raises(EnvOverrideError, match="must not set value"):
        Toleration(
            key="x",
            operator=TolerationOperator.EXISTS,
            value="something",
        )


def test_toleration_equal_with_empty_key_rejected():
    """Equal+empty-key matches taints by value alone — almost
    always a mistake. Operator should use Exists+empty-key
    instead."""
    with pytest.raises(EnvOverrideError, match="non-empty key"):
        Toleration(key="", operator=TolerationOperator.EQUAL)


def test_toleration_exists_with_empty_key_allowed():
    """Exists+empty-key = match all taints. Used for
    cluster-wide bypass tolerations (rare but valid)."""
    t = Toleration(key="", operator=TolerationOperator.EXISTS)
    assert t.key == ""


def test_toleration_invalid_effect_rejected():
    with pytest.raises(EnvOverrideError, match="effect"):
        Toleration(
            key="x", operator=TolerationOperator.EQUAL,
            value="y", effect="Banish",  # not a real k8s effect
        )


def test_toleration_seconds_only_with_no_execute():
    """K8s API: tolerationSeconds only meaningful for NoExecute."""
    with pytest.raises(EnvOverrideError, match="NoExecute"):
        Toleration(
            key="x", operator=TolerationOperator.EQUAL,
            value="y", effect="NoSchedule", toleration_seconds=60,
        )


def test_toleration_seconds_negative_rejected():
    with pytest.raises(EnvOverrideError, match="non-negative"):
        Toleration(
            key="x", operator=TolerationOperator.EXISTS,
            effect="NoExecute", toleration_seconds=-1,
        )


# ---- parse_toleration ----------------------------------------------


def test_parse_toleration_full_form():
    t = parse_toleration({
        "key": "dedicated", "operator": "Equal",
        "value": "batch", "effect": "NoSchedule",
    })
    assert t.key == "dedicated"
    assert t.operator == TolerationOperator.EQUAL


def test_parse_toleration_default_operator():
    """K8s default is Equal."""
    t = parse_toleration({"key": "x", "value": "y"})
    assert t.operator == TolerationOperator.EQUAL


def test_parse_toleration_rejects_bad_operator():
    with pytest.raises(EnvOverrideError, match="operator"):
        parse_toleration({"key": "x", "operator": "Maybe"})


def test_parse_toleration_seconds_must_be_int():
    with pytest.raises(EnvOverrideError, match="int"):
        parse_toleration({
            "key": "x", "operator": "Exists",
            "effect": "NoExecute", "toleration_seconds": "300",  # string
        })


def test_parse_toleration_rejects_non_mapping():
    with pytest.raises(EnvOverrideError):
        parse_toleration("not a mapping")  # type: ignore[arg-type]


# ---- render_tolerations_for_pod ------------------------------------


def test_render_omits_empty_optional_fields():
    """K8s uses field absence to mean defaults; emitting empty
    strings would be wrong."""
    t = Toleration(key="x", operator=TolerationOperator.EQUAL, value="y")
    out = render_tolerations_for_pod((t,))
    assert out == [{"key": "x", "operator": "Equal", "value": "y"}]
    # No 'effect' key, no 'tolerationSeconds'


def test_render_exists_omits_value():
    """Exists never carries value (rejected at construction);
    render should never emit value either."""
    t = Toleration(
        key="node.kubernetes.io/unreachable",
        operator=TolerationOperator.EXISTS,
        effect="NoExecute",
        toleration_seconds=300,
    )
    out = render_tolerations_for_pod((t,))
    assert "value" not in out[0]
    assert out[0]["tolerationSeconds"] == 300


def test_render_includes_effect_when_set():
    t = Toleration(
        key="x", operator=TolerationOperator.EQUAL,
        value="y", effect="NoSchedule",
    )
    out = render_tolerations_for_pod((t,))
    assert out[0]["effect"] == "NoSchedule"
