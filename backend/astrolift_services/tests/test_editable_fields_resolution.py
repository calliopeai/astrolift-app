"""`editableFields` must reflect the driver, not a permissive placeholder (#1414).

The client already handles the honest answers: an empty list disables Edit and
points at Re-provision, a restricted list renders one input per key. None of
that could run while every service reported ``["*"]``, and after #1376 the
drivers with no in-place path declare ``[]`` and are refused at the backstop,
late and confusingly, instead of the UI simply not offering the edit.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_services.schema import types as schema_types


class _NoInPlaceUpdates:
    """A driver like the fifteen that #1376 gave an empty editable set."""

    def editable_fields(self) -> list[str]:
        return []


class _RestrictedUpdates:
    def editable_fields(self) -> list[str]:
        return ["size", "retention_days"]


class _PermissiveUpdates:
    def editable_fields(self) -> list[str]:
        return ["*"]


class _NotPure:
    """A driver whose editable_fields reads instance state.

    None exist today, which is what makes the uninitialised-instance call safe.
    One appearing later must not silently fall back to the permissive default.
    """

    def __init__(self, config=None) -> None:
        self._keys = ["from_config"]

    def editable_fields(self) -> list[str]:
        return self._keys


def _service(kind="postgres", variant="rds", plugin_slug="aws"):
    cluster = SimpleNamespace(slug="c1", provider_plugin=SimpleNamespace(slug=plugin_slug))
    return SimpleNamespace(kind=kind, variant=variant, effective_cluster=cluster)


@pytest.fixture(autouse=True)
def _clear_cache():
    schema_types._EDITABLE_FIELDS_CACHE.clear()
    yield
    schema_types._EDITABLE_FIELDS_CACHE.clear()


@pytest.fixture
def registry(monkeypatch):
    """Point the driver lookup at a class we control.

    Raises the real ``DriverNotFound`` rather than a stand-in, so the
    cluster-plugin-then-k8s_native walk in ``managed_resolution`` runs for real
    instead of the miss escaping through it (#1484).
    """
    from astrolift_drivers.registry import DriverNotFound

    state = {"cls": _NoInPlaceUpdates, "calls": 0}

    def get(_slug, _role):
        state["calls"] += 1
        if state["cls"] is None:
            raise DriverNotFound(_role)
        return state["cls"]

    monkeypatch.setattr("astrolift_drivers.registry.plugins", SimpleNamespace(get=get), raising=False)
    return state


# ---- the bug ----------------------------------------------------------------


def test_a_driver_with_no_in_place_path_reports_an_empty_list(registry):
    """The acceptance criterion. Today this is ``["*"]`` for every service."""
    registry["cls"] = _NoInPlaceUpdates

    assert schema_types._editable_fields_for(_service()) == []


def test_a_restricted_driver_reports_its_own_keys(registry):
    registry["cls"] = _RestrictedUpdates

    assert schema_types._editable_fields_for(_service()) == ["size", "retention_days"]


def test_a_permissive_driver_still_reports_everything(registry):
    registry["cls"] = _PermissiveUpdates

    assert schema_types._editable_fields_for(_service()) == ["*"]


def test_resolution_is_on_by_default_now(registry):
    """The flag existed, defaulted off, and no call site ever passed True, so
    the driver's answer never reached a client."""
    import inspect

    signature = inspect.signature(schema_types.managed_service_to_type)

    assert signature.parameters["resolve_editable_fields"].default is True


# ---- cost -------------------------------------------------------------------


def test_the_answer_is_resolved_once_per_variant_not_once_per_row(registry):
    """Per-row driver construction is why this was opt-in. A list query must
    cost one resolution per distinct variant."""
    registry["cls"] = _RestrictedUpdates

    for _ in range(25):
        schema_types._editable_fields_for(_service())

    assert registry["calls"] == 1


def test_different_variants_resolve_separately(registry):
    registry["cls"] = _RestrictedUpdates
    schema_types._editable_fields_for(_service(variant="rds"))
    schema_types._editable_fields_for(_service(variant="aurora_postgres"))

    assert registry["calls"] == 2


def test_the_cached_list_cannot_be_mutated_by_a_caller(registry):
    """The cache hands out the same list object otherwise, and one caller
    appending to it would change every later answer."""
    registry["cls"] = _RestrictedUpdates

    first = schema_types._editable_fields_for(_service())
    first.append("injected")

    assert schema_types._editable_fields_for(_service()) == ["size", "retention_days"]


def test_no_driver_is_constructed_for_a_pure_implementation(registry):
    """Constructing a driver builds cloud SDK clients. That is the cost this
    avoids, so a driver that raises in __init__ must still resolve."""

    class _ExplodesOnInit:
        def __init__(self, *a, **k):
            raise AssertionError("driver was constructed")

        def editable_fields(self) -> list[str]:
            return ["size"]

    registry["cls"] = _ExplodesOnInit

    assert schema_types._editable_fields_for(_service()) == ["size"]


# ---- fallbacks ---------------------------------------------------------------


def test_an_unresolvable_driver_falls_back_to_permissive(registry):
    """Fail-open here on purpose: it only widens what the UI offers, and the
    mutation re-resolves while the driver refuses at the backstop. Narrowing to
    [] on a transient failure would tell an operator their service can never be
    edited, which is worse and less recoverable."""
    registry["cls"] = None

    assert schema_types._editable_fields_for(_service()) == ["*"]


def test_a_service_with_no_cluster_falls_back_to_permissive():
    svc = SimpleNamespace(kind="postgres", variant="rds", effective_cluster=None)

    assert schema_types._editable_fields_for(svc) == ["*"]


def test_an_impure_driver_is_constructed_rather_than_defaulted(registry, monkeypatch):
    """The uninitialised-instance call is safe because all 82 implementations
    are pure. If one stops being pure, it must be built properly, not silently
    reported as fully editable."""
    registry["cls"] = _NotPure
    monkeypatch.setattr(
        "core.cluster_observability.managed_config_for",
        lambda _slug, _cluster, **_kwargs: {},
        raising=False,
    )

    assert schema_types._editable_fields_for(_service()) == ["from_config"]


# ---- in-cluster variants on cloud clusters (#1484) ---------------------------


def test_an_in_cluster_variant_on_a_cloud_cluster_reports_its_own_editable_fields():
    """The UI half of #1484.

    An AKS-hosted service on an in-cluster variant resolved nothing, fell
    through to ``["*"]``, and rendered a single ``*`` input for a driver that
    accepts two keys. The fallback has to reach the GraphQL layer too, not only
    the lifecycle activities."""
    from astrolift_drivers.registry import PluginManifest, PluginRegistry

    registry = PluginRegistry()
    registry.register(
        PluginManifest(
            plugin_id="azure",
            display_name="Azure",
            version="0",
            drivers={"managed:postgres:azure_pg_flex": _PermissiveUpdates},
        ),
    )
    registry.register(
        PluginManifest(
            plugin_id="k8s_native",
            display_name="Kubernetes",
            version="0",
            drivers={"managed:cache:memcached": _RestrictedUpdates},
        ),
    )

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("astrolift_drivers.registry.plugins", registry)
        answer = schema_types._editable_fields_for(
            _service(kind="cache", variant="memcached", plugin_slug="azure"),
        )

    assert answer == ["size", "retention_days"]
