"""Tests for the noVNC WebSocket relay (#877).

Mirrors ``test_exec_ws.py``: the auth + ASGI dispatch live behind
cookies/sessions, so these unit tests cover the protocol-level pieces by
patching the auth + task-resolution helpers:

  - path parser
  - auth rejection (no/invalid session)
  - permission denial
  - non-running / non-vnc-capable task rejection
  - happy-path bidirectional byte relay against a fake backend
  - backend swap + the default stub's EOF behavior

A second tier (``@pytest.mark.django_db``) exercises the REAL security
logic — ``_resolve_vnc_task`` and ``_check_vnc_permission`` — against
real Organization / AgentTask / User / RoleBinding rows so that
cross-tenant isolation and the ``agent_task.watch`` gate are genuinely
covered rather than monkeypatched away.
"""

from __future__ import annotations

import asyncio

import pytest

from core.schema.vnc_ws import (
    VncBackend,
    VncSession,
    _check_vnc_permission,
    _parse_task_guid,
    _resolve_vnc_task,
    _StubVncBackend,
    get_vnc_backend,
    set_vnc_backend,
)

# ---- path parser ----------------------------------------------------


def test_parse_task_guid() -> None:
    assert _parse_task_guid("/app/vnc/abc-123") == "abc-123"


def test_parse_task_guid_with_trailing_segments_takes_guid() -> None:
    assert _parse_task_guid("/app/vnc/abc-123/extra") == "abc-123"


def test_parse_task_guid_too_short_returns_none() -> None:
    assert _parse_task_guid("/app/vnc") is None
    assert _parse_task_guid("/app/exec/acme/web") is None
    assert _parse_task_guid("/other/path") is None


# ---- backend swap + stub --------------------------------------------


def test_default_backend_is_either_stub_or_production_wired() -> None:
    from core.cluster_vnc import K8sVncBackend

    backend = get_vnc_backend()
    assert isinstance(backend, (_StubVncBackend, K8sVncBackend))


def test_set_vnc_backend_swaps() -> None:
    class _Other(VncBackend):
        async def open(self, *, task_guid: str) -> VncSession:
            return VncSession()  # type: ignore[abstract]

    prior = get_vnc_backend()
    try:
        set_vnc_backend(_Other())
        assert isinstance(get_vnc_backend(), _Other)
    finally:
        set_vnc_backend(prior)


@pytest.mark.asyncio
async def test_stub_backend_eofs_immediately() -> None:
    backend = _StubVncBackend()
    session = await backend.open(task_guid="abc-123")
    assert await session.recv() == b""
    await session.sendall(b"ignored")
    await session.close()


# ---- shared scaffolding ---------------------------------------------


class _FakeVncSession(VncSession):
    """Records bytes written to the pod and replays a scripted set of
    pod->client chunks, then EOFs."""

    def __init__(self, pod_chunks: list[bytes]) -> None:
        self._pod_chunks = list(pod_chunks)
        self.sent_to_pod: list[bytes] = []
        self.closed = False

    async def recv(self) -> bytes:
        if self._pod_chunks:
            return self._pod_chunks.pop(0)
        # Block until cancelled so the relay's receive loop drives the
        # teardown — emulates an idle-but-open framebuffer.
        await asyncio.sleep(3600)
        return b""

    async def sendall(self, data: bytes) -> None:
        self.sent_to_pod.append(data)

    async def close(self) -> None:
        self.closed = True


class _FakeVncBackend(VncBackend):
    def __init__(self, session: _FakeVncSession) -> None:
        self._session = session
        self.opened_for: list[str] = []

    async def open(self, *, task_guid: str) -> VncSession:
        self.opened_for.append(task_guid)
        return self._session


def _patch_auth(
    monkeypatch,
    *,
    authed: bool = True,
    org_id: int | None = 1,
    perm_ok: bool = True,
    task_ok: bool = True,
    pod_name: str = "agent-task-deadbeef",
    audit_sink: list | None = None,
) -> None:
    """Patch the auth + task-resolution helpers so the relay handshake
    reaches (or fails before) the accept, without Django sessions / a
    cluster."""
    import core.schema.ws_auth as ws_auth_mod
    from core.schema import vnc_ws as mod

    async def _user(_session_key):
        class _U:
            is_authenticated = authed

        return _U(), {}

    async def _tenant(_user, _data):
        if org_id is None:
            return None

        class _T:
            organization_id = org_id
            actor_user_id = 99

        return _T()

    async def _perm(*, tenant_org_id, actor_user_id):
        return perm_ok

    async def _task(*, task_guid, tenant_org_id):
        if not task_ok:
            return None
        return {"pod_name": pod_name}

    async def _audit(**kw):
        if audit_sink is not None:
            audit_sink.append(kw)

    monkeypatch.setattr(ws_auth_mod, "_resolve_user_from_sessionid", _user)
    monkeypatch.setattr(ws_auth_mod, "_resolve_tenant_for_user", _tenant)
    monkeypatch.setattr(mod, "_check_vnc_permission", _perm)
    monkeypatch.setattr(mod, "_resolve_vnc_task", _task)
    monkeypatch.setattr(mod, "_audit_vnc_open", _audit)


def _drive(scope, incoming):
    """Build a receive()/send() pair over a scripted incoming list."""
    queue = list(incoming)
    sent: list[dict] = []

    async def receive() -> dict:
        if queue:
            return queue.pop(0)
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    return receive, send, sent


# ---- REAL security logic (django_db) --------------------------------
#
# These call the underlying SYNC bodies of the sync_to_async-wrapped
# helpers via ``.func`` so they run in a plain @django_db test (no event
# loop) while still exercising the real ORM query + permission resolver.
# Nothing about _resolve_vnc_task / _check_vnc_permission is patched.


def _make_org(slug: str):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name=slug.title(), slug=slug)


def _make_user(email: str):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(username=email, email=email)


def _make_running_vnc_task(org, *, vnc_enabled: bool = True, pod_name: str = "agent-task-deadbeef"):
    """A RUNNING AgentTask in ``org`` with the frozen vnc_enabled flag set.

    Walks the real transition graph DRAFT -> QUEUED -> PROVISIONING ->
    RUNNING so the row mirrors a genuinely-dispatched task.
    """
    from astrolift_agents.models import AgentTask

    task = AgentTask.objects.create(
        organization=org,
        vnc_enabled=vnc_enabled,
        pod_name=pod_name,
    )
    task.transition_to(AgentTask.Status.QUEUED)
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.transition_to(AgentTask.Status.RUNNING)
    return task


def _grant_watch(user, org):
    """Bind ``user`` to a role carrying agent_task.watch in ``org``."""
    from astrolift_identity.models import Role, RoleBinding

    from core.permissions import Permission

    role = Role.objects.create(
        organization=org,
        name="VNC Watcher",
        slug=f"vnc-watcher-{org.id}",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.AGENT_TASK_WATCH.value],
    )
    RoleBinding.objects.create(
        user=user,
        role=role,
        scope_kind=RoleBinding.ScopeKind.ORG,
        scope_id=org.id,
    )


@pytest.mark.django_db
def test_resolve_vnc_task_cross_tenant_returns_none() -> None:
    """A RUNNING, vnc-enabled, pod-set task in ANOTHER org must NOT
    resolve for the caller's org — deny-by-default tenant isolation."""
    caller_org = _make_org("caller-org")
    other_org = _make_org("other-org")
    task = _make_running_vnc_task(other_org)

    # Right guid, wrong (caller's) org -> None.
    assert _resolve_vnc_task.func(task_guid=str(task.guid), tenant_org_id=caller_org.id) is None
    # And it WOULD resolve under its own org (control), proving the row is
    # otherwise valid and the None above is purely the tenant boundary.
    assert _resolve_vnc_task.func(task_guid=str(task.guid), tenant_org_id=other_org.id) is not None


@pytest.mark.django_db
def test_resolve_vnc_task_in_org_resolves() -> None:
    """RUNNING + vnc_enabled + pod_name in the caller's org resolves to
    the pod name (not None)."""
    org = _make_org("acme")
    task = _make_running_vnc_task(org, pod_name="agent-task-abc123")

    resolved = _resolve_vnc_task.func(task_guid=str(task.guid), tenant_org_id=org.id)
    assert resolved == {"pod_name": "agent-task-abc123"}


@pytest.mark.django_db
def test_resolve_vnc_task_non_running_returns_none() -> None:
    """A vnc-enabled task that has not reached RUNNING must not resolve."""
    from astrolift_agents.models import AgentTask

    org = _make_org("acme")
    task = AgentTask.objects.create(
        organization=org,
        vnc_enabled=True,
        pod_name="agent-task-abc123",
    )
    task.transition_to(AgentTask.Status.QUEUED)
    task.transition_to(AgentTask.Status.PROVISIONING)
    assert task.status == AgentTask.Status.PROVISIONING

    assert _resolve_vnc_task.func(task_guid=str(task.guid), tenant_org_id=org.id) is None


@pytest.mark.django_db
def test_resolve_vnc_task_not_vnc_enabled_returns_none() -> None:
    """A RUNNING task whose FROZEN vnc_enabled flag is False must not
    resolve — even with a pod_name set (the gate is the frozen column,
    per FIX 3, not the live spec)."""
    org = _make_org("acme")
    task = _make_running_vnc_task(org, vnc_enabled=False, pod_name="agent-task-abc123")

    assert _resolve_vnc_task.func(task_guid=str(task.guid), tenant_org_id=org.id) is None


@pytest.mark.django_db
def test_resolve_vnc_task_no_pod_name_returns_none() -> None:
    """A RUNNING, vnc-enabled task with no pod_name yet cannot be
    port-forwarded, so the gate denies it."""
    org = _make_org("acme")
    task = _make_running_vnc_task(org, vnc_enabled=True, pod_name="")

    assert _resolve_vnc_task.func(task_guid=str(task.guid), tenant_org_id=org.id) is None


@pytest.mark.django_db
def test_check_vnc_permission_grants_only_with_watch() -> None:
    """The real permission resolver: a user WITHOUT agent_task.watch is
    denied; the SAME user, once bound to a role carrying it, is allowed."""
    from core.tenancy import clear_current_tenant

    org = _make_org("acme")
    user = _make_user("watcher@example.com")

    try:
        # No binding yet -> deny-by-default.
        assert (
            _check_vnc_permission.func(tenant_org_id=org.id, actor_user_id=user.id)
            is False
        )

        # Grant agent_task.watch in this org -> now allowed.
        _grant_watch(user, org)
        assert (
            _check_vnc_permission.func(tenant_org_id=org.id, actor_user_id=user.id)
            is True
        )
    finally:
        # _check_vnc_permission sets the tenant contextvar; don't leak it.
        clear_current_tenant()


@pytest.mark.django_db
def test_check_vnc_permission_is_org_scoped() -> None:
    """A watch grant in org A must NOT satisfy the gate for org B —
    the permission is scoped to the org the binding names."""
    from core.tenancy import clear_current_tenant

    org_a = _make_org("org-a")
    org_b = _make_org("org-b")
    user = _make_user("scoped@example.com")
    _grant_watch(user, org_a)

    try:
        assert (
            _check_vnc_permission.func(tenant_org_id=org_a.id, actor_user_id=user.id)
            is True
        )
        # Same user, different org, no binding there -> denied.
        assert (
            _check_vnc_permission.func(tenant_org_id=org_b.id, actor_user_id=user.id)
            is False
        )
    finally:
        clear_current_tenant()


# ---- rejection paths ------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_bad_path() -> None:
    from core.schema import vnc_ws as mod

    receive, send, sent = _drive(None, [])
    scope = {"type": "websocket", "path": "/app/vnc", "headers": []}
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4404}


@pytest.mark.asyncio
async def test_rejects_unauthed(monkeypatch) -> None:
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch, authed=False)
    receive, send, sent = _drive(None, [])
    scope = {"type": "websocket", "path": "/app/vnc/abc-123", "headers": []}
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4401}


@pytest.mark.asyncio
async def test_rejects_permission_denied(monkeypatch) -> None:
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch, perm_ok=False)
    backend = _FakeVncBackend(_FakeVncSession([]))
    set_vnc_backend(backend)
    receive, send, sent = _drive(None, [])
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4403}
    # Never accepted, never opened a port-forward.
    assert backend.opened_for == []
    assert {"type": "websocket.accept"} not in sent


@pytest.mark.asyncio
async def test_rejects_non_running_or_non_vnc_task(monkeypatch) -> None:
    """When the task is missing / not RUNNING / not vnc_enabled the
    resolver returns None and the relay closes 4410 before accept."""
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch, task_ok=False)
    backend = _FakeVncBackend(_FakeVncSession([]))
    set_vnc_backend(backend)
    receive, send, sent = _drive(None, [])
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4410}
    assert backend.opened_for == []
    assert {"type": "websocket.accept"} not in sent


# ---- happy path -----------------------------------------------------


@pytest.mark.asyncio
async def test_relays_frames_both_directions(monkeypatch) -> None:
    """Client bytes reach the pod via sendall; pod bytes reach the
    client as websocket.send bytes frames. The session is opened once,
    audited once, and closed on teardown."""
    from core.schema import vnc_ws as mod

    audited: list[dict] = []
    _patch_auth(monkeypatch, audit_sink=audited)

    session = _FakeVncSession(pod_chunks=[b"RFB 003.008\n", b"framebuffer"])
    backend = _FakeVncBackend(session)
    set_vnc_backend(backend)

    incoming = [
        {"type": "websocket.receive", "bytes": b"client->pod-1"},
        {"type": "websocket.receive", "bytes": b"client->pod-2"},
        {"type": "websocket.disconnect"},
    ]
    receive, send, sent = _drive(None, incoming)
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }

    await mod.vnc_ws_application(scope, receive, send)

    # Accepted, then closed 1000.
    assert sent[0] == {"type": "websocket.accept"}
    assert sent[-1] == {"type": "websocket.close", "code": 1000}

    # Opened exactly once for the right task + audited once.
    assert backend.opened_for == ["abc-123"]
    assert len(audited) == 1
    assert audited[0]["task_guid"] == "abc-123"
    assert audited[0]["actor_user_id"] == 99

    # Client -> pod: both byte frames forwarded in order.
    assert session.sent_to_pod == [b"client->pod-1", b"client->pod-2"]

    # Pod -> client: both scripted chunks delivered as binary sends.
    pod_to_client = [m["bytes"] for m in sent if m.get("type") == "websocket.send"]
    assert b"RFB 003.008\n" in pod_to_client
    assert b"framebuffer" in pod_to_client

    # Session torn down.
    assert session.closed is True


@pytest.mark.asyncio
async def test_client_disconnect_closes_relay(monkeypatch) -> None:
    """Client-disconnect teardown: the client sends a disconnect while
    the pod framebuffer is still live (recv idles after one chunk). The
    relay tears down cleanly, closes 1000, and closes the session."""
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch)
    # One chunk, then the fake session's recv blocks forever (idle but
    # open framebuffer) — so teardown can ONLY be driven by the client
    # disconnect queued below.
    session = _FakeVncSession(pod_chunks=[b"hello"])
    set_vnc_backend(_FakeVncBackend(session))

    incoming = [{"type": "websocket.disconnect"}]
    receive, send, sent = _drive(None, incoming)
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.vnc_ws_application(scope, receive, send)

    assert sent[0] == {"type": "websocket.accept"}
    assert sent[-1] == {"type": "websocket.close", "code": 1000}
    assert session.closed is True


class _EofVncSession(VncSession):
    """Replays scripted pod->client chunks then returns b"" (EOF) on
    every subsequent recv — modelling the pod socket closing. Unlike
    _FakeVncSession it does NOT block after the chunks, so pod-EOF alone
    must drive teardown.

    ``recv_calls`` is the regression hook: a correct pump returns the
    instant it reads the b"" EOF, so it calls recv exactly
    ``len(chunks) + 1`` times. A broken EOF branch (e.g. one that
    ``continue``s instead of returning) keeps reading past the EOF, so
    the count climbs — the test asserts the exact count."""

    def __init__(self, pod_chunks: list[bytes]) -> None:
        self._pod_chunks = list(pod_chunks)
        self.sent_to_pod: list[bytes] = []
        self.closed = False
        self.recv_calls = 0

    async def recv(self) -> bytes:
        self.recv_calls += 1
        # Yield so a broken (spinning) pump can't starve the event loop —
        # the relay's own teardown / the test's wait_for stays responsive.
        await asyncio.sleep(0)
        if self._pod_chunks:
            return self._pod_chunks.pop(0)
        return b""  # EOF — pod framebuffer gone (returned on every call).

    async def sendall(self, data: bytes) -> None:
        self.sent_to_pod.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_pod_eof_alone_closes_relay(monkeypatch, caplog) -> None:
    """Pod-EOF teardown with NO client disconnect queued: the client
    receive idles forever, so only the pod side returning b"" can end
    the relay. This exercises the ``if not chunk: return`` branch in
    _pump_pod_to_client — the teardown path the old test never hit
    (it ended via a queued client disconnect instead).

    The sharp regression signal is ``recv_calls``: the pump must read the
    EOF exactly once and return. If the EOF branch stops returning, the
    pump reads past the EOF (count > chunks + 1). The relay logs+swallows
    a *raising* pump, so a crash there would be masked — hence we assert
    on the call count and a clean (un-logged) teardown, not on a raise."""
    import logging

    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch)
    chunks = [b"RFB 003.008\n", b"frame"]
    session = _EofVncSession(pod_chunks=list(chunks))
    set_vnc_backend(_FakeVncBackend(session))

    # Client never sends and never disconnects — receive() idles forever
    # so it cannot be the thing that tears the relay down.
    async def receive() -> dict:
        await asyncio.sleep(3600)
        return {"type": "websocket.disconnect"}

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }

    # If pod-EOF did NOT drive teardown, this would hang on the idling
    # client receive; wait_for bounds it so a regression fails (timeout)
    # rather than hanging the suite.
    with caplog.at_level(logging.ERROR, logger="core.schema.vnc_ws"):
        await asyncio.wait_for(mod.vnc_ws_application(scope, receive, send), timeout=5)

    assert sent[0] == {"type": "websocket.accept"}
    # Both scripted chunks were relayed before the EOF.
    pod_to_client = [m["bytes"] for m in sent if m.get("type") == "websocket.send"]
    assert pod_to_client == chunks
    # Relay closed 1000 and the pod session was torn down — driven solely
    # by the pod EOF.
    assert sent[-1] == {"type": "websocket.close", "code": 1000}
    assert session.closed is True
    # The pump read each chunk then the single EOF and returned at once —
    # exactly len(chunks) + 1 recv calls. A broken EOF branch that keeps
    # reading would push this higher.
    assert session.recv_calls == len(chunks) + 1
    # Clean EOF teardown logs nothing; a pump that raised (and was
    # swallowed) would have logged a "relay pump failed" error.
    assert "relay pump failed" not in caplog.text
