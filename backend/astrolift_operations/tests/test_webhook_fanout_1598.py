"""Emitted events reach webhook subscribers (#1598).

Everything either side of this existed and nothing joined them.
`core.events` has had `register_event_subscriber` since events landed, and
no webhook subscriber was ever registered. `webhook_delivery` had signing,
headers, a jittered retry schedule, outcome classification and an in-flight
cap. `WebhookSubscription` rows were creatable through the API and carried
an `events` allow-list nothing consulted.

So an operator could add a subscription, see it listed, fire a test delivery
that succeeded, and never receive a single real event.

The scoping tests matter most. A webhook is an egress channel to a third
party, so a subscription narrowed to one app receiving another app's events
is a cross-tenant disclosure, not a cosmetic bug.
"""

from __future__ import annotations

import pytest

from astrolift_operations.webhook_fanout import _wants, matching_subscriptions

pytestmark = pytest.mark.django_db


class _Envelope:
    def __init__(self, event_type="deployment.succeeded", org=None, team_id=None, app_id=None):
        self.event_type = event_type
        self.organization_id = org
        self.team_id = team_id
        self.registered_app_id = app_id
        self.payload = {"hello": "world"}
        self.guid = "01abc"


@pytest.fixture
def org(db):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Acme", slug="acme")


def _sub(org, **over):
    from astrolift_operations.models import WebhookSubscription

    fields = {
        "organization": org,
        "url": "https://hooks.example.test/a",
        "secret_hash": "s3cr3t",
        "events": [],
        "is_active": True,
    }
    fields.update(over)
    return WebhookSubscription.objects.create(**fields)


# ---- matching -----------------------------------------------------------


def test_an_active_subscription_receives_its_org_events(org):
    _sub(org)

    assert len(matching_subscriptions(_Envelope(org=org.pk))) == 1


def test_an_inactive_subscription_receives_nothing(org):
    _sub(org, is_active=False)

    assert matching_subscriptions(_Envelope(org=org.pk)) == []


def test_an_event_with_no_organization_matches_nothing(org):
    """Platform-level events exist -- Event.organization is nullable -- and
    there is no org whose subscribers should see them."""
    _sub(org)

    assert matching_subscriptions(_Envelope(org=None)) == []


def test_another_orgs_event_is_not_delivered(org):
    """The one that would be a disclosure rather than a bug."""
    from astrolift_identity.models import Organization

    other = Organization.objects.create(name="Other", slug="other")
    _sub(org)

    assert matching_subscriptions(_Envelope(org=other.pk)) == []


def test_an_app_scoped_subscription_ignores_other_apps(org):
    """Real app rows, not invented ids: the FK is enforced, and a test that
    can only pass against a fake id is testing the fake."""
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp

    team = Team.objects.create(organization=org, name="Core", slug="core")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p")
    mine = RegisteredApp.objects.create(
        organization=org, team=team, project=project, name="mine", slug="mine"
    )
    theirs = RegisteredApp.objects.create(
        organization=org, team=team, project=project, name="theirs", slug="theirs"
    )

    _sub(org, registered_app=mine)

    assert matching_subscriptions(_Envelope(org=org.pk, app_id=theirs.pk)) == []
    assert len(matching_subscriptions(_Envelope(org=org.pk, app_id=mine.pk))) == 1


# ---- the allow-list -----------------------------------------------------


def test_an_empty_allow_list_means_everything():
    """What the API's own default produces, and what an operator who left
    the field alone expects."""
    assert _wants(_Sub([]), "anything.at.all")


def test_an_exact_event_type_matches():
    assert _wants(_Sub(["deployment.succeeded"]), "deployment.succeeded")
    assert not _wants(_Sub(["deployment.succeeded"]), "deployment.failed")


def test_a_prefix_wildcard_matches():
    """Without this a subscriber has to enumerate every event type the
    platform will ever emit, and silently miss the ones added later."""
    assert _wants(_Sub(["deployment.*"]), "deployment.started")
    assert _wants(_Sub(["deployment.*"]), "deployment.succeeded")
    assert not _wants(_Sub(["deployment.*"]), "pipeline.started")


def test_a_bare_star_matches_everything():
    assert _wants(_Sub(["*"]), "whatever")


class _Sub:
    def __init__(self, events):
        self.events = events
