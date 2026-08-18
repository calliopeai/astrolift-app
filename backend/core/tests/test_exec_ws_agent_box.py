"""The exec relay reaching an agent box (#129).

Before this, the relay resolved the first path segment against
``RegisteredApp`` only. A box is not one, so a healthy running box closed
with the same code a real permission denial uses and the operator was
told to go ask for a grant they already held.

Two tiers, mirroring ``test_vnc_ws.py``:

* async dispatcher tests that patch the resolution helpers, covering
  which grant each target kind demands, which close code each miss
  produces, and — the load-bearing one — that every rejection still
  happens before ``websocket.accept``, because the CLI reads the HTTP
  status of the rejected handshake, not the WS close code;
* ``@pytest.mark.django_db`` tests that call the SYNC bodies of the
  ``sync_to_async`` helpers via ``.func`` against real rows, so the
  tenant boundary and the real permission resolver are genuinely
  exercised rather than patched away.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.schema.exec_ws import (
    ExecBackend,
    ExecSession,
    _check_box_attach_permission,
    _check_box_in_tenant,
    _record_box_attach,
    get_exec_backend,
    set_exec_backend,
)

# ---- shared scaffolding ---------------------------------------------

BOX_PATH = "/app/exec/box-claude-dev-u7/agent-box-abc123-x9k2p"


@pytest.fixture(autouse=True)
def _restore_exec_backend():
    """The exec backend is a process global, and most tests here swap it.
    Left installed, the recording stub is what a later module's
    'the backend is the stub or the real driver' assertion sees."""
    previous = get_exec_backend()
    yield
    set_exec_backend(previous)


class _RecordingBackend(ExecBackend):
    def __init__(self) -> None:
        self.opens: list[dict] = []

    async def open(self, **kw: Any) -> ExecSession:
        self.opens.append({"app_slug": kw["app_slug"], "workload_slug": kw["workload_slug"]})

        class _Sess(ExecSession):
            async def stdin(self, data: str) -> None:
                return None

            async def close(self) -> None:
                return None

        return _Sess()


def _patch_relay(
    monkeypatch,
    *,
    is_app: bool,
    is_box: bool,
    exec_pod: bool,
    box_attach: bool,
    box_records: list | None = None,
    app_audits: list | None = None,
) -> None:
    """Authenticate the handshake and pin what the slug resolves to.

    Every knob is independent on purpose: the point of several of these
    tests is that holding the *other* kind's grant does not help.
    """
    import core.schema.ws_auth as ws_auth_mod
    from core.schema import exec_ws as mod

    async def _user(_session_key):
        class _U:
            is_authenticated = True

        return _U(), {}

    async def _tenant(_user, _data):
        class _T:
            organization_id = 1
            actor_user_id = 99

        return _T()

    async def _app_in_tenant(*, app_slug, tenant_org_id):
        return is_app

    async def _box_in_tenant(*, box_slug, tenant_org_id):
        return is_box

    async def _exec_perm(*, tenant_org_id, actor_user_id):
        return exec_pod

    async def _attach_perm(*, tenant_org_id, actor_user_id):
        return box_attach

    async def _record(**kw):
        if box_records is not None:
            box_records.append(kw)

    async def _audit(**kw):
        if app_audits is not None:
            app_audits.append(kw)

    monkeypatch.setattr(ws_auth_mod, "_resolve_user_from_sessionid", _user)
    monkeypatch.setattr(ws_auth_mod, "_resolve_tenant_for_user", _tenant)
    monkeypatch.setattr(mod, "_check_app_in_tenant", _app_in_tenant)
    monkeypatch.setattr(mod, "_check_box_in_tenant", _box_in_tenant)
    monkeypatch.setattr(mod, "_check_exec_permission", _exec_perm)
    monkeypatch.setattr(mod, "_check_box_attach_permission", _attach_perm)
    monkeypatch.setattr(mod, "_record_box_attach", _record)
    monkeypatch.setattr(mod, "_audit_exec_open", _audit)


async def _run(path: str, frames: list[dict]) -> list[dict]:
    from core.schema import exec_ws as mod

    incoming = [{"type": "websocket.receive", "text": json.dumps(f)} for f in frames]
    sent: list[dict] = []

    async def receive() -> dict:
        if incoming:
            return incoming.pop(0)
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": path,
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.exec_ws_application(scope, receive, send)
    return sent


_OPEN_FRAME = {"type": "open", "container": "main", "command": ["tmux", "new-session", "-A"]}


# ---- dispatcher: target kind picks the grant ------------------------


@pytest.mark.asyncio
async def test_box_slug_reaches_the_backend(monkeypatch) -> None:
    """The bug, inverted: a box slug now opens a session.

    ``exec_pod=False`` is deliberate — it proves the box got in on
    ``agent_box.attach`` and not because the app gate was widened.
    """
    backend = _RecordingBackend()
    set_exec_backend(backend)
    _patch_relay(monkeypatch, is_app=False, is_box=True, exec_pod=False, box_attach=True)

    sent = await _run(BOX_PATH, [_OPEN_FRAME, {"type": "close"}])

    assert backend.opens == [{"app_slug": "box-claude-dev-u7", "workload_slug": "agent-box-abc123-x9k2p"}]
    assert {"type": "websocket.accept"} in sent


@pytest.mark.asyncio
async def test_box_without_the_attach_grant_is_denied(monkeypatch) -> None:
    """A resolvable box the caller may not attach to closes 4403 —
    holding ``app.exec_pod`` does not stand in for the box grant."""
    backend = _RecordingBackend()
    set_exec_backend(backend)
    _patch_relay(monkeypatch, is_app=False, is_box=True, exec_pod=True, box_attach=False)

    sent = await _run(BOX_PATH, [_OPEN_FRAME])

    assert sent[-1] == {"type": "websocket.close", "code": 4403}
    assert backend.opens == []


@pytest.mark.asyncio
async def test_app_target_still_demands_exec_pod(monkeypatch) -> None:
    """The reverse direction: the box grant must not open an app pod."""
    backend = _RecordingBackend()
    set_exec_backend(backend)
    _patch_relay(monkeypatch, is_app=True, is_box=False, exec_pod=False, box_attach=True)

    sent = await _run("/app/exec/web/web-7c9f-abc", [_OPEN_FRAME])

    assert sent[-1] == {"type": "websocket.close", "code": 4403}
    assert backend.opens == []


@pytest.mark.asyncio
async def test_unknown_slug_closes_not_found_not_denied(monkeypatch) -> None:
    """The disambiguation this ticket is about: a slug nothing answers to
    gets the not-found code, even for a caller holding every grant. An
    operator reading 4403 goes and asks an administrator for a
    permission; that was the wrong instruction for a typo'd slug."""
    backend = _RecordingBackend()
    set_exec_backend(backend)
    _patch_relay(monkeypatch, is_app=False, is_box=False, exec_pod=True, box_attach=True)

    sent = await _run("/app/exec/no-such-thing/some-pod", [_OPEN_FRAME])

    assert sent[-1] == {"type": "websocket.close", "code": 4404}
    assert backend.opens == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_app", "is_box", "exec_pod", "box_attach"),
    [
        (False, False, True, True),  # unknown target -> 4404
        (False, True, True, False),  # box, no attach grant -> 4403
        (True, False, False, True),  # app, no exec grant -> 4403
    ],
)
async def test_every_rejection_precedes_accept(monkeypatch, is_app, is_box, exec_pod, box_attach) -> None:
    """Guards a silent client break. A close sent before ``accept`` makes
    the ASGI server reject the handshake at HTTP level, and the CLI keys
    its error message off that status rather than off the WS close code.
    A rejection that started arriving *after* accept would still look
    correct here in close-code terms while changing what the CLI prints.
    """
    set_exec_backend(_RecordingBackend())
    _patch_relay(monkeypatch, is_app=is_app, is_box=is_box, exec_pod=exec_pod, box_attach=box_attach)

    sent = await _run(BOX_PATH, [_OPEN_FRAME])

    assert {"type": "websocket.accept"} not in sent
    assert sent == [sent[-1]]
    assert sent[-1]["type"] == "websocket.close"


@pytest.mark.asyncio
async def test_box_open_records_a_box_attach_not_an_app_audit(monkeypatch) -> None:
    """A box session must not be filed as ``app.exec_pod.opened`` against
    an app slug that does not exist."""
    records: list[dict] = []
    audits: list[dict] = []
    set_exec_backend(_RecordingBackend())
    _patch_relay(
        monkeypatch,
        is_app=False,
        is_box=True,
        exec_pod=False,
        box_attach=True,
        box_records=records,
        app_audits=audits,
    )

    await _run(BOX_PATH, [_OPEN_FRAME, {"type": "close"}])

    assert audits == []
    assert len(records) == 1
    assert records[0]["box_slug"] == "box-claude-dev-u7"
    assert records[0]["pod_name"] == "agent-box-abc123-x9k2p"
    assert records[0]["actor_user_id"] == 99


# ---- real ORM + real permission resolver (django_db) ----------------


def _org(slug: str):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name=slug.title(), slug=slug)


def _user(email: str):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(username=email, email=email)


def _box(org, *, slug="box-claude-dev-u7", **kwargs):
    from astrolift_agents.models import AgentBox

    return AgentBox.objects.create(
        organization=org,
        name="Claude box",
        slug=slug,
        status=AgentBox.Status.RUNNING,
        namespace="agents-acme",
        **kwargs,
    )


def _bind(user, org, *permissions):
    from astrolift_identity.models import Role, RoleBinding

    role = Role.objects.create(
        organization=org,
        name="Box Attacher",
        slug=f"box-attacher-{org.id}",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[p.value for p in permissions],
    )
    RoleBinding.objects.create(
        user=user,
        role=role,
        scope_kind=RoleBinding.ScopeKind.ORG,
        scope_id=org.id,
    )


@pytest.fixture
def captured_events():
    """Snapshot the *installed* writer and restore to it — restoring to
    ``_log_event`` would break later tests that rely on emit() landing
    rows in the Event table."""
    import core.events as events_mod

    captured: list = []
    previous = events_mod._writer
    events_mod.register_event_writer(captured.append)
    yield captured
    events_mod.register_event_writer(previous)


@pytest.mark.django_db
def test_box_lookup_refuses_another_orgs_box() -> None:
    """The mandatory cross-tenant case. ``@tenant_scoped`` asserts a
    tenant, it does not filter, so the by-slug lookup has to fail closed
    on its own or one org attaches to another org's agent."""
    caller = _org("caller-org")
    other = _org("other-org")
    box = _box(other)

    assert _check_box_in_tenant.func(box_slug=box.slug, tenant_org_id=caller.id) is False
    # Control: the row is otherwise perfectly attachable under its owner,
    # so the False above is the tenant boundary and nothing else.
    assert _check_box_in_tenant.func(box_slug=box.slug, tenant_org_id=other.id) is True


@pytest.mark.django_db
def test_box_lookup_refuses_a_retired_box() -> None:
    """A soft-deleted box is history; its slug must stop resolving so a
    stored attach command cannot land in a pod that was torn down."""
    org = _org("acme")
    box = _box(org)
    box.soft_delete()

    assert _check_box_in_tenant.func(box_slug=box.slug, tenant_org_id=org.id) is False


@pytest.mark.django_db
def test_box_lookup_refuses_a_tenantless_caller() -> None:
    org = _org("acme")
    box = _box(org)

    assert _check_box_in_tenant.func(box_slug=box.slug, tenant_org_id=None) is False


@pytest.mark.django_db
def test_attach_gate_needs_its_own_grant() -> None:
    """The real resolver: ``app.exec_pod`` does not satisfy the box gate,
    and ``agent_box.attach`` does."""
    from core.permissions import Permission
    from core.tenancy import clear_current_tenant

    org = _org("acme")
    user = _user("dev@example.com")

    try:
        assert _check_box_attach_permission.func(tenant_org_id=org.id, actor_user_id=user.id) is False
        _bind(user, org, Permission.AGENT_BOX_ATTACH)
        assert _check_box_attach_permission.func(tenant_org_id=org.id, actor_user_id=user.id) is True
    finally:
        # The gate installs the tenant contextvar; don't leak it.
        clear_current_tenant()


@pytest.mark.django_db
def test_attach_gate_is_org_scoped() -> None:
    """A grant in org A must not open a box in org B."""
    from core.permissions import Permission
    from core.tenancy import clear_current_tenant

    home = _org("home-org")
    elsewhere = _org("elsewhere-org")
    user = _user("dev@example.com")
    _bind(user, home, Permission.AGENT_BOX_ATTACH)

    try:
        assert _check_box_attach_permission.func(tenant_org_id=home.id, actor_user_id=user.id) is True
        assert _check_box_attach_permission.func(tenant_org_id=elsewhere.id, actor_user_id=user.id) is False
    finally:
        clear_current_tenant()


@pytest.mark.django_db
def test_open_stamps_last_attached_at_and_emits_the_event(captured_events) -> None:
    """The field had four references and none of them a write. A session
    opening is the moment worth recording."""
    org = _org("acme")
    box = _box(org)
    assert box.last_attached_at is None

    _record_box_attach.func(
        box_slug=box.slug,
        pod_name="agent-box-abc123-x9k2p",
        container="main",
        command=["tmux", "new-session", "-A", "-s", "astrolift"],
        tenant_org_id=org.id,
        actor_user_id=None,
    )

    box.refresh_from_db()
    assert box.last_attached_at is not None

    emitted = [e for e in captured_events if e.event_type == "agent_box.attached"]
    assert len(emitted) == 1
    assert emitted[0].resource_kind == "agent_box"
    assert emitted[0].resource_id == box.slug
    assert emitted[0].payload["pod_name"] == "agent-box-abc123-x9k2p"


@pytest.mark.django_db
def test_stamp_never_touches_another_orgs_box(captured_events) -> None:
    """Same slug in two orgs: stamping is org-filtered, so a caller in
    one org cannot write telemetry onto the other's row."""
    mine = _org("mine")
    theirs = _org("theirs")
    my_box = _box(mine)
    their_box = _box(theirs)

    _record_box_attach.func(
        box_slug=my_box.slug,
        pod_name="pod-1",
        container="main",
        command=["sh"],
        tenant_org_id=mine.id,
        actor_user_id=None,
    )

    my_box.refresh_from_db()
    their_box.refresh_from_db()
    assert my_box.last_attached_at is not None
    assert their_box.last_attached_at is None
