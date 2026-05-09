"""Tests for the FakeDriver harness (#188)."""

from __future__ import annotations

import asyncio

import pytest

from core.testing.fakes import (
    DEFAULT,
    FakeClusterDriver,
    FakeDnsDriver,
    FakeDriver,
    FakeManagedServiceDriver,
    UnimplementedFakeMethod,
)

# ---- spy --------------------------------------------------------------


def test_record_call_logs_method_args_kwargs_in_order():
    fake = FakeDriver()
    fake.record_call("apply", "ctx", spec={"kind": "Deployment"})
    fake.record_call("delete", "ctx")
    assert [c.method for c in fake.calls] == ["apply", "delete"]
    assert fake.calls[0].args == ("ctx",)
    assert fake.calls[0].kwargs == {"spec": {"kind": "Deployment"}}


def test_assert_called_passes_with_count():
    fake = FakeDriver()
    fake.record_call("apply")
    fake.record_call("apply")
    matched = fake.assert_called("apply", times=2)
    assert len(matched) == 2


def test_assert_called_raises_on_mismatch():
    fake = FakeDriver()
    fake.record_call("apply")
    with pytest.raises(AssertionError):
        fake.assert_called("apply", times=2)
    with pytest.raises(AssertionError):
        fake.assert_called("never_called")


def test_assert_not_called_raises_when_called():
    fake = FakeDriver()
    fake.assert_not_called("apply")  # passes
    fake.record_call("apply")
    with pytest.raises(AssertionError):
        fake.assert_not_called("apply")


# ---- response queue ---------------------------------------------------


def test_set_responses_pops_in_order():
    fake = FakeDriver()
    fake.set_responses("get", [{"x": 1}, {"x": 2}])
    assert fake.next_response("get") == {"x": 1}
    assert fake.next_response("get") == {"x": 2}
    assert fake.next_response("get") is DEFAULT


def test_next_response_required_raises_on_empty_queue():
    """provision() and update() use ``required=True`` because they
    must return a real handle — DEFAULT would silently break tests."""
    fake = FakeDriver()
    with pytest.raises(UnimplementedFakeMethod):
        fake.next_response("provision", required=True)


# ---- error injection --------------------------------------------------


def test_set_error_raises_on_record_call():
    fake = FakeDriver()
    fake.set_error("apply", RuntimeError("kube apiserver 503"))
    with pytest.raises(RuntimeError, match="503"):
        fake.record_call("apply")


def test_error_persists_across_calls_until_reset():
    fake = FakeDriver()
    fake.set_error("apply", RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        fake.record_call("apply")
    with pytest.raises(RuntimeError):
        fake.record_call("apply")
    # but the failed call still gets logged each time — useful for
    # asserting retry behavior under failure injection
    assert len(fake.calls) == 2


def test_reset_clears_calls_responses_and_errors():
    fake = FakeDriver()
    fake.record_call("apply")
    fake.set_responses("get", [1])
    fake.set_error("delete", RuntimeError("x"))
    fake.reset()
    assert fake.calls == []
    assert fake.next_response("get") is DEFAULT
    fake.record_call("delete")  # would raise pre-reset


# ---- fall-through -----------------------------------------------------


def test_unimplemented_method_raises_clear_error():
    fake = FakeDriver()
    with pytest.raises(UnimplementedFakeMethod, match="random_method"):
        fake.random_method()


# ---- concrete subclasses ---------------------------------------------


def test_fake_cluster_driver_records_apply():
    fake = FakeClusterDriver()
    fake.set_responses("apply", [None])
    asyncio.run(fake.apply(cluster="prod", objects=[{"kind": "Deployment"}]))
    fake.assert_called("apply", times=1)
    last = fake.calls[-1]
    assert last.kwargs["cluster"] == "prod"
    assert last.kwargs["objects"] == [{"kind": "Deployment"}]


def test_fake_cluster_driver_get_returns_none_when_unstubbed():
    """``get`` is the lookup path — defaulting to None matches the
    real driver protocol so tests for 'object missing' don't have
    to queue an empty response."""
    fake = FakeClusterDriver()
    out = asyncio.run(fake.get(cluster="prod", ref="x"))
    assert out is None


def test_fake_cluster_driver_get_returns_queued_response():
    fake = FakeClusterDriver()
    fake.set_responses("get", [{"kind": "Deployment", "metadata": {"name": "web"}}])
    out = asyncio.run(fake.get(cluster="prod", ref="x"))
    assert out["metadata"]["name"] == "web"


def test_fake_dns_driver_list_defaults_to_empty():
    fake = FakeDnsDriver()
    out = asyncio.run(fake.list(zone="example.com"))
    assert out == []


def test_fake_managed_service_driver_provision_requires_response():
    """provision must return a handle. An unconfigured fake should
    fail loudly — silent ``DEFAULT`` handles caused real bugs in
    earlier prototypes (None-safe assertions hid the gap)."""
    fake = FakeManagedServiceDriver()
    with pytest.raises(UnimplementedFakeMethod):
        asyncio.run(fake.provision(plan="x"))


def test_error_injection_propagates_through_concrete_method():
    fake = FakeClusterDriver()
    fake.set_error("apply", TimeoutError("apiserver slow"))
    with pytest.raises(TimeoutError):
        asyncio.run(fake.apply(cluster="prod", objects=[]))
    fake.assert_called("apply", times=1)
