"""A pipeline webhook can actually authenticate (#1590 follow-up).

It could not, in any deployment. `_get_org_pipeline_secret` had two
sources and neither existed: `Organization.extra_data`, a field dropped
from the model, and `astrolift_lifecycle.services.secrets.read_org_secret`,
a module that was never written -- inside `except Exception: pass`. So it
returned None unconditionally and every delivery was answered 401 before
any payload was parsed. Webhooks are the primary trigger path for a
pipeline, so nothing a webhook was supposed to start could start.

There was never a missing capability. The secret already lives, encrypted,
on the org's `SourceConnection` (`webhook_secret_backend_kind` +
`webhook_secret_ciphertext`), written at App-install time by
`auth1/scm_app_manifest.py` and read by two other live receivers through the
same `decrypt` call. The pipeline receivers were reaching past a populated,
purpose-built column for a store that did not exist.

These tests do NOT patch the resolver seam. `test_webhook.py` does, for good
reason at the time -- but a patched seam is what let this sit unnoticed, so
the point here is to exercise the real one.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json

import pytest
from django.test import RequestFactory

from astrolift_identity.models import Organization
from astrolift_pipelines.gitlab_webhook_views import pipeline_gitlab_webhook
from astrolift_pipelines.webhook_security import org_webhook_secret
from astrolift_pipelines.webhook_views import pipeline_github_webhook
from astrolift_scm.models import SourceConnection

pytestmark = pytest.mark.django_db

_n = itertools.count(1)
_SECRET = b"webhook-shared-secret"
_factory = RequestFactory()


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-sec-{next(_n)}")


def _connection(org, kind: str, secret: bytes | None = _SECRET, **overrides):
    from core.secrets import encrypt_at_rest

    fields: dict = {
        "organization": org,
        "kind": kind,
        "account_login": "acme",
        "installation_id": "1234",
        "is_active": True,
    }
    if secret is not None:
        encrypted = encrypt_at_rest(secret)
        fields["webhook_secret_backend_kind"] = encrypted.backend_kind
        fields["webhook_secret_ciphertext"] = encrypted.backend_ref
    fields.update(overrides)
    return SourceConnection.objects.create(**fields)


def _post(view, org_slug, body=b"{}", **headers):
    if view is pipeline_github_webhook:
        headers.setdefault("HTTP_X_GITHUB_DELIVERY", "disposable-security-proof")
    request = _factory.post(
        f"/webhooks/pipelines/{org_slug}/", data=body, content_type="application/json", **headers
    )
    return view(request, org_slug)


def _sign(body: bytes, secret: bytes = _SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _push_body() -> bytes:
    return json.dumps(
        {
            "ref": "refs/heads/main",
            "after": "a" * 40,
            "repository": {
                "clone_url": "https://github.com/acme/app.git",
                "html_url": "https://github.com/acme/app",
            },
        }
    ).encode()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_the_secret_is_read_off_the_org_connection(org):
    _connection(org, SourceConnection.Kind.GITHUB_APP_INSTALL)

    assert org_webhook_secret(org, source_kind="github") == _SECRET


def test_no_connection_means_no_secret(org):
    assert org_webhook_secret(org, source_kind="github") is None


def test_a_connection_without_a_secret_is_skipped(org):
    _connection(org, SourceConnection.Kind.GITHUB_APP_INSTALL, secret=None)

    assert org_webhook_secret(org, source_kind="github") is None


def test_another_orgs_connection_is_not_used(org):
    other = Organization.objects.create(name="Other", slug=f"other-sec-{next(_n)}")
    _connection(other, SourceConnection.Kind.GITHUB_APP_INSTALL)

    assert org_webhook_secret(org, source_kind="github") is None


def test_a_gitlab_delivery_does_not_use_a_github_secret(org):
    """Cross-host confusion. The two hosts use the secret differently -- an
    HMAC key versus a bearer token -- so resolving the wrong one is not
    merely untidy."""
    _connection(org, SourceConnection.Kind.GITHUB_APP_INSTALL)

    assert org_webhook_secret(org, source_kind="gitlab") is None


def test_an_orphaned_connection_is_not_used(org):
    _connection(org, SourceConnection.Kind.GITHUB_APP_INSTALL, is_orphaned=True)

    assert org_webhook_secret(org, source_kind="github") is None


def test_the_app_installation_is_preferred_over_a_pat(org):
    """The manifest flow populates the App row; ranking it first keeps
    resolution stable for an org that has both."""
    _connection(org, SourceConnection.Kind.GITHUB_PAT, secret=b"pat-secret")
    _connection(org, SourceConnection.Kind.GITHUB_APP_INSTALL, secret=b"app-secret")

    assert org_webhook_secret(org, source_kind="github") == b"app-secret"


def test_an_undecryptable_row_does_not_mask_a_working_sibling(org):
    """A half-rotated or half-migrated row must not take the org's webhooks
    down when another connection still carries a usable secret.

    The bad row is the App install deliberately, so it sorts *first* and is
    genuinely tried before the good one. A mutation caught the earlier
    version of this test: with the bad row second, resolution returned the
    good secret before ever reaching it, so making the loop abort on its
    first failure changed nothing and the test still passed.
    """
    _connection(
        org,
        SourceConnection.Kind.GITHUB_APP_INSTALL,
        secret=None,
        webhook_secret_backend_kind="local_fernet",
        webhook_secret_ciphertext=b"not-valid-ciphertext",
    )
    _connection(org, SourceConnection.Kind.GITHUB_PAT, secret=b"good")

    assert org_webhook_secret(org, source_kind="github") == b"good"


# ---------------------------------------------------------------------------
# End to end: the thing that has never worked
# ---------------------------------------------------------------------------


def test_a_correctly_signed_github_delivery_is_accepted(org):
    """The whole point. Before this, a genuine signed delivery got 401
    because no secret could be resolved to check it against."""
    _connection(org, SourceConnection.Kind.GITHUB_APP_INSTALL)
    body = _push_body()

    response = _post(
        pipeline_github_webhook,
        org.slug,
        body,
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_HUB_SIGNATURE_256=_sign(body),
        HTTP_X_GITHUB_DELIVERY="d-1",
    )

    assert response.status_code == 200, response.content


def test_a_wrongly_signed_delivery_is_still_rejected(org):
    """The fix must not become "accept everything"."""
    _connection(org, SourceConnection.Kind.GITHUB_APP_INSTALL)
    body = _push_body()

    response = _post(
        pipeline_github_webhook,
        org.slug,
        body,
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_HUB_SIGNATURE_256=_sign(body, b"wrong-secret"),
        HTTP_X_GITHUB_DELIVERY="d-2",
    )

    assert response.status_code == 401


def test_an_org_with_no_connection_still_fails_closed(org):
    body = _push_body()

    response = _post(
        pipeline_github_webhook,
        org.slug,
        body,
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_HUB_SIGNATURE_256=_sign(body),
        HTTP_X_GITHUB_DELIVERY="d-3",
    )

    assert response.status_code == 401


def test_a_correct_gitlab_token_is_accepted(org):
    _connection(org, SourceConnection.Kind.GITLAB_PAT)

    response = _post(
        pipeline_gitlab_webhook,
        org.slug,
        json.dumps(
            {
                "object_kind": "push",
                "ref": "refs/heads/main",
                "project": {"http_url": "https://gitlab.com/acme/app"},
            }
        ).encode(),
        HTTP_X_GITLAB_EVENT="Push Hook",
        HTTP_X_GITLAB_TOKEN=_SECRET.decode(),
    )

    assert response.status_code == 200, response.content


def test_a_wrong_gitlab_token_is_rejected(org):
    _connection(org, SourceConnection.Kind.GITLAB_PAT)

    response = _post(
        pipeline_gitlab_webhook,
        org.slug,
        b"{}",
        HTTP_X_GITLAB_EVENT="Push Hook",
        HTTP_X_GITLAB_TOKEN="wrong",
    )

    assert response.status_code == 403
