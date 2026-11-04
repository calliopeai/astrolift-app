"""Pure comparison behind the managed-service match preview and apply (#2606).

No database and no Temporal: the plan is a function of declared vs recorded.
"""

from __future__ import annotations

from astrolift_manifest.managed_service_match import (
    APP,
    BIND,
    BLOCKED,
    CREATE,
    DETACH,
    PROJECT,
    RECONFIGURE,
    REMOVE,
    RETRY,
    RecordedService,
    ServiceSpec,
    plan_digest,
    plan_is_blocked,
    plan_managed_service_match,
)


def _app_spec(**overrides) -> ServiceSpec:
    fields = {
        "owner_scope": APP,
        "environment": "production",
        "kind": "postgres",
        "name": "records",
        "variant": "rds",
        "isolation": "dedicated",
        "config": {"size": "small"},
        "bind_workloads": ("*",),
        "deletion_policy": "retain",
        "lifecycle_policy": {"retention": {}},
    }
    fields.update(overrides)
    return ServiceSpec(**fields)


def _project_spec(**overrides) -> ServiceSpec:
    fields = {
        "owner_scope": PROJECT,
        "environment": "production",
        "kind": "redis",
        "name": "cache",
        "variant": "elasticache",
        "isolation": "shared",
        "config": {"size": "small"},
        "bind_workloads": ("web",),
    }
    fields.update(overrides)
    return ServiceSpec(**fields)


def _recorded(spec: ServiceSpec, **overrides) -> RecordedService:
    fields = {"spec": spec, "manifest_managed": True}
    fields.update(overrides)
    return RecordedService(**fields)


def _actions(changes):
    return [(c.action, c.owner_scope, c.kind, c.name) for c in changes]


# --- app-owned services -------------------------------------------------


def test_a_declared_service_with_no_platform_row_is_created():
    changes = plan_managed_service_match([_app_spec()], [])

    assert _actions(changes) == [(CREATE, APP, "postgres", "records")]


def test_an_identical_app_service_plans_nothing():
    spec = _app_spec()

    assert plan_managed_service_match([spec], [_recorded(spec)]) == ()


def test_a_config_difference_is_a_reconfigure():
    declared = _app_spec(config={"size": "large"})
    recorded = _recorded(_app_spec())

    changes = plan_managed_service_match([declared], [recorded])

    assert _actions(changes) == [(RECONFIGURE, APP, "postgres", "records")]


def test_a_variant_change_on_a_row_with_a_backend_is_blocked_not_reconfigured():
    """persist refuses this on apply; the plan must say so before anything runs."""
    declared = _app_spec(variant="aurora_serverless_v2")
    recorded = _recorded(_app_spec(), backend_ref="postgres/records")

    changes = plan_managed_service_match([declared], [recorded])

    assert [c.action for c in changes] == [BLOCKED]
    assert plan_is_blocked(changes)


def test_a_variant_change_without_a_backend_is_a_reconfigure():
    declared = _app_spec(variant="aurora_serverless_v2")

    changes = plan_managed_service_match([declared], [_recorded(_app_spec())])

    assert [c.action for c in changes] == [RECONFIGURE]
    assert not plan_is_blocked(changes)


def test_an_unchanged_failed_row_plans_a_retry():
    spec = _app_spec()

    changes = plan_managed_service_match([spec], [_recorded(spec, needs_retry=True)])

    assert [c.action for c in changes] == [RETRY]


def test_an_imperative_row_with_the_same_name_is_blocked():
    spec = _app_spec()

    changes = plan_managed_service_match([spec], [_recorded(spec, manifest_managed=False)])

    assert [c.action for c in changes] == [BLOCKED]
    assert "imperatively" in changes[0].reason


def test_a_manifest_managed_row_the_manifest_no_longer_declares_is_removed():
    recorded = _recorded(_app_spec(name="old"))

    changes = plan_managed_service_match([], [recorded])

    assert _actions(changes) == [(REMOVE, APP, "postgres", "old")]


def test_an_imperative_row_the_manifest_does_not_declare_is_left_alone():
    changes = plan_managed_service_match([], [_recorded(_app_spec(name="mine"), manifest_managed=False)])

    assert changes == ()


# --- project-owned services ---------------------------------------------


def test_a_project_service_with_no_row_is_created_and_bound():
    changes = plan_managed_service_match([_project_spec()], [])

    assert [c.action for c in changes] == [CREATE, BIND]


def test_an_existing_shared_row_not_yet_attached_is_only_bound():
    spec = _project_spec()

    changes = plan_managed_service_match([spec], [], project_rows={(spec.kind, spec.name): spec})

    assert [c.action for c in changes] == [BIND]


def test_an_identical_attachment_plans_nothing():
    spec = _project_spec()

    changes = plan_managed_service_match(
        [spec], [_recorded(spec)], project_rows={(spec.kind, spec.name): spec}
    )

    assert changes == ()


def test_a_changed_binding_on_an_attachment_is_a_bind():
    spec = _project_spec(bind_workloads=("web", "worker"))
    recorded = _recorded(_project_spec(bind_workloads=("web",)))

    changes = plan_managed_service_match(
        [spec], [recorded], project_rows={(spec.kind, spec.name): recorded.spec}
    )

    assert [c.action for c in changes] == [BIND]


def test_a_shared_row_with_different_desired_state_is_blocked():
    declared = _project_spec(config={"size": "large"})
    shared = _project_spec()

    changes = plan_managed_service_match(
        [declared], [], project_rows={(declared.kind, declared.name): shared}
    )

    assert [c.action for c in changes] == [BLOCKED]


def test_a_project_attachment_the_manifest_drops_is_detached():
    recorded = _recorded(_project_spec(environment="staging"))

    changes = plan_managed_service_match([], [recorded])

    assert [(c.action, c.environment) for c in changes] == [(DETACH, "staging")]


# --- plan identity -------------------------------------------------------


def test_the_digest_is_stable_and_moves_with_the_plan():
    spec = _app_spec(config={"size": "large"})
    recorded = [_recorded(_app_spec())]
    first = plan_managed_service_match([spec], recorded)
    again = plan_managed_service_match([spec], recorded)
    other_spec = _app_spec(config={"size": "xl"})
    other = plan_managed_service_match([other_spec], recorded)

    assert plan_digest([spec], first) == plan_digest([spec], again)
    # Same action, different declared value: an apply must not accept it.
    assert plan_digest([spec], first) != plan_digest([other_spec], other)


def test_declared_order_is_kept_and_removals_come_last():
    first = _app_spec(name="a", config={"size": "large"})
    second = _app_spec(name="b")
    recorded = [
        _recorded(_app_spec(name="a")),
        _recorded(_app_spec(name="b")),
        _recorded(_app_spec(name="gone")),
    ]

    changes = plan_managed_service_match([first, second], recorded)

    assert [(c.action, c.name) for c in changes] == [(RECONFIGURE, "a"), (REMOVE, "gone")]
