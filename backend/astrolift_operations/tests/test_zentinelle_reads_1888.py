"""Zentinelle usage, policy and audit reads, and the capability flag (#1888).

Zentinelle is faked at ``requests.request`` with the install-channel read
endpoints the reads ask for (calliopeai/zentinelle#390; not on Zentinelle's
main yet, so a real one answers 404, see the upgrade test); RBAC is real roles on a
two-team org, so the narrowing to the agents a viewer may see is the
resolver's, not a stub's.
"""

from __future__ import annotations

import json
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from django.utils import timezone

from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleConnection
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_operations.schema.zentinelle_reads import ExportZentinelleAuditInput, ZentinelleReadsMutations
from astrolift_registry.models import Workload
from core.permissions import Permission, PermissionDenied
from core.secrets import encrypt_at_rest
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

BASE = "https://zentinelle.test"
API = "/api/zentinelle/v1/astrolift"
SINCE = timezone.now() - timezone.timedelta(days=7)


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = {}
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        return self._body


class FakeZentinelle:
    """The install channel's read endpoints, answering per ``deployment_id``."""

    def __init__(self, credential: str) -> None:
        self.credential = credential
        self.calls: list[SimpleNamespace] = []
        self.status: dict[str, int] = {}
        self.audit_total = 3

    def __call__(self, method, url, *, json=None, headers=None, timeout=None, allow_redirects=True):
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        path = parts.path[len(API) :]
        self.calls.append(SimpleNamespace(method=method, path=path, query=query, headers=dict(headers or {})))
        if (headers or {}).get("Authorization") != f"Bearer {self.credential}":
            return FakeResponse(401, {"detail": "Invalid Astrolift install credential"})
        if path in self.status:
            return FakeResponse(self.status[path], {"error": "boom"})
        agents = query.get("deployment_id") or ["medops", "platform"]
        if path == "/usage":
            return FakeResponse(
                200,
                {
                    "usage": [
                        {
                            "deployment_id": agent,
                            "provider": "anthropic",
                            "model": "claude",
                            "requests": 2,
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cost_usd": 0.25,
                        }
                        for agent in agents
                    ]
                },
            )
        if path == "/policies":
            return FakeResponse(
                200,
                {
                    "policies": [
                        {
                            "id": f"pol-{agent}",
                            "name": f"budget {agent}",
                            "policy_type": "budget_limit",
                            "enforcement": "enforce",
                            "scope_type": "endpoint",
                            "priority": 10,
                            "description": "",
                        }
                        for agent in agents
                    ]
                },
            )
        if path == "/audit":
            start = int((query.get("cursor") or ["0"])[0])
            limit = int(query["limit"][0])
            rows = [
                {
                    "id": f"evt-{i}",
                    "timestamp": "2026-10-01T00:00:00+00:00",
                    "action": "=cmd" if i == 0 else "evaluate",
                    "resource_type": "agent",
                    "resource_id": "astrolift-task-1",
                    "resource_name": "",
                    "deployment_id": agents[0],
                    "agent_id": "astrolift-task-1",
                    "ext_user_id": "user-7",
                }
                for i in range(start, min(start + limit, self.audit_total))
            ]
            end = start + len(rows)
            return FakeResponse(
                200, {"events": rows, "next_cursor": str(end) if end < self.audit_total else None}
            )
        return FakeResponse(404, {"error": "not_found"})

    def reads(self) -> list[SimpleNamespace]:
        return [c for c in self.calls if c.path in ("/usage", "/policies", "/audit")]


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def world():
    world = ScopeWorld("z1888")
    world.agents = [
        Workload.objects.create(registered_app=app, name=slug, slug=slug, kind=Workload.Kind.AGENT)
        for app, slug in [(world.medops_app, "medops"), (world.platform_app, "platform")]
    ]
    world.user = make_user("z1888")
    world.info = make_info(world.user)
    return world


def _connect(world, *, status=ZentinelleConnection.Status.CONNECTED) -> str:
    credential = "sk_astroinst_" + secrets.token_hex(24)
    sealed = encrypt_at_rest(credential.encode())
    ZentinelleConnection.objects.create(
        organization=world.org,
        base_url=BASE,
        status=status,
        tenant_ids=["tenant-a"],
        credential_backend_kind=sealed.backend_kind,
        credential_ciphertext=sealed.backend_ref,
        connected_at=timezone.now(),
    )
    return credential


@pytest.fixture
def zentinelle(world, monkeypatch) -> FakeZentinelle:
    fake = FakeZentinelle(_connect(world))
    monkeypatch.setattr(zentinelle_connect.requests, "request", fake)
    return fake


def _grant(world, permissions, kind="ORG"):
    target = {"ORG": world.org, "TEAM": world.medops, "PROJECT": world.medops_project}[kind]
    bind_role(
        world.user,
        permissions=[Permission.ORG_READ, Permission.AGENT_READ, *permissions],
        kind=kind,
        scope_id=target.pk,
        slug=f"z1888-{kind}-{len(permissions)}",
    )


def _usage(world, **kwargs):
    with as_tenant(world, world.user):
        return OperationsQuery().astrolift_zentinelle_usage(world.info, since=SINCE, **kwargs)


# ---- capability -----------------------------------------------------------------


def test_capability_reports_the_callers_organization_without_calling_zentinelle(world, monkeypatch):
    calls = []
    monkeypatch.setattr(zentinelle_connect.requests, "request", lambda *a, **k: calls.append(a))
    _grant(world, [])
    with as_tenant(world, world.user):
        assert OperationsQuery().astrolift_zentinelle_capability(world.info).connected is False
    _connect(world, status=ZentinelleConnection.Status.REVOKED)
    with as_tenant(world, world.user):
        assert OperationsQuery().astrolift_zentinelle_capability(world.info).connected is False
    ZentinelleConnection.objects.filter(organization=world.org).update(
        status=ZentinelleConnection.Status.CONNECTED
    )
    with as_tenant(world, world.user):
        assert OperationsQuery().astrolift_zentinelle_capability(world.info).connected is True
    assert calls == []


def test_capability_is_per_organization(world, zentinelle):
    other = ScopeWorld("z1888b")
    other.user = make_user("z1888b")
    bind_role(other.user, permissions=[Permission.ORG_READ], kind="ORG", scope_id=other.org.pk, slug="z1888b")
    with as_tenant(other, other.user):
        assert OperationsQuery().astrolift_zentinelle_capability(make_info(other.user)).connected is False


# ---- usage --------------------------------------------------------------------------


def test_an_org_wide_viewer_reads_the_whole_tenant(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])

    result = _usage(world)

    [call] = zentinelle.reads()
    assert call.method == "GET" and call.path == "/usage"
    assert call.query["tenant_id"] == ["tenant-a"]
    assert "deployment_id" not in call.query
    assert call.query["since"] == [SINCE.isoformat()]
    assert {r.agent_slug for r in result.rows} == {"medops", "platform"}
    assert result.rows[0].cost_usd == 0.25 and result.rows[0].input_tokens == 100


def test_a_project_viewer_reads_only_its_projects_agents(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW], kind="PROJECT")

    result = _usage(world)

    [call] = zentinelle.reads()
    assert call.query["deployment_id"] == ["medops"]
    assert [r.agent_slug for r in result.rows] == ["medops"]


def test_naming_an_agent_the_viewer_cannot_see_is_not_found_and_never_asked(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW], kind="TEAM")

    with pytest.raises(Exception) as raised:
        _usage(world, agent_slug="platform")

    assert raised.value.extensions["code"] == "NOT_FOUND"
    assert zentinelle.reads() == []


def test_project_and_team_selectors_narrow_an_org_viewer(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])

    _usage(world, project_id=str(world.platform_project.guid))
    _usage(world, team_id=str(world.medops.guid))
    _usage(world, agent_slug="medops")

    assert [c.query["deployment_id"] for c in zentinelle.reads()] == [["platform"], ["medops"], ["medops"]]


def test_a_project_with_no_agents_answers_empty_without_asking(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])
    Workload.objects.filter(slug="platform").delete()

    assert _usage(world, project_id=str(world.platform_project.guid)).rows == []
    assert zentinelle.reads() == []


def test_a_slug_another_agent_shares_is_left_out(world, zentinelle):
    """Zentinelle files usage by slug, so a viewer of one ``medops`` must not get the other's."""
    Workload.objects.create(
        registered_app=world.platform_app, name="dup", slug="medops", kind=Workload.Kind.AGENT
    )
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW], kind="PROJECT")

    assert _usage(world).rows == []
    with pytest.raises(Exception) as raised:
        _usage(world, agent_slug="medops")
    assert raised.value.extensions["code"] == "PRECONDITION"
    assert zentinelle.reads() == []


def test_one_selector_at_a_time(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])
    with pytest.raises(Exception) as raised:
        _usage(world, agent_slug="medops", team_id=str(world.medops.guid))
    assert raised.value.extensions["code"] == "VALIDATION"


def test_usage_needs_usage_view(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_POLICY_VIEW])
    with pytest.raises(PermissionDenied):
        _usage(world)


# ---- honest failures ------------------------------------------------------------


def test_not_connected_is_said_not_answered_empty(world, monkeypatch):
    monkeypatch.setattr(zentinelle_connect.requests, "request", lambda *a, **k: pytest.fail("called"))
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])
    with pytest.raises(Exception) as raised:
        _usage(world)
    assert raised.value.extensions["code"] == "PRECONDITION"
    assert "not connected to Zentinelle" in raised.value.message


def test_a_zentinelle_without_the_reads_asks_for_an_upgrade(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])
    zentinelle.status["/usage"] = 404
    with pytest.raises(Exception) as raised:
        _usage(world)
    assert raised.value.extensions["code"] == "PRECONDITION"
    assert "calliopeai/zentinelle#390" in raised.value.message


def test_a_failing_or_unreachable_zentinelle_is_an_error(world, zentinelle, monkeypatch):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])
    zentinelle.status["/usage"] = 500
    with pytest.raises(Exception) as raised:
        _usage(world)
    assert raised.value.extensions["code"] == "INTERNAL" and "500" in raised.value.message

    def unreachable(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(zentinelle_connect.requests, "request", unreachable)
    with pytest.raises(Exception) as raised:
        _usage(world)
    assert "could not reach Zentinelle" in raised.value.message


def test_a_refused_credential_marks_the_connection_revoked(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])
    zentinelle.credential = "sk_astroinst_other"
    with pytest.raises(Exception) as raised:
        _usage(world)
    assert raised.value.extensions["code"] == "PRECONDITION"
    assert ZentinelleConnection.objects.get(organization=world.org).status == "revoked"
    with as_tenant(world, world.user):
        assert OperationsQuery().astrolift_zentinelle_capability(world.info).connected is False


# ---- policies and audit -------------------------------------------------------------


def test_policies_for_an_agent(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_POLICY_VIEW])
    with as_tenant(world, world.user):
        rows = OperationsQuery().astrolift_zentinelle_policies(world.info, agent_slug="platform")
    [call] = zentinelle.reads()
    assert call.path == "/policies" and call.query["deployment_id"] == ["platform"]
    assert [(p.id, p.policy_type, p.enforcement, p.scope) for p in rows] == [
        ("pol-platform", "budget_limit", "enforce", "endpoint")
    ]


def test_audit_pages_through_the_cursor(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_AUDIT_VIEW], kind="PROJECT")
    with as_tenant(world, world.user):
        first = OperationsQuery().astrolift_zentinelle_audit_page(world.info, limit=2)
        second = OperationsQuery().astrolift_zentinelle_audit_page(
            world.info, limit=2, cursor=first.next_cursor
        )
    assert [e.id for e in first.events] == ["evt-0", "evt-1"] and first.next_cursor == "2"
    assert [e.id for e in second.events] == ["evt-2"] and second.next_cursor is None
    assert all(c.query["deployment_id"] == ["medops"] for c in zentinelle.reads())
    assert first.events[0].agent_slug == "medops" and first.events[0].actor == "user-7"


def test_audit_view_does_not_grant_export(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_AUDIT_VIEW])
    with as_tenant(world, world.user):
        result = ZentinelleReadsMutations().export_zentinelle_audit(
            world.info, ExportZentinelleAuditInput(since=SINCE)
        )
    assert not result.ok and str(result.errors[0].code) == "PERMISSION_DENIED"
    assert zentinelle.reads() == []


def test_export_collects_every_page_and_is_audited(world, zentinelle, monkeypatch):
    import core.mutations as core_mutations

    audited = []
    original = core_mutations._audit_writer
    core_mutations.register_audit_writer(lambda entry: (audited.append(entry), original(entry)))
    monkeypatch.setattr("astrolift_operations.zentinelle_reads.AUDIT_PAGE_MAX", 2)
    try:
        _grant(world, [Permission.ZENTINELLE_AUDIT_EXPORT])
        with as_tenant(world, world.user):
            ndjson = ZentinelleReadsMutations().export_zentinelle_audit(
                world.info, ExportZentinelleAuditInput(since=SINCE)
            )
            csv_result = ZentinelleReadsMutations().export_zentinelle_audit(
                world.info, ExportZentinelleAuditInput(since=SINCE, format="csv", agent_slug="platform")
            )
    finally:
        core_mutations.register_audit_writer(original)

    assert ndjson.ok and ndjson.data.row_count == 3
    lines = [json.loads(line) for line in ndjson.data.content.splitlines()]
    assert [line["id"] for line in lines] == ["evt-0", "evt-1", "evt-2"]
    assert lines[0]["action"] == "=cmd"
    assert csv_result.ok and csv_result.data.content.splitlines()[1].startswith("evt-0,")
    assert "'=cmd" in csv_result.data.content
    assert [e.action for e in audited] == ["zentinelle.audit.export", "zentinelle.audit.export"]
    assert audited[0].extra["row_count"] == 3


def test_export_refuses_an_unknown_format(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_AUDIT_EXPORT])
    with as_tenant(world, world.user):
        result = ZentinelleReadsMutations().export_zentinelle_audit(
            world.info, ExportZentinelleAuditInput(since=SINCE, format="pdf")
        )
    assert not result.ok and result.errors[0].field == "format"


def test_export_stops_when_zentinelle_repeats_a_cursor(world, zentinelle, monkeypatch):
    def stuck(method, url, **kwargs):
        zentinelle(method, url, **kwargs)
        return FakeResponse(200, {"events": [], "next_cursor": "7"})

    monkeypatch.setattr(zentinelle_connect.requests, "request", stuck)
    _grant(world, [Permission.ZENTINELLE_AUDIT_EXPORT])
    with as_tenant(world, world.user):
        result = ZentinelleReadsMutations().export_zentinelle_audit(
            world.info, ExportZentinelleAuditInput(since=SINCE)
        )
    assert not result.ok and "same audit cursor" in result.errors[0].message
    assert len(zentinelle.reads()) == 2
