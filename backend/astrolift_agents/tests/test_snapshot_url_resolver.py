"""Schema tests for the VNC theatre read surface (gallery + snapshot_url).

Covers the ``snapshot_url`` field on ``AstroliftAgentTask`` and the
``agent_gallery`` query that drives the snapshot-tile gallery:

snapshot_url (minted via the LocalFs blob store, real Postgres tasks):
  * RUNNING vnc task with a frame written  -> presigned GET URL
  * RUNNING vnc task, no frame uploaded yet -> None (NotFound swallowed)
  * non-vnc task                            -> None (gated before blob call)
  * RUNNING vnc task, no blob store config   -> None (NotConfigured swallowed)
  * non-RUNNING vnc task                     -> None (gated before blob call)

agent_gallery:
  * returns only RUNNING, vnc_enabled tasks that have a published vnc_url
  * is tenant-scoped (a foreign org's running vnc task never appears)
  * rejects a foreign org_id argument (organization mismatch)
  * is gated on agent_task.watch: a member without the grant is denied,
    a member with a RoleBinding granting it is allowed
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_agents.models import AgentTask
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.schema.types import agent_task_to_type
from astrolift_identity.models import Organization, Role, RoleBinding
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    # Org creation indexes a profile document; stub the OpenSearch round-trip.
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
    return Organization.objects.create(name="Theatre Org", slug="theatre-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Rival Org", slug="rival-org-theatre")


@pytest.fixture
def superuser():
    return get_user_model().objects.create_superuser(
        username="theatre-admin",
        email="admin@theatre.test",
        password="x",
    )


@pytest.fixture
def local_blob(tmp_path, monkeypatch):
    """Point the snapshot store at a temp dir via the local-fs fallback."""
    monkeypatch.delenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", raising=False)
    monkeypatch.setenv("PIPELINE_ARTIFACT_LOCAL_PATH", str(tmp_path))
    return tmp_path


def _info(user):
    from types import SimpleNamespace

    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user, auth=None)))


def _ctx(user, org):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


def _running_vnc_task(org, *, snapshot_key="", vnc_url="/app/vnc/x"):
    """A task in the shape a live VNC run has: RUNNING + vnc_enabled +
    a published relay path. ``snapshot_key`` is set by the spawn-time
    injector for VNC tasks; left blank here unless the test needs it."""
    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.RUNNING,
        vnc_enabled=True,
        vnc_url=vnc_url,
    )
    if snapshot_key:
        task.snapshot_key = snapshot_key
        task.save(update_fields=["snapshot_key"])
    return task


def _write_frame(blob_root, task):
    """Write a fake latest.jpg at the task's snapshot key so the GET presign
    (which existence-checks for LocalFs) resolves instead of raising."""
    from astrolift_agents.snapshot_store import snapshot_blob_key

    key = snapshot_blob_key(str(task.guid))
    path = blob_root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xe0jpegbytes")


# ---------------------------------------------------------------------------
# snapshot_url field on AgentTaskType
# ---------------------------------------------------------------------------


def test_snapshot_url_present_for_running_vnc_task_with_frame(org, local_blob):
    from astrolift_agents.snapshot_store import snapshot_blob_key

    task = _running_vnc_task(org)
    # Freeze the real key on the task, then write a frame at it.
    task.snapshot_key = snapshot_blob_key(str(task.guid))
    task.save(update_fields=["snapshot_key"])
    _write_frame(local_blob, task)

    out = agent_task_to_type(task)
    assert out.snapshot_url is not None
    # LocalFs returns a file:// URL pointing at the exact snapshot key.
    assert out.snapshot_url.startswith("file://")
    assert out.snapshot_url.endswith(f"snapshots/{task.guid}/latest.jpg")


def test_snapshot_url_none_when_no_frame_uploaded_yet(org, local_blob):
    # Key frozen, but the uploader hasn't PUT a frame -> GET presign raises
    # BlobStoreNotFoundError, which the resolver swallows to None.
    from astrolift_agents.snapshot_store import snapshot_blob_key

    task = _running_vnc_task(org)
    task.snapshot_key = snapshot_blob_key(str(task.guid))
    task.save(update_fields=["snapshot_key"])

    out = agent_task_to_type(task)
    assert out.snapshot_url is None


def test_snapshot_url_none_for_non_vnc_task(org, local_blob):
    # Even with a blob store configured, a non-vnc task is gated out before
    # any blob call (no snapshot_key, vnc_enabled False).
    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.RUNNING,
        vnc_enabled=False,
    )
    out = agent_task_to_type(task)
    assert out.snapshot_url is None


def test_snapshot_url_none_for_non_running_vnc_task(org, local_blob):
    # A vnc task with a key but not RUNNING (e.g. still PROVISIONING) has no
    # live framebuffer; snapshot_url must be None regardless of the key.
    from astrolift_agents.snapshot_store import snapshot_blob_key

    task = AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.PROVISIONING,
        vnc_enabled=True,
    )
    task.snapshot_key = snapshot_blob_key(str(task.guid))
    task.save(update_fields=["snapshot_key"])
    _write_frame(local_blob, task)  # frame exists, but task isn't RUNNING

    out = agent_task_to_type(task)
    assert out.snapshot_url is None


def test_snapshot_url_none_when_no_blob_store_configured(org, monkeypatch):
    # RUNNING vnc task with a key but the install has no blob store ->
    # BlobStoreNotConfiguredError, swallowed to None.
    monkeypatch.delenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", raising=False)
    monkeypatch.delenv("PIPELINE_ARTIFACT_LOCAL_PATH", raising=False)
    task = _running_vnc_task(org, snapshot_key="snapshots/g/latest.jpg")

    out = agent_task_to_type(task)
    assert out.snapshot_url is None


# ---------------------------------------------------------------------------
# agent_gallery query — filtering + tenancy
# ---------------------------------------------------------------------------


def test_gallery_returns_only_running_vnc_tasks_with_relay(superuser, org, local_blob):
    watchable = _running_vnc_task(org, vnc_url="/app/vnc/watch")
    # A RUNNING vnc task that has NOT yet published a relay path is excluded.
    no_relay = _running_vnc_task(org, vnc_url="")
    # A RUNNING non-vnc task is excluded.
    AgentTask.objects.create(organization=org, status=AgentTask.Status.RUNNING, vnc_enabled=False)
    # A vnc task that is not RUNNING is excluded.
    AgentTask.objects.create(
        organization=org,
        status=AgentTask.Status.COMPLETED,
        vnc_enabled=True,
        vnc_url="/app/vnc/done",
    )

    query = AgentsQuery()
    with _ctx(superuser, org):
        rows = query.agent_gallery(_info(superuser), org_id=str(org.guid))

    ids = {r.id for r in rows}
    assert str(watchable.guid) in ids
    assert str(no_relay.guid) not in ids
    assert len(ids) == 1


def test_gallery_is_tenant_scoped(superuser, org, other_org, local_blob):
    mine = _running_vnc_task(org, vnc_url="/app/vnc/mine")
    # Identical shape, but owned by another org — must never appear.
    _running_vnc_task(other_org, vnc_url="/app/vnc/theirs")

    query = AgentsQuery()
    with _ctx(superuser, org):
        rows = query.agent_gallery(_info(superuser), org_id=str(org.guid))

    ids = {r.id for r in rows}
    assert ids == {str(mine.guid)}


def test_gallery_rejects_foreign_org_id(superuser, org, other_org, local_blob):
    # A non-superuser passing another org's guid is rejected; superuser
    # bypasses the match, so use a plain member here.
    member = get_user_model().objects.create(username="member@theatre.test", email="member@theatre.test")
    _grant_watch(member, org)

    from graphql import GraphQLError

    query = AgentsQuery()
    with _ctx(member, org):
        with pytest.raises(GraphQLError):
            query.agent_gallery(_info(member), org_id=str(other_org.guid))


def test_gallery_carries_vnc_url_for_the_theatre(superuser, org, local_blob):
    _running_vnc_task(org, vnc_url="/app/vnc/relay-path")
    query = AgentsQuery()
    with _ctx(superuser, org):
        rows = query.agent_gallery(_info(superuser), org_id=str(org.guid))
    assert rows[0].vnc_url == "/app/vnc/relay-path"


# ---------------------------------------------------------------------------
# agent_gallery query — permission gate (agent_task.watch)
# ---------------------------------------------------------------------------


def _grant_watch(user, org):
    role = Role.objects.create(
        name="agent-watcher",
        slug=f"agent-watcher-{org.slug}",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.AGENT_TASK_WATCH.value, Permission.APP_READ.value],
        is_system=True,
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


def test_gallery_denies_member_without_watch_permission(org, local_blob):
    # A member with no agent_task.watch binding cannot list the theatre,
    # even though the rows are in their own org.
    member = get_user_model().objects.create(username="nowatch@theatre.test", email="nowatch@theatre.test")
    _running_vnc_task(org, vnc_url="/app/vnc/x")

    query = AgentsQuery()
    with _ctx(member, org):
        with pytest.raises(PermissionDenied):
            query.agent_gallery(_info(member), org_id=str(org.guid))


def test_gallery_allows_member_with_watch_permission(org, local_blob):
    member = get_user_model().objects.create(username="watch@theatre.test", email="watch@theatre.test")
    _grant_watch(member, org)
    task = _running_vnc_task(org, vnc_url="/app/vnc/granted")

    query = AgentsQuery()
    with _ctx(member, org):
        rows = query.agent_gallery(_info(member), org_id=str(org.guid))
    assert {r.id for r in rows} == {str(task.guid)}
