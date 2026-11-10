"""Real persistence retains selected isolation and the exact external policy."""

from __future__ import annotations

import pytest

from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_manifest.render import render_manifests

from .test_nitro_sandboxes_2668 import source, template
from .test_persist import _scaffold

pytestmark = pytest.mark.django_db


def test_persisted_sandbox_survives_reload_update_and_removal():
    app = _scaffold()
    policy = "  external-policy==  "
    confidential = source(f'nitro_sandbox = {{mode = "confidential", policy = "{policy}"}}')
    persist_manifest(app, normalize(parse_raw(confidential)), raw_text=confidential)
    app.refresh_from_db()
    first_hash = app.manifest_hash
    assert app.manifest_raw == confidential
    assert app.manifest_normalized["workloads"][0]["nitro_sandbox"]["policy"] == policy
    resources = render_manifests(
        normalize(parse_raw(app.manifest_raw)),
        namespace="team",
        image_tag="test",
        image_repository="worker",
        environment_name="test",
    )
    assert template(resources)["spec"]["runtimeClassName"] == "confidential-sandbox"
    assert template(resources)["metadata"]["annotations"]["sandbox.amazonaws.com/policy"] == policy
    isolated = source('nitro_sandbox = {mode = "isolated"}')
    assert persist_manifest(app, normalize(parse_raw(isolated)), raw_text=isolated).hash_changed
    app.refresh_from_db()
    assert app.manifest_hash != first_hash
    assert app.manifest_normalized["workloads"][0]["nitro_sandbox"]["mode"] == "isolated"
    ordinary = source()
    assert persist_manifest(app, normalize(parse_raw(ordinary)), raw_text=ordinary).hash_changed
    app.refresh_from_db()
    assert "nitro_sandbox" not in app.manifest_normalized["workloads"][0]


def test_task_agent_dispatch_reads_its_persisted_selector(settings):
    from astrolift_dispatch.nitro_sandbox import selected_task_runtime
    from astrolift_dispatch.spawners.k8s_job import _render_agent_job
    from astrolift_dispatch.tests.test_k8s_job_vnc import _FakeTask
    from astrolift_registry.models import Workload

    settings.AGENT_RUNTIME_CLASS = ""
    app = _scaffold()
    text = source('nitro_sandbox = {mode = "isolated"}', kind="agent")
    persist_manifest(app, normalize(parse_raw(text)), raw_text=text)
    row = Workload.objects.select_related("registered_app").get(registered_app=app, slug="worker")
    runtime = selected_task_runtime(row)
    assert runtime == "isolated-sandbox"
    job = _render_agent_job(
        job_name="agent-task-persisted",
        workload=row,
        namespace="team",
        task=_FakeTask(guid="task-persisted", vnc_enabled=False),
        runtime_class=runtime,
    )
    assert job["spec"]["template"]["spec"]["runtimeClassName"] == "isolated-sandbox"
