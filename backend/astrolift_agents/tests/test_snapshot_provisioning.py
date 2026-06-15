"""Tests for per-task VNC snapshot URL provisioning at spawn.

Covers the snapshot key scheme + presigned PUT/GET (LocalFs driver, no DB)
and the spawn-time injector against a real AgentTask (real Postgres):

  * VNC task  -> ASTROLIFT_SNAPSHOT_URL + ASTROLIFT_SNAPSHOT_INTERVAL injected,
                 a presigned PUT URL minted, snapshot_key frozen on the task
  * non-VNC   -> no snapshot env, no snapshot_key
  * no blob store configured -> injection is a no-op (run still starts)
"""

from __future__ import annotations

import pytest

from astrolift_agents import snapshot_store
from astrolift_agents.models import AgentTask
from astrolift_dispatch.snapshot_injector import (
    DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
    inject_snapshot_into_job_spec,
    snapshot_env_vars,
)
from astrolift_identity.models import Organization
from providers._sdk.blob_store import BlobStoreNotConfiguredError


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="SnapOrg", slug="snap-org-agents")


@pytest.fixture
def local_blob(tmp_path, monkeypatch):
    """Point the snapshot store at a temp dir via the local-fs fallback."""
    monkeypatch.delenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", raising=False)
    monkeypatch.setenv("PIPELINE_ARTIFACT_LOCAL_PATH", str(tmp_path))
    return tmp_path


# ---- key scheme + presigned URLs (no DB) ----------------------------


def test_snapshot_blob_key_scheme():
    assert snapshot_store.snapshot_blob_key("abc-123") == "snapshots/abc-123/latest.jpg"


def test_presigned_upload_url_minted_for_local_driver(local_blob):
    url = snapshot_store.presigned_snapshot_upload_url(org=object(), task_guid="g-1")
    # LocalFs returns a file:// URL pointing at the snapshot key target.
    assert url.startswith("file://")
    assert url.endswith("snapshots/g-1/latest.jpg")


def test_presigned_upload_url_does_not_require_existing_object(local_blob):
    # PUT presign is a write target — must not 404 just because nothing has
    # been uploaded yet (unlike the GET presign).
    url = snapshot_store.presigned_snapshot_upload_url(org=object(), task_guid="never-written")
    assert "never-written" in url


def test_no_blob_store_raises_not_configured(monkeypatch):
    monkeypatch.delenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", raising=False)
    monkeypatch.delenv("PIPELINE_ARTIFACT_LOCAL_PATH", raising=False)
    with pytest.raises(BlobStoreNotConfiguredError):
        snapshot_store.presigned_snapshot_upload_url(org=object(), task_guid="g-2")


def test_snapshot_local_path_overrides_artifact_path(tmp_path, monkeypatch):
    # A dedicated snapshot path takes precedence over the artifact path.
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    monkeypatch.setenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", str(snap_dir))
    monkeypatch.setenv("PIPELINE_ARTIFACT_LOCAL_PATH", str(tmp_path / "artifacts"))
    url = snapshot_store.presigned_snapshot_upload_url(org=object(), task_guid="g-3")
    assert str(snap_dir) in url


# ---- injector against a real AgentTask (real Postgres) --------------


@pytest.mark.django_db(transaction=True)
def test_vnc_task_gets_snapshot_env_and_key(org, local_blob):
    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.DRAFT,
        vnc_enabled=True,
    )
    env = snapshot_env_vars(task)
    names = {e["name"]: e["value"] for e in env}

    assert "ASTROLIFT_SNAPSHOT_URL" in names
    assert names["ASTROLIFT_SNAPSHOT_URL"].endswith(f"snapshots/{task.guid}/latest.jpg")
    assert names["ASTROLIFT_SNAPSHOT_INTERVAL"] == str(DEFAULT_SNAPSHOT_INTERVAL_SECONDS)

    # The key is frozen on the task so the gallery can mint a GET later.
    task.refresh_from_db()
    assert task.snapshot_key == f"snapshots/{task.guid}/latest.jpg"


@pytest.mark.django_db(transaction=True)
def test_non_vnc_task_gets_no_snapshot_env_or_key(org, local_blob):
    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.DRAFT,
        vnc_enabled=False,
    )
    assert snapshot_env_vars(task) == []
    task.refresh_from_db()
    assert task.snapshot_key == ""


@pytest.mark.django_db(transaction=True)
def test_inject_appends_snapshot_env_to_primary_container(org, local_blob):
    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.DRAFT,
        vnc_enabled=True,
    )
    job = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "agent", "image": "img", "env": [{"name": "X", "value": "1"}]}]
                }
            }
        }
    }
    out = inject_snapshot_into_job_spec(job, task)
    env = out["spec"]["template"]["spec"]["containers"][0]["env"]
    names = {e["name"] for e in env}
    # Pre-existing env preserved; snapshot vars appended.
    assert "X" in names
    assert "ASTROLIFT_SNAPSHOT_URL" in names
    assert "ASTROLIFT_SNAPSHOT_INTERVAL" in names


@pytest.mark.django_db(transaction=True)
def test_inject_is_noop_for_non_vnc(org, local_blob):
    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.DRAFT,
        vnc_enabled=False,
    )
    job = {"spec": {"template": {"spec": {"containers": [{"name": "agent", "env": []}]}}}}
    out = inject_snapshot_into_job_spec(job, task)
    assert out["spec"]["template"]["spec"]["containers"][0]["env"] == []


@pytest.mark.django_db(transaction=True)
def test_inject_noop_when_no_blob_store_configured(org, monkeypatch):
    # VNC task but no blob store -> skip injection (uploader no-ops), no crash.
    monkeypatch.delenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", raising=False)
    monkeypatch.delenv("PIPELINE_ARTIFACT_LOCAL_PATH", raising=False)
    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.DRAFT,
        vnc_enabled=True,
    )
    assert snapshot_env_vars(task) == []
    task.refresh_from_db()
    assert task.snapshot_key == ""
