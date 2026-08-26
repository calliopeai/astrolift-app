"""The slice contract `SHARED_WITH_MAIN` was written for (#1578).

`PreviewPolicy.SHARED_WITH_MAIN` had no consumer because no driver could
satisfy it: `ManagedServiceDriver` exposed provision, update, deprovision,
binding, status, snapshot and restore, and none of them creates a logical
database, an ACL user, or a key prefix *inside* an instance that already
exists. So every `SharedResource` identifier `preview_managed_services`
computed had nowhere to be created and nowhere to be delivered.

The failure this contract is shaped to prevent is the one the issue names:
pointing a preview at the main instance with the main credentials, which is
production data in a PR preview. Hence the guards -- a slice that overrides
nothing is refused, and the composition rule lives in one place.
"""

from __future__ import annotations

import pytest

from _sdk.managed_service import (
    Binding,
    ServiceHandle,
    SliceCapableDriver,
    SliceResult,
    SliceSpec,
    ValueRef,
    apply_slice,
    supports_slicing,
)

PARENT = ServiceHandle(handle="rds:acme-prod")


def _parent_binding() -> Binding:
    return Binding(
        env_vars={
            "POSTGRES_HOST": ValueRef(literal="db.internal"),
            "POSTGRES_PORT": ValueRef(literal="5432"),
            "POSTGRES_DB": ValueRef(literal="acme"),
            "POSTGRES_USER": ValueRef(literal="acme"),
            "POSTGRES_PASSWORD": ValueRef(secret_ref="secret://acme/db"),
        },
        notes="parent instance",
    )


# ---- the spec ------------------------------------------------------------


def test_a_slice_needs_a_caller_owned_name():
    """A slice nobody can name cannot be torn down. Drivers derive their own
    identifiers from this rather than generating one, so the caller can ask
    for the same slice twice and remove the right one."""
    with pytest.raises(ValueError, match="slice_id is required"):
        SliceSpec(slice_id="", parent=PARENT)


def test_a_slice_needs_an_existing_parent():
    with pytest.raises(ValueError, match="existing instance"):
        SliceSpec(slice_id="preview-pr-42", parent=ServiceHandle(handle=""))


def test_a_well_formed_spec_is_accepted():
    spec = SliceSpec(slice_id="preview-pr-42", parent=PARENT, labels={"env": "preview"})

    assert spec.slice_id == "preview-pr-42"
    assert spec.labels == {"env": "preview"}


# ---- the result, and the guard that matters -----------------------------


def test_a_slice_that_overrides_nothing_is_refused():
    """The load-bearing guard. A slice that changes no envelope key is not
    isolation -- the workload would connect to exactly what the parent
    connects to, which is the production-data-in-a-preview failure #1578
    exists to prevent."""
    with pytest.raises(ValueError, match="no env overrides"):
        SliceResult(slice_handle="db:acme_preview_pr_42")


def test_a_result_needs_a_handle():
    with pytest.raises(ValueError, match="slice_handle is required"):
        SliceResult(slice_handle="", env_overrides={"POSTGRES_DB": ValueRef(literal="x")})


# ---- composition --------------------------------------------------------


def test_overrides_replace_only_what_they_name():
    """A sliced postgres reuses the parent's host, port, user and password and
    changes only the database."""
    result = SliceResult(
        slice_handle="db:acme_preview_pr_42",
        env_overrides={"POSTGRES_DB": ValueRef(literal="acme_preview_pr_42")},
    )

    sliced = apply_slice(_parent_binding(), result)

    assert sliced.env_vars["POSTGRES_DB"].literal == "acme_preview_pr_42"
    assert sliced.env_vars["POSTGRES_HOST"].literal == "db.internal"
    assert sliced.env_vars["POSTGRES_PASSWORD"].secret_ref == "secret://acme/db"


def test_composition_never_drops_a_key():
    """Overrides replace and never remove. A driver returning a whole binding
    instead could quietly hand back the parent's database, and nothing would
    notice."""
    parent = _parent_binding()
    result = SliceResult(slice_handle="h", env_overrides={"POSTGRES_DB": ValueRef(literal="sliced")})

    sliced = apply_slice(parent, result)

    assert set(sliced.env_vars) == set(parent.env_vars)


def test_notes_are_carried_from_both():
    result = SliceResult(
        slice_handle="h",
        env_overrides={"POSTGRES_DB": ValueRef(literal="sliced")},
        notes="carved by preview teardown policy",
    )

    sliced = apply_slice(_parent_binding(), result)

    assert "parent instance" in sliced.notes
    assert "preview teardown policy" in sliced.notes


def test_the_parent_binding_is_not_mutated():
    parent = _parent_binding()
    apply_slice(parent, SliceResult(slice_handle="h", env_overrides={"POSTGRES_DB": ValueRef(literal="x")}))

    assert parent.env_vars["POSTGRES_DB"].literal == "acme"


# ---- capability detection ------------------------------------------------


def test_both_verbs_are_required():
    """A driver that can create a slice and not remove one leaves a preview's
    database behind on every teardown, and the leak is invisible until
    someone reads the instance's database list."""

    class _CreateOnly:
        def provision_slice(self, spec):  # pragma: no cover - never called
            ...

    class _Both:
        def provision_slice(self, spec):  # pragma: no cover
            ...

        def deprovision_slice(self, spec, slice_handle):  # pragma: no cover
            ...

    assert not supports_slicing(_CreateOnly())
    assert not supports_slicing(object())
    assert supports_slicing(_Both())


# ---- the envelope gained the keys a prefix needs -------------------------


def test_redis_and_object_store_can_now_carry_a_prefix():
    """The issue's finding: postgres had `POSTGRES_DB` so a per-preview
    database was expressible, but redis had no key-prefix key and
    object_store had no prefix key, so a computed identifier had nowhere to
    be delivered."""
    from _sdk.managed_service_kinds import KINDS

    redis = next(k for k in KINDS.kinds if k.name == "redis")
    object_store = next(k for k in KINDS.kinds if k.name == "object_store")

    assert "REDIS_KEY_PREFIX" in redis.binding_envs_optional
    assert "OBJECT_STORE_PREFIX" in object_store.binding_envs_optional


def test_postgres_already_had_its_slice_key():
    from _sdk.managed_service_kinds import KINDS

    postgres = next(k for k in KINDS.kinds if k.name == "postgres")

    assert "POSTGRES_DB" in postgres.binding_envs_required


def test_slicing_is_not_on_the_conformance_protocol():
    """Why `SliceCapableDriver` is separate, pinned.

    `ManagedServiceDriver` is a conformance contract -- `test_parity.py`
    asserts every real driver implements every method on it. Declaring an
    optional capability there makes it mandatory: putting these two methods
    inline made forty-odd drivers non-conformant in one commit, and the gate
    caught it.

    Which is right. A queue or a topic has no equivalent of a logical
    database, and a driver made to pretend would hand the caller the parent
    instance under a different name.
    """
    from _sdk.managed_service import ManagedServiceDriver, SliceCapableDriver

    assert not hasattr(ManagedServiceDriver, "provision_slice")
    assert hasattr(SliceCapableDriver, "provision_slice")
    assert hasattr(SliceCapableDriver, "deprovision_slice")


def test_every_slicing_driver_matches_the_protocol_signature():
    """The gate that was missing (#1670).

    `CNPGPostgresDriver.deprovision_slice` shipped as
    `(self, slice_handle) -> bool` against a protocol declaring
    `(self, spec, slice_handle) -> None`. Calling it per the contract
    raised TypeError; calling it per the implementation broke the
    contract. Neither happened, because nothing called it at all -- the
    slice could be carved and never removed, which is the leak.

    `supports_slicing` only checks the names are callable, so it returned
    True for a driver whose remove verb could not be invoked. Checking
    arity is what closes that gap.
    """
    import inspect

    from k8s_native.managed.postgres_cnpg import CNPGPostgresDriver

    expected = inspect.signature(SliceCapableDriver.deprovision_slice)

    for driver_cls in (CNPGPostgresDriver,):
        if not supports_slicing(driver_cls()):
            continue
        actual = inspect.signature(driver_cls.deprovision_slice)
        assert list(actual.parameters) == list(expected.parameters), (
            f"{driver_cls.__name__}.deprovision_slice takes "
            f"{list(actual.parameters)}, the contract takes "
            f"{list(expected.parameters)}. supports_slicing() would still "
            "say True, and the call would raise TypeError at teardown."
        )
