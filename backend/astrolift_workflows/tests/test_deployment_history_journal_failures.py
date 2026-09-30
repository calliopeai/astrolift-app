"""Real PostgreSQL journal failures preserve provider errors without success."""

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection

from astrolift_lifecycle.models import DeploymentLog
from astrolift_lifecycle.run_history import observed_phase
from astrolift_workflows.activities.build_image import BuildImageInput, _build_image_sync, _PreparedBuild
from astrolift_workflows.tests.test_build_image import _make_deployment

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def reject_observation(event):
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE FUNCTION pg_temp.reject_history_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.event = TG_ARGV[0] THEN RAISE EXCEPTION 'history unavailable'; END IF; RETURN NEW; END; $$"
        )
        cursor.execute(
            "CREATE TRIGGER reject_history_event BEFORE INSERT ON astrolift_lifecycle_deploymentlog FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_history_event(%s)",
            [event],
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER reject_history_event ON astrolift_lifecycle_deploymentlog")


def test_failed_phase_journal_does_not_replace_original_error(caplog):
    deployment = _make_deployment(build_strategy="dockerfile")
    original = RuntimeError("apiserver rejected actual apply secret-provider-token")

    @observed_phase("apply")
    def apply(deployment_id):
        raise original

    with reject_observation("failed"), pytest.raises(RuntimeError) as result:
        apply(deployment.pk)
    assert result.value is original
    assert list(DeploymentLog.objects.filter(deployment=deployment).values_list("event", flat=True)) == [
        "started"
    ]
    assert "Could not journal failed deployment activity" in caplog.text
    assert "secret-provider-token" not in caplog.text


def test_failed_completion_journal_never_returns_success():
    deployment = _make_deployment(build_strategy="dockerfile")

    @observed_phase("apply")
    def apply(deployment_id):
        return True

    with reject_observation("completed"), pytest.raises(DatabaseError, match="history unavailable"):
        apply(deployment.pk)
    assert not DeploymentLog.objects.filter(deployment=deployment, event="completed").exists()


@pytest.mark.parametrize("outcome", ["exception", "failure-result", "completion-journal"])
def test_build_journal_preserves_real_driver_failure_and_never_invents_success(monkeypatch, outcome):
    module = importlib.import_module("astrolift_workflows.activities.build_image")
    deployment = _make_deployment(build_strategy="dockerfile")
    original = RuntimeError("actual registry connection refused")

    class Driver:
        def build(self, *args):
            if outcome == "exception":
                raise original
            return SimpleNamespace(
                success=outcome == "completion-journal",
                errors=["actual kaniko failure"],
                digest="sha256:real",
                image_uri="registry/app:v1",
            )

    monkeypatch.setattr(
        module,
        "_prepare_build",
        lambda *args: _PreparedBuild(
            driver=Driver(), registry_driver=object(), repo_name="app", repo_uri="registry/app"
        ),
    )
    event = "completed" if outcome == "completion-journal" else "failed"
    expected = DatabaseError if outcome == "completion-journal" else RuntimeError
    match = (
        "history unavailable"
        if outcome == "completion-journal"
        else "actual registry connection refused"
        if outcome == "exception"
        else "actual kaniko failure"
    )
    with reject_observation(event), pytest.raises(expected, match=match) as result:
        _build_image_sync(BuildImageInput(deployment.pk, "run-image", ""))
    if outcome == "exception":
        assert result.value.__cause__ is original
    assert not DeploymentLog.objects.filter(deployment=deployment, event="completed").exists()
    assert not DeploymentLog.objects.filter(deployment=deployment, phase="push").exists()
