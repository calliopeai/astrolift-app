"""In-place update contract for every registered managed-service driver (#1376).

``ManagedServiceDriver.editable_fields()`` is a promise the platform enforces on
the operator's behalf. ``updateManagedService`` rejects a config change touching
any key outside the returned list *before* it starts a workflow, and everything
inside the list is expected to reach the provider through ``update()``. When the
update workflow's finalize step runs it copies the desired config onto
``ManagedService.applied_config`` and flips the row to ACTIVE, so an ``ok=True``
that applied nothing is recorded by the platform as a change that happened.

That is exactly what shipped. Fifteen drivers implemented ``update()`` as a
courtesy no-op returning ``ok=True`` with a message explaining that the change
really goes somewhere else ("re-apply manifest to scale / reconfigure",
"config changes reconcile on the next provision"), while inheriting the
permissive ``["*"]`` default from the protocol. An operator changing
``storage_size`` on a CNPG cluster, ``access_tier`` on a Blob container or
``max_size_in_megabytes`` on a Service Bus queue got an accepted update, a row
that went back to ACTIVE, and an unchanged backing resource.

The contract this module pins:

* a driver that **claims** editable fields must have an ``update()`` that can
  actually apply them -- it may not be inert;
* a driver that claims **none** (``[]``) must never report ``ok=True``, because
  the only honest answer to "apply this in place" is a refusal.

Why this reads source instead of only calling the drivers
---------------------------------------------------------
Most of the 141 registered drivers cannot be constructed in a test process:
AWS drivers validate their region at ``__init__`` time, GCP drivers build a real
API client that needs application-default credentials. ``tests/
test_binding_envelope_contract.py`` hit the same wall. So the contract is
checked statically against the ``update()`` body, which is the thing that
actually decides what reaches the provider, and is *additionally* checked at
runtime for every no-editable-fields driver that does construct. Both the
"cannot read this statically" and "cannot construct this" sets are pinned
exactly, so neither can grow unnoticed.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
import typing
from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.managed_service import (
    UPDATE_NOT_SUPPORTED_IN_PLACE,
    ManagedServiceDriver,
    UpdateSpec,
    unsupported_update,
)
from aws.plugin import PLUGIN as AWS_PLUGIN
from azure.plugin import PLUGIN as AZURE_PLUGIN
from gcp.plugin import PLUGIN as GCP_PLUGIN
from k8s_native.plugin import PLUGIN as K8S_PLUGIN

_PLUGINS = (AWS_PLUGIN, AZURE_PLUGIN, GCP_PLUGIN, K8S_PLUGIN)


# --------------------------------------------------------------------------
# Contract data
# --------------------------------------------------------------------------

# Drivers whose ``update()`` this module cannot classify. Each needs an issue;
# none may be added without one. Empty: every registered driver's ``update()``
# returns a directly-constructed ``UpdateResult`` or ``unsupported_update()``.
_UNREADABLE_UPDATES: dict[tuple[str, str, str], str] = {}


# Drivers whose ``update()`` unconditionally raises. They cannot report a false
# success by construction, so they take no part in the comparisons below. Both
# are third-party stubs that raise from every lifecycle method.
_NEVER_RETURNS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("gcp", "email", "gcp_thirdparty"),
        ("gcp", "search", "gcp_elastic_cloud"),
    },
)


# No-editable-fields drivers that cannot be instantiated here, so only the
# static half of the contract covers them. Value is the reason.
_NOT_CONSTRUCTIBLE: dict[tuple[str, str, str], str] = {
    ("gcp", "object_store", "gcs"): "imports google.cloud.storage at __init__",
    ("gcp", "queue", "pubsub"): "builds a real Pub/Sub client, which needs ADC",
}


# Method names that cannot mutate a backing resource: string, mapping and
# logging helpers. Everything else called on an object -- a cloud SDK client, a
# cluster driver, ``self`` -- is treated as potential provider work, so a body
# containing one is never classified inert.
_NON_MUTATING_CALLS = frozenset(
    {
        "append",
        "debug",
        "error",
        "exception",
        "format",
        "get",
        "info",
        "items",
        "join",
        "keys",
        "lower",
        "split",
        "strip",
        "upper",
        "values",
        "warning",
    },
)


# --------------------------------------------------------------------------
# Registry walk
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Driver:
    plugin_id: str
    kind: str
    variant: str
    cls: type[Any]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.plugin_id, self.kind, self.variant)

    @property
    def label(self) -> str:
        return f"{self.plugin_id}/{self.kind}/{self.variant}"


def _registered_drivers() -> tuple[_Driver, ...]:
    out: list[_Driver] = []
    for plugin in _PLUGINS:
        for (kind, variant), cls in sorted(plugin.managed_service_drivers.items()):
            out.append(_Driver(plugin_id=plugin.id, kind=kind, variant=variant, cls=cls))
    return tuple(out)


DRIVERS = _registered_drivers()


# --------------------------------------------------------------------------
# Static reading
# --------------------------------------------------------------------------


class _Unreadable(Exception):
    """``update()`` does not expose its result shape to static reading."""


RAISES = "raises"
REFUSES = "refuses"
INERT = "inert"
APPLIES = "applies"


def _function_ast(func: Any) -> ast.FunctionDef:
    func = inspect.unwrap(getattr(func, "__wrapped__", func))
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError) as exc:  # pragma: no cover - defensive
        raise _Unreadable(f"no source for {func!r}") from exc
    node = ast.parse(source).body[0]
    if not isinstance(node, ast.FunctionDef):
        raise _Unreadable(f"{func!r} is not a plain function")
    return node


def _owner_of(cls: type[Any], name: str) -> type[Any]:
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass
    raise _Unreadable(f"{cls.__name__} has no {name}()")


def _executable_body(fn: ast.FunctionDef) -> list[ast.stmt]:
    """``fn``'s statements minus its docstring."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _ok_flag(call: ast.Call) -> ast.expr | None:
    """The ``ok`` argument of an ``UpdateResult(...)`` call, keyword or first positional."""
    for keyword in call.keywords:
        if keyword.arg == "ok":
            return keyword.value
    return call.args[0] if call.args else None


def _does_provider_work(fn: ast.FunctionDef, pure: frozenset[str]) -> bool:
    """Whether ``fn`` could have changed anything before returning.

    A driver cannot reach a cloud API or a cluster without calling a method on
    something, so a body whose only attribute calls are string/mapping/logging
    helpers has demonstrably applied nothing.
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr not in pure:
            return True
    return False


def _update_behaviour(cls: type[Any], pure: frozenset[str] = _NON_MUTATING_CALLS) -> str:
    """Classify what ``cls.update()`` can report.

    ``refuses``  cannot return ``ok=True`` on any path.
    ``inert``    can return ``ok=True`` having performed no provider work.
    ``applies``  can return ``ok=True`` after doing something.
    ``raises``   never returns at all.
    """
    owner = _owner_of(cls, "update")
    if owner is ManagedServiceDriver:
        raise _Unreadable("update() is the protocol stub, not an implementation")
    fn = _function_ast(owner.__dict__["update"])
    body = _executable_body(fn)
    if body and all(isinstance(stmt, ast.Raise) for stmt in body):
        return RAISES

    returns = [node for node in ast.walk(fn) if isinstance(node, ast.Return) and node.value is not None]
    if not returns:
        raise _Unreadable("update() constructs no result")
    reports_success = False
    for node in returns:
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            raise _Unreadable(f"update() returns {ast.unparse(value)}, not a result constructor")
        if value.func.id == "unsupported_update":
            continue
        if value.func.id != "UpdateResult":
            raise _Unreadable(f"update() returns {value.func.id}(...)")
        flag = _ok_flag(value)
        if not isinstance(flag, ast.Constant):
            raise _Unreadable(f"update() builds UpdateResult with a non-literal ok: {ast.unparse(value)}")
        reports_success = reports_success or bool(flag.value)

    if not reports_success:
        return REFUSES
    return APPLIES if _does_provider_work(fn, pure) else INERT


def _declared_editable_fields(cls: type[Any]) -> list[str]:
    """``editable_fields()`` read statically, so uninstantiable drivers count too.

    A driver that does not declare the method inherits the protocol's permissive
    ``["*"]``, which is the shape that let #1376 happen; it is reported as such
    rather than as an absence.
    """
    owner = _owner_of(cls, "editable_fields")
    if owner is ManagedServiceDriver:
        return ["*"]
    fn = _function_ast(owner.__dict__["editable_fields"])
    returns = [node.value for node in ast.walk(fn) if isinstance(node, ast.Return) and node.value is not None]
    if not returns:
        raise _Unreadable("editable_fields() returns nothing")
    if len(returns) == 1 and isinstance(returns[0], ast.List):
        if not returns[0].elts:
            return []
        literals = [item.value for item in returns[0].elts if isinstance(item, ast.Constant)]
        return literals or ["<computed>"]
    # A sorted()/set-difference expression names real keys; the only distinction
    # this contract needs is empty vs non-empty, and this branch is never empty.
    return ["<computed>"]


def _construct(cls: type[Any]) -> Any:
    """Best-effort instantiation: no-arg, else a config with placeholder strings."""
    try:
        return cls()
    except TypeError:
        pass
    config_cls = typing.get_type_hints(cls.__init__)["config"]
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(config_cls):
        if field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING:
            continue
        if field.type not in ("str", str):
            raise _Unreadable(f"{config_cls.__name__}.{field.name} is a required {field.type}")
        kwargs[field.name] = "us-east-1" if field.name == "region" else "contract-probe"
    return cls(config=config_cls(**kwargs))


def _no_editable_fields_drivers() -> tuple[_Driver, ...]:
    return tuple(driver for driver in DRIVERS if _declared_editable_fields(driver.cls) == [])


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_every_registered_driver_update_is_statically_readable() -> None:
    """No silent skips: an unclassifiable ``update()`` has to be ledgered."""
    unreadable: dict[tuple[str, str, str], str] = {}
    never_returns: set[tuple[str, str, str]] = set()
    for driver in DRIVERS:
        try:
            if _update_behaviour(driver.cls) == RAISES:
                never_returns.add(driver.key)
        except _Unreadable as exc:
            unreadable[driver.key] = str(exc)

    assert set(unreadable) == set(_UNREADABLE_UPDATES), (
        "the set of drivers whose update() cannot be classified changed. "
        f"newly unreadable={ {k: unreadable[k] for k in sorted(set(unreadable) - set(_UNREADABLE_UPDATES))} } "
        f"now readable (drop the ledger entry)={sorted(set(_UNREADABLE_UPDATES) - set(unreadable))}"
    )
    assert never_returns == _NEVER_RETURNS, (
        "the set of drivers whose update() unconditionally raises changed: "
        f"new={sorted(never_returns - _NEVER_RETURNS)} gone={sorted(_NEVER_RETURNS - never_returns)}"
    )


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda d: d.label)
def test_driver_that_claims_editable_fields_can_apply_them(driver: _Driver) -> None:
    """#1376's failure mode: ``["*"]`` inherited next to a do-nothing ``update()``.

    A driver reporting ``ok=True`` from a body that calls nothing has applied
    nothing, and the platform will record the desired config as applied.
    """
    if driver.key in _UNREADABLE_UPDATES:
        pytest.xfail(f"update() not statically readable, tracked in {_UNREADABLE_UPDATES[driver.key]}")
    claimed = _declared_editable_fields(driver.cls)
    if not claimed:
        return
    assert _update_behaviour(driver.cls) != INERT, (
        f"{driver.label} says {claimed} can be changed in place, but its update() reports "
        "ok=True without calling anything, so nothing reaches the provider. Either implement "
        "the in-place path, or narrow editable_fields() and refuse with unsupported_update()."
    )


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda d: d.label)
def test_driver_with_no_editable_fields_never_reports_success(driver: _Driver) -> None:
    """``editable_fields() == []`` and ``ok=True`` cannot both be true.

    The row's finalize step trusts ``ok``: it advances ``applied_config`` and
    returns the service to ACTIVE. A driver with nothing it can apply has to say
    so, or the platform records a change that never happened.
    """
    if driver.key in _UNREADABLE_UPDATES:
        pytest.xfail(f"update() not statically readable, tracked in {_UNREADABLE_UPDATES[driver.key]}")
    if _declared_editable_fields(driver.cls) != []:
        return
    assert _update_behaviour(driver.cls) in {REFUSES, RAISES}, (
        f"{driver.label} declares no editable fields yet its update() can return ok=True. "
        "Return unsupported_update(spec.handle, <reason>) so the operator is told the change "
        "needs a reprovision instead of being told it landed."
    )


def test_drivers_with_no_editable_fields_are_the_expected_set() -> None:
    """The ``[]`` declarations are load-bearing, so pin who makes them.

    A driver silently regaining the permissive default would make both
    assertions above vacuous for it.
    """
    assert {driver.key for driver in _no_editable_fields_drivers()} == {
        ("aws", "cdn", "cloudfront"),
        ("aws", "object_store", "s3"),
        ("azure", "object_store", "azure_blob"),
        ("azure", "object_store", "blob"),
        ("azure", "queue", "servicebus"),
        ("gcp", "object_store", "gcs"),
        ("gcp", "queue", "pubsub"),
        ("k8s_native", "cache", "memcached"),
        ("k8s_native", "document_db", "mongodb_operator"),
        ("k8s_native", "event_stream", "kafka_strimzi"),
        ("k8s_native", "event_stream", "nats"),
        ("k8s_native", "filesystem", "nfs_csi"),
        ("k8s_native", "mysql", "operator"),
        ("k8s_native", "postgres", "cnpg"),
        ("k8s_native", "queue", "rabbitmq_operator"),
        ("k8s_native", "redis", "operator"),
    }


def test_uninstantiable_no_editable_fields_drivers_are_pinned() -> None:
    """Only the static half covers these, so the exclusion may not grow quietly."""
    failed: dict[tuple[str, str, str], str] = {}
    for driver in _no_editable_fields_drivers():
        try:
            _construct(driver.cls)
        except Exception as exc:  # any construction failure counts, not just TypeError
            failed[driver.key] = type(exc).__name__
    assert set(failed) == set(_NOT_CONSTRUCTIBLE), (
        "the set of no-editable-fields drivers that cannot be instantiated changed: "
        f"new={ {k: failed[k] for k in sorted(set(failed) - set(_NOT_CONSTRUCTIBLE))} } "
        f"now constructible (drop the ledger entry)={sorted(set(_NOT_CONSTRUCTIBLE) - set(failed))}"
    )


@pytest.mark.parametrize(
    "driver",
    [d for d in _no_editable_fields_drivers() if d.key not in _NOT_CONSTRUCTIBLE],
    ids=lambda d: d.label,
)
def test_no_editable_fields_driver_refuses_at_runtime(driver: _Driver) -> None:
    """The static reading, confirmed by actually calling the driver."""
    result = _construct(driver.cls).update(UpdateSpec(handle="probe/handle", config={"size": "large"}))
    assert result.ok is False, f"{driver.label} reported success for an update it cannot perform"
    assert result.retryable is False, f"{driver.label} asks to be retried for a permanently impossible update"
    assert result.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    assert "reprovision" in result.message.lower(), (
        f"{driver.label} refuses without telling the operator what to do instead: {result.message!r}"
    )
    assert result.handle == "probe/handle"


def test_unsupported_update_is_a_permanent_refusal() -> None:
    """The shape the update activity depends on to stop retrying and fail loudly."""
    result = unsupported_update("postgres/primary", "CNPG reconciles on provision")
    assert result.ok is False
    assert result.retryable is False
    assert result.handle == "postgres/primary"
    assert result.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    assert result.message == "CNPG reconciles on provision; apply this change with reprovisionManagedService"


def test_protocol_default_is_still_the_permissive_one() -> None:
    """The classifier's baseline. If the default changes, every driver that
    inherits it changes meaning, and ``_declared_editable_fields`` is wrong."""
    assert ManagedServiceDriver.editable_fields(object()) == ["*"]  # type: ignore[arg-type]


def test_non_mutating_call_list_does_not_hide_provider_work() -> None:
    """The allowlist decides what counts as an inert body, so keep it honest.

    Reclassifying every allowlisted name as provider work must not change any
    driver's verdict: if it does, some driver's only "work" is a string or
    mapping helper and it is inert in fact.
    """
    readable = [d for d in DRIVERS if d.key not in _UNREADABLE_UPDATES]
    strict = {d.label for d in readable if _update_behaviour(d.cls, frozenset()) == INERT}
    lenient = {d.label for d in readable if _update_behaviour(d.cls) == INERT}
    assert strict == lenient, (
        "the pure-call allowlist changes a driver's classification: "
        f"only inert when allowlisted={sorted(lenient - strict)}"
    )
