"""Legacy mutations that name another user stay out of that user's account (#1979).

* ``organizationMemberStatus`` flips the global ``User.is_active``, which
  ends the person's access in every org. Sharing a legacy organization
  row with them was the only gate.
* ``pinTransaction`` with ``proxyUser`` refused only a correct PIN, so its
  answers told any caller which guess matched another user's PIN.

Both run through the real schema, as the client would call them.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore

from config.schema import schema
from core.models import PinTransaction
from core.schema.context import StrawberryContext
from organization.models import Organization as LegacyOrganization
from organization.models import OrganizationMember

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


class _Request:
    def __init__(self, user):
        self.user = user
        self.session = SessionStore()
        self.session.create()
        self.headers = {}


def _run(user, query: str, **variables):
    return schema.execute_sync(
        query, context_value=StrawberryContext(_Request(user)), variable_values=variables
    )


MEMBER_STATUS = """
    mutation($userId: ID!, $orgId: ID!) {
        organizationMemberStatus(input: {userId: $userId, isActive: false, organizationId: $orgId}) {
            ok
        }
    }
"""

PIN_TRANSACTION = """
    mutation($pin: String!, $proxy: ID) {
        pinTransaction(pin: $pin, proxyUser: $proxy)
    }
"""


def test_a_legacy_co_member_cannot_switch_off_someone_elses_account():
    org = LegacyOrganization.objects.create(name="Legacy 1979")
    caller = User.objects.create_user(username="legacy-caller-1979", email="caller@legacy.test", password="x")
    root = User.objects.create_superuser(username="legacy-root-1979", email="root@legacy.test", password="x")
    for user in (caller, root):
        OrganizationMember.objects.create(organization=org, member=user, is_active=True)

    result = _run(caller, MEMBER_STATUS, userId=str(root.pk), orgId=str(org.pk))

    assert result.errors, result.data
    assert "platform operator" in result.errors[0].message
    root.refresh_from_db()
    assert root.is_active is True
    assert OrganizationMember.objects.get(member=root).is_active is True


def test_a_proxy_pin_gets_one_answer_whatever_the_guess():
    caller = User.objects.create_user(username="pin-caller-1979", email="caller@pin.test", password="x")
    victim = User.objects.create_user(username="pin-victim-1979", email="victim@pin.test", password="x")
    no_pin = User.objects.create_user(username="pin-none-1979", email="none@pin.test", password="x")
    victim.profile.update_pin("2468")

    answers = {
        _run(caller, PIN_TRANSACTION, pin=guess, proxy=str(target.pk)).errors[0].message
        for guess, target in (("2468", victim), ("1357", victim), ("2468", no_pin))
    }

    assert answers == {"Authenticating with another user's PIN is not permitted."}
    assert not PinTransaction.objects.filter(user=caller).exists()
