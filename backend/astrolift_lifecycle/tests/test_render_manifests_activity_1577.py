"""
``render_manifests`` at the activity level (#1577).

Every other test of this module reaches for a ``_sync`` helper, so the
async half of ``render_manifests`` -- the part that decides what may
touch the ORM and what may not -- has never been executed by a test.
That gap is the whole reason #1577 survived: ``namespace_for_app(app)``
ran outside ``sync_to_async`` and lazily loaded ``app.organization``,
which Django refuses from an async context.

The activity is on the main deploy path (``deploy_app``,
``rollback_deployment``, ``promote_deployment`` all execute it
unconditionally), and ``k8s_namespace`` is blank for every app the
platform creates -- only imported agents set it -- so the FK branch is
the normal branch, not the edge case.

``ActivityEnvironment`` is Temporal's own harness for this. It supplies
the activity context that ``activity.heartbeat()`` needs, which is why
calling the decorated function bare does not work.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from temporalio.testing import ActivityEnvironment

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.activities.app_lifecycle import render_manifests

pytestmark = pytest.mark.django_db(transaction=True)


_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""


def _prepare(app, env) -> int:
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )
    return deployment.pk


async def test_render_manifests_runs_without_touching_the_orm_from_the_event_loop(app, env):
    """The regression guard for #1577.

    Before the fix this raised ``SynchronousOnlyOperation`` rather than
    returning, so asserting on the resources is enough -- getting a
    result at all is the assertion.
    """
    assert not app.k8s_namespace, "fixture must exercise the organization-FK branch"
    deployment_id = await sync_to_async(_prepare)(app, env)

    result = await ActivityEnvironment().run(render_manifests, deployment_id)

    assert result["resources"], "render produced no resources"


async def test_render_manifests_namespaces_resources_by_org_and_app_slug(app, env):
    """The FK actually resolved.

    Without this the fix could 'pass' by swallowing the lookup and
    falling back to a blank or app-only namespace, which would apply
    the deploy into the wrong place -- a worse failure than the crash.
    """
    deployment_id = await sync_to_async(_prepare)(app, env)

    result = await ActivityEnvironment().run(render_manifests, deployment_id)

    namespaces = {r["metadata"].get("namespace") for r in result["resources"]}
    assert namespaces == {"acme-test-hello-app"}
