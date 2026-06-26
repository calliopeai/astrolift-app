"""Import smoke test for the Temporal worker registration module.

A prior deploy crash-looped because ``worker.py`` listed an activity in
the ``ACTIVITIES`` tuple that was never imported -- a ``NameError`` at
module load that only surfaced when the worker process started in prod.

These tests import ``astrolift_workflows.worker`` and assert that every
name in ``WORKFLOWS`` / ``ACTIVITIES`` resolves to a real Temporal
definition, so this class of break fails in CI instead of at runtime.
"""

from __future__ import annotations


def test_worker_module_imports():
    """Importing the worker must not raise (NameError on an undefined
    activity/workflow name in a tuple is exactly the regression we guard)."""
    import astrolift_workflows.worker as worker

    assert worker.WORKFLOWS
    assert worker.ACTIVITIES


def test_every_activity_is_a_temporal_activity():
    """Each entry in ACTIVITIES must be a real @activity.defn callable.

    A bare name that happens to resolve to a non-activity object would
    pass the import but fail at Worker construction; assert the decorator
    marker is present so the tuple can only hold real activities."""
    import astrolift_workflows.worker as worker

    for fn in worker.ACTIVITIES:
        assert (
            getattr(fn, "__temporal_activity_definition", None) is not None
        ), f"{getattr(fn, '__name__', fn)!r} in ACTIVITIES is not an @activity.defn"


def test_every_workflow_is_a_temporal_workflow():
    """Each entry in WORKFLOWS must be a real @workflow.defn class."""
    import astrolift_workflows.worker as worker

    for cls in worker.WORKFLOWS:
        assert (
            getattr(cls, "__temporal_workflow_definition", None) is not None
        ), f"{getattr(cls, '__name__', cls)!r} in WORKFLOWS is not a @workflow.defn"


def test_activity_names_are_unique():
    """Duplicate registrations would make Worker construction reject the
    set; catch a copy-paste dup in the tuple here instead."""
    import astrolift_workflows.worker as worker

    names = [
        fn.__temporal_activity_definition.name
        for fn in worker.ACTIVITIES
        if getattr(fn, "__temporal_activity_definition", None) is not None
    ]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate activity registrations: {dupes}"


def test_static_site_activities_are_registered():
    """#1010 (P0b): the four static-site activities must be registered on the
    worker, or the deploy/teardown workflows fail at runtime with an
    'activity not registered' error the moment a static_site app deploys."""
    import astrolift_workflows.worker as worker

    registered = {
        fn.__temporal_activity_definition.name
        for fn in worker.ACTIVITIES
        if getattr(fn, "__temporal_activity_definition", None) is not None
    }
    for name in (
        "astrolift.deploy.ensure_static_site_services",
        "astrolift.deploy.sync_static_assets",
        "astrolift.deploy.ensure_static_dns",
        "astrolift.deploy.delete_static_dns_records",
    ):
        assert name in registered, f"{name!r} is not registered in worker.ACTIVITIES"
