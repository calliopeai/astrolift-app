"""Tests for preview managed-service policies (#90, spec 18 §8)."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.preview_managed_services import (
    PreviewPolicy,
    default_policy_for,
    resolve_policy,
    shared_plan_for,
    teardown_plan_for,
)

# ---- defaults -------------------------------------------------------


def test_cheap_kinds_default_to_shared():
    """Spec 18 §8 — postgres/redis/queue default to shared_with_main
    so previews are cheap to spin up."""
    for kind in ("postgres", "mysql", "redis", "queue", "topic", "object_store"):
        assert default_policy_for(kind) == PreviewPolicy.SHARED_WITH_MAIN


def test_expensive_kinds_default_to_dedicated():
    """Stateful or expensive services get fresh instances per
    preview to avoid contamination."""
    for kind in ("kv_store", "search", "vector_index", "time_series", "document_db", "mq", "nfs"):
        assert default_policy_for(kind) == PreviewPolicy.DEDICATED


def test_unknown_kind_defaults_to_dedicated():
    """Fail-safe — unknown kinds get a fresh instance, no shared
    state contamination."""
    assert default_policy_for("future-thing") == PreviewPolicy.DEDICATED


# ---- resolve_policy -------------------------------------------------


def test_resolve_uses_default_when_no_override():
    assert resolve_policy(kind="postgres", manifest_override=None) == PreviewPolicy.SHARED_WITH_MAIN
    assert resolve_policy(kind="postgres", manifest_override="") == PreviewPolicy.SHARED_WITH_MAIN


def test_resolve_uses_manifest_override():
    assert (
        resolve_policy(
            kind="postgres",
            manifest_override="dedicated",
        )
        == PreviewPolicy.DEDICATED
    )


def test_resolve_rejects_unknown_override():
    """Caller (manifest parser) should validate enum strings; if
    something invalid leaks through, raise."""
    with pytest.raises(ValueError):
        resolve_policy(kind="postgres", manifest_override="someday")


# ---- shared_plan_for ------------------------------------------------


def test_postgres_shared_plan_has_db_and_user():
    plan = shared_plan_for(kind="postgres", pr_number=42, app_slug="api")
    types = {r.resource_type for r in plan.resources}
    assert types == {"database", "user"}
    db = next(r for r in plan.resources if r.resource_type == "database")
    assert db.identifier == "api_pr_42"


def test_mysql_shared_plan_same_shape_as_postgres():
    plan = shared_plan_for(kind="mysql", pr_number=42, app_slug="api")
    types = {r.resource_type for r in plan.resources}
    assert types == {"database", "user"}


def test_redis_shared_plan_has_key_prefix_and_acl_user():
    plan = shared_plan_for(kind="redis", pr_number=42, app_slug="api")
    types = {r.resource_type for r in plan.resources}
    assert types == {"key_prefix", "acl_user"}
    prefix = next(r for r in plan.resources if r.resource_type == "key_prefix")
    assert prefix.identifier == "pr_42:"


def test_object_store_shared_plan_has_bucket_prefix():
    plan = shared_plan_for(kind="object_store", pr_number=42, app_slug="api")
    types = {r.resource_type for r in plan.resources}
    assert types == {"bucket_prefix"}
    prefix = plan.resources[0]
    assert prefix.identifier == "pr_42/"


def test_queue_and_topic_shared_plan_per_preview_resource():
    queue_plan = shared_plan_for(kind="queue", pr_number=42, app_slug="api")
    assert queue_plan.resources[0].resource_type == "queue"
    assert queue_plan.resources[0].identifier == "api-pr_42"

    topic_plan = shared_plan_for(kind="topic", pr_number=42, app_slug="api")
    assert topic_plan.resources[0].identifier == "api-pr_42"


def test_email_sms_shared_plan_uses_message_tag():
    """Email + SMS providers share the org's account; tag messages
    so deliveries don't go to real users from preview pods."""
    for kind in ("email", "sms"):
        plan = shared_plan_for(kind=kind, pr_number=42, app_slug="api")
        assert plan.resources[0].resource_type == "message_tag"
        assert plan.resources[0].identifier == "pr_42"


def test_shared_plan_rejects_dedicated_only_kind():
    """kv_store / search / etc. don't have a sane shared mode —
    raise loudly. Caller (dispatcher) shouldn't have called this."""
    with pytest.raises(ValueError, match="not supported"):
        shared_plan_for(kind="kv_store", pr_number=42, app_slug="api")


def test_shared_plan_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        shared_plan_for(kind="postgres", pr_number=0, app_slug="api")
    with pytest.raises(ValueError):
        shared_plan_for(kind="postgres", pr_number=42, app_slug="")


# ---- teardown_plan_for ----------------------------------------------


def test_teardown_plan_matches_deploy_plan():
    """Critical invariant: the teardown workflow re-derives the
    same plan from (kind, pr_number, app_slug). No persistence
    needed; pure determinism."""
    a = shared_plan_for(kind="postgres", pr_number=42, app_slug="api")
    b = teardown_plan_for(kind="postgres", pr_number=42, app_slug="api")
    assert a == b
