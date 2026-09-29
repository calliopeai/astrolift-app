"""The audit trail on the list contract (#2151).

``astroliftAuditEventsPage`` gains ``search``, ``targetKind``,
``targetId``, ``subjectUserId``, ``filter`` and ``sort``;
``exportAuditEvents`` takes the same set, so the file is the rows on
screen. Pinned here: each narrows the page and its count, they AND with
the legacy arguments, a person's grants and revokes are found by the
subject filter whichever way they were filed, and the old cursors keep
walking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_graphql import UnsupportedSort
from astrolift_identity.models import Organization, Role, RoleBinding
from astrolift_operations.models import AuditEvent
from astrolift_operations.schema.audit_list import AuditEventsFilterInput
from astrolift_operations.schema.queries import OperationsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Audit Org", slug="audit-2151")


@pytest.fixture
def people():
    User = get_user_model()
    return SimpleNamespace(
        alice=User.objects.create(username="alice-2151", email="alice@example.test"),
        bob=User.objects.create(username="bob-2151", email="bob@example.test"),
    )


def _event(org, action, **fields) -> AuditEvent:
    return AuditEvent.objects.create(
        organization=org,
        actor_kind=fields.pop("actor_kind", "user"),
        actor_id=fields.pop("actor_id", ""),
        action=action,
        decision=fields.pop("decision", AuditEvent.Decision.ALLOW),
        **fields,
    )


def _page(org, *, viewer=None, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=viewer)):
        return OperationsQuery().astrolift_audit_events_page(_info(), include_total=True, **kwargs)


def _actions(page) -> list[str]:
    return sorted(e.action for e in page.items)


@pytest.fixture
def trail(org, people):
    """A grant to Bob (filed under the user), a single revoke of one of his
    bindings (filed under the binding), a bulk revoke naming him in the
    data, and unrelated noise, one of them in another org."""
    role = Role.objects.create(organization=org, name="Viewer 2151", slug="viewer-2151", permissions=[])
    binding = RoleBinding.objects.create(user=people.bob, role=role, scope_kind="ORG", scope_id=org.pk)
    binding.soft_delete()
    alice = str(people.alice.pk)
    _event(org, "role_binding.grant", actor_id=alice, target_kind="user", target_id=str(people.bob.pk))
    _event(
        org, "role_binding.revoke", actor_id=alice, target_kind="role_binding", target_id=str(binding.guid)
    )
    _event(
        org,
        "role_binding.revoke",
        actor_id=alice,
        target_kind="role_binding",
        target_id="00000000-0000-0000-0000-00000000beef",
        data={"bulk": True, "user_id": people.bob.pk},
    )
    _event(org, "team.create", actor_id=str(people.bob.pk), target_kind="Team", target_slug="payments")
    _event(
        org,
        "app.deploy",
        actor_id=alice,
        decision=AuditEvent.Decision.DENY,
        target_kind="RegisteredApp",
        target_id="app-9",
        request_id="req-7e11aa",
    )
    other = Organization.objects.create(name="Other", slug="audit-2151-other")
    _event(other, "role_binding.grant", target_kind="user", target_id=str(people.bob.pk))
    return SimpleNamespace(binding=binding)


def test_subject_user_finds_grants_and_revokes_however_filed(permission_resolver, org, people, trail):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    page = _page(org, subject_user_id=str(people.bob.pk))
    assert _actions(page) == ["role_binding.grant", "role_binding.revoke", "role_binding.revoke"]
    assert page.total_count == 3
    # The same through the filter input, and "me" as the viewer.
    mine = _page(org, viewer=people.bob.pk, filter=AuditEventsFilterInput(subject_user="me"))
    assert mine.total_count == 3
    # A value that is not a pk matches nothing rather than everything.
    assert _page(org, subject_user_id="bob").total_count == 0
    # "me" with no viewer matches nothing.
    assert _page(org, filter=AuditEventsFilterInput(subject_user="me")).total_count == 0


def test_target_kind_and_id_narrow_the_page_and_count(permission_resolver, org, people, trail):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    by_kind = _page(org, target_kind="ROLE_BINDING")
    assert _actions(by_kind) == ["role_binding.revoke", "role_binding.revoke"]
    assert by_kind.total_count == 2
    listed = _page(org, filter=AuditEventsFilterInput(target_kind=["team", "registeredapp"]))
    assert _actions(listed) == ["app.deploy", "team.create"]
    by_id = _page(org, target_kind="user", target_id=str(people.bob.pk))
    assert _actions(by_id) == ["role_binding.grant"]


def test_search_matches_action_prefix_actor_target_and_request(permission_resolver, org, people, trail):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    assert _page(org, search="role_binding.").total_count == 3
    # Actor by username, through the user table (the writer leaves actor_display blank).
    assert _actions(_page(org, search="bob-2151")) == ["team.create"]
    assert _actions(_page(org, search="payme")) == ["team.create"]
    assert _actions(_page(org, search="req-7e11")) == ["app.deploy"]
    # A fragment in the middle of an action is not a prefix.
    assert _page(org, search="binding").total_count == 0


def test_new_arguments_and_the_legacy_ones_and_together(permission_resolver, org, people, trail):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    alice = str(people.alice.pk)
    page = _page(org, actor_id=alice, target_kind="role_binding", decision="allow")
    assert page.total_count == 2
    page = _page(
        org,
        viewer=people.alice.pk,
        filter=AuditEventsFilterInput(actor=["me"], decision=["deny"]),
    )
    assert _actions(page) == ["app.deploy"]


def test_sort_oldest_first_and_unknown_key_refused(permission_resolver, org, people, trail):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    newest = [e.id for e in _page(org, limit=10).items]
    oldest = [e.id for e in _page(org, limit=10, sort="occurredAt").items]
    assert oldest == list(reversed(newest))
    assert [e.id for e in _page(org, limit=10, sort="-occurredAt").items] == newest
    with pytest.raises(UnsupportedSort):
        _page(org, sort="action")


def test_oldest_first_walk_is_complete_and_cursors_do_not_cross_orders(
    permission_resolver, org, people, trail
):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = _page(org, limit=2, after=cursor, sort="occurredAt")
        seen.extend(str(e.id) for e in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 5
    # A newest-first cursor (the unscoped one the page always issued) handed
    # to the oldest-first order restarts from the top instead of seeking.
    legacy = _page(org, limit=2).next_cursor
    restarted = _page(org, limit=2, after=legacy, sort="occurredAt")
    assert [str(e.id) for e in restarted.items] == seen[:2]
    # And it still continues its own walk without a sort argument.
    assert _page(org, limit=2, after=legacy).items[0].id not in [e.id for e in _page(org, limit=2).items]


def test_export_honours_the_same_filters(permission_resolver, org, people, trail, tmp_path):
    from django.test import override_settings

    from astrolift_operations.models import AuditExport
    from astrolift_operations.schema.mutations import OperationsMutation
    from astrolift_operations.schema.mutations.types import ExportAuditEventsInput

    permission_resolver.grant(Permission.AUDIT_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=people.alice.pk)):
            result = OperationsMutation().export_audit_events(
                _info(),
                ExportAuditEventsInput(
                    format="CSV",
                    subject_user_id=str(people.bob.pk),
                    filter=AuditEventsFilterInput(target_kind=["role_binding"]),
                ),
            )
    assert result.ok is True, result.errors
    # The two revokes about Bob; the grant is filed under the user, not a binding.
    assert result.data.row_count == 2
    snapshot = AuditExport.objects.get().filters_snapshot
    assert snapshot["subject_user_id"] == str(people.bob.pk)
    assert snapshot["filter"] == {"target_kind": ["role_binding"]}


def test_single_grant_and_revoke_file_under_their_subject():
    """What the subject filter relies on: a grant names the user, a revoke the binding."""
    from astrolift_identity.schema.mutations.role_bindings import _grant_target, _revoke_target

    assert _grant_target(None, None, SimpleNamespace(user_id="42")) == ("user", "42")
    assert _revoke_target(None, None, input=SimpleNamespace(id="01a0-guid")) == ("role_binding", "01a0-guid")
