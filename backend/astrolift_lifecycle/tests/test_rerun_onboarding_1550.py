"""The app doctor's fourth `fix` verb finally has a mutation (#1550).

`rerun_onboarding` was emitted by the doctor's registry check -- the one
that fires when an app has no `registry_repo_uri`, meaning provisioning
never completed and the app can never build -- and nothing implemented
it. The panel maps each verb to a mutation, so that check offered a
button with nothing behind it.

The interesting half of this is the refusal. The shared `start_workflow`
submits under `WorkflowIDReusePolicy.TERMINATE_IF_RUNNING`, so reusing
the onboarding workflow id does not join or dedupe an in-flight run, it
kills it and starts over. Two call sites describe that id as a guard
that makes a concurrent click safe. It is not, and the impatient second
click on a provisioning app is the one that costs the most, so this
mutation checks for a RUNNING instance and declines.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.schema.mutations import LifecycleMutation
from astrolift_lifecycle.schema.mutations.types import RerunOnboardingInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

MODULE = "astrolift_workflows.client"


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _err_code(result) -> str:
    code = result.errors[0].code
    return getattr(code, "value", code)


@pytest.fixture
def granted(permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_CREATE)
    return permission_resolver


@pytest.fixture
def temporal(monkeypatch):
    """Record starts and control what `describe` reports.

    Patched on `astrolift_workflows.client` because the resolver imports
    from there at call time, so patching the resolver's module namespace
    would miss.
    """
    import astrolift_workflows.client as client

    state = {"status": None, "started": [], "enqueued": True, "raise_on_start": None}

    def _describe(workflow_id):
        if state["status"] is None:
            return None
        return {"workflow_id": workflow_id, "status": state["status"]}

    def _start(name, args, *, workflow_id, task_queue=None):
        if state["raise_on_start"] is not None:
            raise state["raise_on_start"]
        state["started"].append((name, workflow_id, args))
        return client.WorkflowHandle(
            workflow_id=workflow_id,
            run_id="run-1",
            enqueued=state["enqueued"],
        )

    monkeypatch.setattr(client, "describe_workflow_instance", _describe)
    monkeypatch.setattr(client, "start_workflow", _start)
    return state


def _rerun(app, org, actor, fake_info):
    with _tenant_for(org, actor):
        return LifecycleMutation().rerun_astrolift_onboarding(
            fake_info,
            input=RerunOnboardingInput(app_slug=app.slug),
        )


def test_it_starts_onboarding_under_the_apps_workflow_id(app, org, actor, fake_info, granted, temporal):
    result = _rerun(app, org, actor, fake_info)

    assert result.ok, result.errors
    assert result.data.started is True
    assert result.data.already_running is False
    assert result.data.workflow_id == f"OnboardAppWorkflow-{app.guid}"

    (name, workflow_id, args) = temporal["started"][0]
    assert name == "OnboardAppWorkflow"
    assert workflow_id == f"OnboardAppWorkflow-{app.guid}"
    # The only field the workflow actually reads, checked against the
    # workflow body rather than the input dataclass's docstring.
    assert args[0].registered_app_id == app.pk


def test_a_running_onboarding_is_declined_not_terminated(app, org, actor, fake_info, granted, temporal):
    """The point of the whole mutation. TERMINATE_IF_RUNNING means a
    second start is destructive, so nothing may be submitted here."""
    temporal["status"] = "RUNNING"

    result = _rerun(app, org, actor, fake_info)

    assert result.ok, result.errors
    assert result.data.started is False
    assert result.data.already_running is True
    assert "already running" in result.data.detail
    assert temporal["started"] == [], "a second start would have terminated the live run"


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "TERMINATED", "CANCELED", "TIMED_OUT"])
def test_every_closed_status_may_be_restarted(app, org, actor, fake_info, granted, temporal, status):
    """Only RUNNING blocks. A failed onboarding is the single most
    likely reason an operator is on this button at all, so a check that
    read "an instance exists" rather than "an instance is running" would
    refuse exactly the case it exists to repair."""
    temporal["status"] = status

    result = _rerun(app, org, actor, fake_info)

    assert result.data.started is True
    assert len(temporal["started"]) == 1


def test_a_failing_describe_starts_rather_than_refusing(app, org, actor, fake_info, granted, temporal):
    """`describe_workflow_instance` swallows its own errors and returns
    None, so a transient Temporal blip is indistinguishable from "never
    ran". Refusing on that would make the repair path flaky in exactly
    the conditions it is needed."""
    temporal["status"] = None

    result = _rerun(app, org, actor, fake_info)

    assert result.data.started is True


def test_temporal_disabled_is_reported_as_not_started_rather_than_ok(
    app, org, actor, fake_info, granted, temporal
):
    """`start_workflow` returns an un-enqueued handle when Temporal is
    off. Reporting that as started is how an operator waits forever for
    a run that was never submitted."""
    temporal["enqueued"] = False

    result = _rerun(app, org, actor, fake_info)

    assert result.ok, result.errors
    assert result.data.started is False
    assert result.data.already_running is False
    assert "disabled" in result.data.detail


def test_a_start_failure_is_an_error_envelope_not_a_raise(app, org, actor, fake_info, granted, temporal):
    temporal["raise_on_start"] = RuntimeError("temporal unreachable")

    result = _rerun(app, org, actor, fake_info)

    assert not result.ok
    assert _err_code(result) == "INTERNAL"
    assert "temporal unreachable" in result.errors[0].message


def test_read_only_actor_is_denied(app, org, actor, fake_info, permission_resolver, temporal):
    permission_resolver.grant(Permission.APP_READ)

    result = _rerun(app, org, actor, fake_info)

    assert not result.ok
    assert _err_code(result) == "PERMISSION_DENIED"
    assert temporal["started"] == []


def test_an_app_in_another_org_is_not_found(app, org, actor, fake_info, granted, temporal):
    """Slugs are unique per org only, and this mutation provisions cloud
    resources against whatever it resolves."""
    from astrolift_identity.models import Organization

    other = Organization.objects.create(name="Other", slug="other-org")

    with tenant_context(TenantContext(organization_id=other.id, actor_user_id=actor.id)):
        result = LifecycleMutation().rerun_astrolift_onboarding(
            fake_info,
            input=RerunOnboardingInput(app_slug=app.slug),
        )

    assert not result.ok
    assert _err_code(result) == "NOT_FOUND"
    assert temporal["started"] == []


def test_no_tenant_fails_closed(app, actor, fake_info, granted, temporal):
    result = LifecycleMutation().rerun_astrolift_onboarding(
        fake_info,
        input=RerunOnboardingInput(app_slug=app.slug),
    )

    assert not result.ok
    assert temporal["started"] == []


def test_every_doctor_fix_verb_now_has_a_mutation():
    """The gap this issue closed, pinned so it cannot reopen.

    Three of the doctor's four verbs had a mutation and `rerun_onboarding`
    did not. Adding a fifth verb without a mutation is the same defect,
    and nothing else in the repo would notice.
    """
    from config.schema import schema

    fields = {f.name for f in schema.mutation.__strawberry_definition__.fields}
    for parent in schema.mutation.__strawberry_definition__.fields:
        inner = getattr(parent.type, "__strawberry_definition__", None)
        if inner is not None:
            fields |= {f.name for f in inner.fields}

    for verb, mutation in {
        "resync_manifest": "resyncAstroliftManifestFromRepo",
        "retry_autowire": "retryAstroliftAutowire",
        "rerun_onboarding": "rerunAstroliftOnboarding",
        "redeploy": "forceAstroliftRedeploy",
    }.items():
        camel = mutation[0].lower() + mutation[1:]
        snake = "".join("_" + c.lower() if c.isupper() else c for c in mutation)
        assert camel in fields or snake in fields, (
            f"the doctor emits fix={verb!r} and no mutation named {mutation} exists, "
            "so the panel renders a button that does nothing"
        )
