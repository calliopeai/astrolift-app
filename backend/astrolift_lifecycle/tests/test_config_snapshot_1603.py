"""A live deploy records what it put live (#1603).

`Deployment.config_snapshot` had five writers and every one of them copied
it forward from a prior or source deployment. Nothing ever computed it, so
the field propagated its `{}` default forever.

Two live consumers read it:

* `build_config_drift` compares `snapshot["manifest_hash"]` against
  `RegisteredApp.manifest_hash`, guarded by
  `if snapshot_hash and current_hash and snapshot_hash != current_hash`.
  An empty snapshot can never satisfy that, so **the manifest-hash and
  image-tag halves of the drift banner have never fired** -- only
  `repo_unsynced` ever did.
* `direct_apply` documents depending on `config_snapshot["manifest_hash"]`.

It fails closed: the operator sees no banner rather than a wrong one, which
is exactly why it went unnoticed. An operator who edits a manifest and does
not redeploy is told nothing.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def deployment(app, env):
    """Uses the suite's own org/team/project/app/env fixtures rather than
    hand-rolling them, so this stays in step with the rest of the suite."""
    from astrolift_lifecycle.models import Deployment

    app.manifest_hash = "sha256:manifest-v2"
    app.save(update_fields=["manifest_hash"])
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        status=Deployment.Status.DEPLOYING.value,
        image_tag="v2",
    )


def test_going_live_records_the_apps_manifest_hash(deployment):
    from astrolift_workflows.activities.app_lifecycle import _write_config_snapshot

    _write_config_snapshot(deployment)
    deployment.refresh_from_db()

    assert deployment.config_snapshot["manifest_hash"] == "sha256:manifest-v2"


def test_going_live_records_the_image_tag(deployment):
    from astrolift_workflows.activities.app_lifecycle import _write_config_snapshot

    _write_config_snapshot(deployment)
    deployment.refresh_from_db()

    assert deployment.config_snapshot["image_tag"] == "v2"


def test_it_merges_rather_than_replacing(deployment):
    """A snapshot copied forward from a prior deploy keeps keys this does not
    own. Replacing wholesale would silently drop them."""
    from astrolift_workflows.activities.app_lifecycle import _write_config_snapshot

    deployment.config_snapshot = {"carried_forward": "keep me"}
    deployment.save(update_fields=["config_snapshot"])

    _write_config_snapshot(deployment)
    deployment.refresh_from_db()

    assert deployment.config_snapshot["carried_forward"] == "keep me"
    assert deployment.config_snapshot["manifest_hash"] == "sha256:manifest-v2"


def test_the_drift_banner_now_fires_when_the_manifest_changes(deployment):
    """End to end, through the real resolver.

    This is the assertion the feature exists for and the one that could not
    pass before: edit the manifest after a deploy, get a banner.
    """
    from astrolift_lifecycle.models import Deployment
    from astrolift_registry.schema.types import build_config_drift
    from astrolift_workflows.activities.app_lifecycle import _write_config_snapshot

    _write_config_snapshot(deployment)
    Deployment.objects.filter(pk=deployment.pk).update(status=Deployment.Status.RUNNING.value)

    app = deployment.registered_app
    app.manifest_hash = "sha256:manifest-v3"
    app.save(update_fields=["manifest_hash"])

    drift = build_config_drift(app)

    assert drift.has_drift is True
    assert "manifest_hash" in drift.fields


def test_no_banner_when_the_manifest_is_unchanged(deployment):
    """The other half. A drift signal that always fires is as useless as one
    that never does."""
    from astrolift_lifecycle.models import Deployment
    from astrolift_registry.schema.types import build_config_drift
    from astrolift_workflows.activities.app_lifecycle import _write_config_snapshot

    _write_config_snapshot(deployment)
    Deployment.objects.filter(pk=deployment.pk).update(status=Deployment.Status.RUNNING.value)

    drift = build_config_drift(deployment.registered_app)

    assert "manifest_hash" not in drift.fields


def test_the_rendered_manifest_hash_is_not_what_gets_stored(deployment):
    """`direct_apply` warns about this explicitly: the banner compares this
    key against `RegisteredApp.manifest_hash`, which hashes the normalised
    astrolift.toml. Storing the rendered k8s object hash here would make the
    banner fire on every app forever."""
    from astrolift_workflows.activities.app_lifecycle import _write_config_snapshot

    _write_config_snapshot(deployment)
    deployment.refresh_from_db()

    assert deployment.config_snapshot["manifest_hash"] == deployment.registered_app.manifest_hash
