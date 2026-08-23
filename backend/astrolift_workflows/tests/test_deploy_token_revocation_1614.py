"""
Deploy tokens are revoked when their app is torn down (#1614).

``_revoke_app_deploy_tokens_sync`` imported ``DeployToken`` from
``astrolift_identity.models``. The model lives in
``astrolift_lifecycle``, and the import sat inside
``except ImportError: return 0`` described as "the identity app may not
be loaded in every backend slice". So the activity returned 0 on every
teardown, reported success, and left every deploy token for the deleted
app live and usable.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import DeployToken
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.app_teardown import _revoke_app_deploy_tokens_sync

pytestmark = pytest.mark.django_db


@pytest.fixture
def app():
    org = Organization.objects.create(name="Acme", slug="acme-1614t")
    team = Team.objects.create(organization=org, name="Platform", slug="platform")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    return RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Hello",
        slug="hello",
        provisioning_status="ready",
    )


def _token(app, name: str) -> DeployToken:
    return DeployToken.objects.create(registered_app=app, name=name, token_hash=f"hash-{name}")


def test_every_live_token_for_the_app_is_revoked(app):
    _token(app, "ci")
    _token(app, "cron")

    assert _revoke_app_deploy_tokens_sync(app.pk) == 2
    assert DeployToken.objects.filter(registered_app=app).count() == 0


def test_revocation_is_soft_so_the_forensic_trail_survives(app):
    """The token row is what an operator correlates a leaked credential
    against; hard-deleting it destroys the last_used_ip / last_used_agent
    columns #425 added for exactly that."""
    token = _token(app, "ci")

    _revoke_app_deploy_tokens_sync(app.pk)

    assert DeployToken.all_objects.get(pk=token.pk).deleted_at is not None


def test_a_revoked_token_no_longer_verifies(app):
    """The behaviour that matters. The activity's own docstring claims
    soft delete is sufficient because the auth lookup filters on it -- this
    is the test that the claim is true rather than assumed."""
    from astrolift_lifecycle.deploy_tokens import issue_token, verify_token

    _row, plaintext = issue_token(app=app, name="ci")
    assert verify_token(plaintext) is not None

    _revoke_app_deploy_tokens_sync(app.pk)

    assert verify_token(plaintext) is None


def test_another_apps_tokens_are_untouched(app):
    other = RegisteredApp.objects.create(
        organization=app.organization,
        project=app.project,
        team=app.team,
        name="Other",
        slug="other",
        provisioning_status="ready",
    )
    _token(app, "ci")
    keep = _token(other, "ci")

    assert _revoke_app_deploy_tokens_sync(app.pk) == 1
    assert DeployToken.objects.filter(pk=keep.pk).exists()


def test_an_app_with_no_tokens_reports_zero(app):
    assert _revoke_app_deploy_tokens_sync(app.pk) == 0
