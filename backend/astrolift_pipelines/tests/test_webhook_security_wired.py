"""The pipeline webhook receivers enforce the security layer (#89).

`webhook_security.py` held four controls and had no caller. Only one of the
four was implemented elsewhere: both receivers verified signatures with
their own copy of the logic. The other three did not exist anywhere, so
until now:

* a captured delivery could be **replayed** indefinitely to re-trigger runs;
* nothing bounded **how many** runs a webhook could create;
* nothing bounded the **payload size** the endpoint would HMAC.

Two flaws were fixed rather than wired as written.

**Ordering.** `full_security_check_github` ran the rate limit *before*
verifying the signature. `org_slug` comes from the URL, so anyone who knew
an org's slug could spend its 300/minute budget and lock out that org's real
webhooks, unauthenticated. The receivers now order the checks explicitly:
size, signature, rate, replay.

**Atomicity.** The limiter did `cache.get` then `cache.set` -- a
read-modify-write, so concurrent workers all read the same count and all
passed, precisely under the load a rate limit exists for.

Also fixed here: the GitLab receiver recorded **no** metrics at all, so
`astrolift_pipeline_webhook_deliveries_total` and the signature-failure
counter covered GitHub only. That is a gap in #1585, which wired
`webhook_views` and not its GitLab sibling.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import inspect
import itertools
import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from freezegun import freeze_time
from prometheus_client import REGISTRY

from astrolift_identity.models import Organization
from astrolift_pipelines import webhook_security
from astrolift_pipelines.gitlab_webhook_views import pipeline_gitlab_webhook
from astrolift_pipelines.webhook_views import pipeline_github_webhook

pytestmark = pytest.mark.django_db

_n = itertools.count(1)
_SECRET = b"s3cret"

# RequestFactory + a direct view call, matching `test_webhook.py`. The
# Django test client runs the middleware stack, and the request-id
# middleware raises "Token has already been used once" when two requests
# are issued inside one test -- which the replay test needs by definition.
_factory = RequestFactory()


def _post(view, org_slug, body=b"{}", **headers):
    if view is pipeline_github_webhook:
        headers.setdefault("HTTP_X_GITHUB_DELIVERY", "disposable-security-proof")
    request = _factory.post(
        f"/webhooks/pipelines/{org_slug}/",
        data=body,
        content_type="application/json",
        **headers,
    )
    return view(request, org_slug)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def org(monkeypatch):
    """Patches the secret-resolver seam, the same way `test_webhook.py` does.

    Not a convenience: `_get_org_pipeline_secret` cannot return a secret in
    any deployment right now. `Organization.extra_data` (the dev fallback)
    was dropped, and the production path imports
    `astrolift_lifecycle.services.secrets`, which does not exist -- the same
    missing module that blocks `pipeline_secrets.py` on #1580. So it always
    returns None and **every pipeline webhook delivery is answered 401**.

    That is recorded on the epic, not fixed here. It does not affect what
    this PR wires: the size check runs before the secret lookup, and the
    rate limit and replay checks sit after verification by design. Both
    receivers' seams are patched because the GitLab module has its own copy
    of the lookup.
    """
    o = Organization.objects.create(name="Acme", slug=f"acme-hook-{next(_n)}")
    from astrolift_pipelines import gitlab_webhook_views, webhook_views

    # GitHub's verifier takes bytes (HMAC key); GitLab's takes a str and
    # encodes it itself. Two seams, two types -- another consequence of the
    # duplicated lookup.
    monkeypatch.setattr(
        webhook_views,
        "_get_org_pipeline_secret",
        lambda o, _s=o.slug: _SECRET if o.slug == _s else None,
    )
    monkeypatch.setattr(
        gitlab_webhook_views,
        "_get_org_pipeline_secret",
        lambda o, _s=o.slug: _SECRET.decode() if o.slug == _s else None,
    )
    return o


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(_SECRET, body, hashlib.sha256).hexdigest()


def _push_body() -> bytes:
    return json.dumps(
        {"ref": "refs/heads/main", "after": "a" * 40, "repository": {"clone_url": "x", "html_url": "y"}}
    ).encode()


def _sample(name: str, **labels) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------


def test_a_repeated_filtered_delivery_remains_safe_without_ephemeral_acknowledgement(org):
    """The control that did not exist: without it, one captured delivery
    re-triggers a pipeline as often as it is replayed."""
    body = _push_body()
    headers = {
        "HTTP_X_GITHUB_EVENT": "push",
        "HTTP_X_HUB_SIGNATURE_256": _sign(body),
        "HTTP_X_GITHUB_DELIVERY": "delivery-1",
    }
    first = _post(pipeline_github_webhook, org.slug, body, **headers)
    second = _post(pipeline_github_webhook, org.slug, body, **headers)

    assert first.status_code == 200
    # 200, not 4xx: the delivery really was processed, and a non-2xx would
    # make GitHub retry it forever.
    assert second.status_code == 200
    assert json.loads(second.content)["status"] == "ok"


def test_a_delivery_without_an_id_is_not_deduplicated(org):
    """GitLab does not reliably send one, and rejecting an absent id would
    reject every GitLab delivery."""
    webhook_security.check_replay("", org.slug)
    webhook_security.check_replay("", org.slug)


def test_two_orgs_can_use_the_same_delivery_id(org):
    other = Organization.objects.create(name="Other", slug=f"other-hook-{next(_n)}")

    webhook_security.check_replay("shared-id", org.slug)
    webhook_security.check_replay("shared-id", other.slug)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_the_limiter_counts_atomically(org):
    """`add` + `incr`, not `get` + `set`. The old read-modify-write let
    concurrent workers all observe the same count and all pass."""
    src = inspect.getsource(webhook_security.check_webhook_rate_limit)

    assert "cache.incr" in src
    assert "cache.get" not in src


def test_the_limit_is_enforced(org):
    limit = webhook_security._WEBHOOK_RATE_LIMIT_PER_MINUTE

    with freeze_time("2026-01-01 12:00:59") as clock:
        for _ in range(limit):
            webhook_security.check_webhook_rate_limit(org.slug)

        with pytest.raises(webhook_security.WebhookSecurityError):
            webhook_security.check_webhook_rate_limit(org.slug)

        clock.tick(timedelta(seconds=1))
        webhook_security.check_webhook_rate_limit(org.slug)


@freeze_time("2026-01-01 12:00:30")
def test_the_limit_is_per_org(org):
    other = Organization.objects.create(name="Other", slug=f"other-rate-{next(_n)}")
    for _ in range(webhook_security._WEBHOOK_RATE_LIMIT_PER_MINUTE):
        webhook_security.check_webhook_rate_limit(org.slug)

    # The other org is untouched -- otherwise one noisy tenant silences
    # everyone else's pipelines.
    webhook_security.check_webhook_rate_limit(other.slug)


def test_a_rate_limited_request_answers_429(org, monkeypatch):
    monkeypatch.setattr(webhook_security, "_WEBHOOK_RATE_LIMIT_PER_MINUTE", 0)
    body = _push_body()

    response = _post(
        pipeline_github_webhook,
        org.slug,
        body,
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_HUB_SIGNATURE_256=_sign(body),
        HTTP_X_GITHUB_DELIVERY="d-rate",
    )

    assert response.status_code == 429


# ---------------------------------------------------------------------------
# Ordering: the flaw that made the rate limit a denial lever
# ---------------------------------------------------------------------------


@freeze_time("2026-01-01 12:00:30")
def test_an_unsigned_request_does_not_spend_the_orgs_rate_budget(org):
    """The security-relevant ordering.

    `org_slug` is in the URL. If the limiter ran before verification, anyone
    who knew a slug could burn that org's 300/minute and lock out its real
    webhooks without ever holding the secret.
    """
    body = _push_body()

    for _ in range(50):
        response = _post(
            pipeline_github_webhook,
            org.slug,
            body,
            HTTP_X_GITHUB_EVENT="push",
            HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64,
            HTTP_X_GITHUB_DELIVERY="d-attack",
        )
        assert response.status_code == 401

    # The budget must be untouched: 50 forged requests spent nothing.
    for _ in range(webhook_security._WEBHOOK_RATE_LIMIT_PER_MINUTE):
        webhook_security.check_webhook_rate_limit(org.slug)


def test_the_wrappers_that_had_the_wrong_order_are_gone():
    """Pinned so the mis-ordered convenience wrappers do not come back."""
    assert not hasattr(webhook_security, "full_security_check_github")
    assert not hasattr(webhook_security, "full_security_check_gitlab")


# ---------------------------------------------------------------------------
# Payload size
# ---------------------------------------------------------------------------


def test_an_oversized_payload_is_refused_before_the_hmac(org, monkeypatch):
    """Size first: HMAC over an arbitrarily large body is work an
    unauthenticated caller should not be able to demand."""
    monkeypatch.setattr(webhook_security, "_MAX_PAYLOAD_BYTES", 10)

    response = _post(
        pipeline_github_webhook,
        org.slug,
        b"x" * 100,
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64,
    )

    assert response.status_code == 413


# ---------------------------------------------------------------------------
# The GitLab metrics gap
# ---------------------------------------------------------------------------


def test_a_gitlab_signature_failure_is_counted(org):
    """The GitLab receiver recorded nothing at all, so a token mismatch on
    that host was invisible on the very counter you would alert on."""
    before = _sample("astrolift_pipeline_webhook_signature_failures_total", org=org.slug, provider="gitlab")

    response = _post(
        pipeline_gitlab_webhook, org.slug, b"{}", HTTP_X_GITLAB_EVENT="Push Hook", HTTP_X_GITLAB_TOKEN="wrong"
    )

    assert response.status_code == 403
    after = _sample("astrolift_pipeline_webhook_signature_failures_total", org=org.slug, provider="gitlab")
    assert after == before + 1


def test_the_no_secret_branch_is_counted_too(org, monkeypatch):
    """The branch that actually executes in production.

    `_get_org_pipeline_secret` returns None in every deployment right now
    (`Organization.extra_data` was dropped, and the prod path imports a
    module that does not exist), so *this* is the path every real GitLab
    delivery takes. A mutation exposed that only the token-mismatch branch
    was covered -- the more important branch had no test at all, and it is
    the one whose counter tells an operator "no secret is configured".
    """
    from astrolift_pipelines import gitlab_webhook_views

    monkeypatch.setattr(gitlab_webhook_views, "_get_org_pipeline_secret", lambda o: None)
    before = _sample("astrolift_pipeline_webhook_signature_failures_total", org=org.slug, provider="gitlab")

    response = _post(
        pipeline_gitlab_webhook,
        org.slug,
        b"{}",
        HTTP_X_GITLAB_EVENT="Push Hook",
        HTTP_X_GITLAB_TOKEN="anything",
    )

    assert response.status_code == 403
    after = _sample("astrolift_pipeline_webhook_signature_failures_total", org=org.slug, provider="gitlab")
    assert after == before + 1


def test_a_gitlab_delivery_is_counted_under_its_own_provider_label(org):
    """The label exists so the two hosts are separable; recording GitLab as
    `github` would be worse than not recording it."""
    before = _sample(
        "astrolift_pipeline_webhook_deliveries_total",
        org=org.slug,
        provider="gitlab",
        outcome="filtered",
    )

    _post(
        pipeline_gitlab_webhook,
        org.slug,
        b"{}",
        HTTP_X_GITLAB_EVENT="Unhandled Hook",
        HTTP_X_GITLAB_TOKEN=_SECRET.decode(),
    )

    after = _sample(
        "astrolift_pipeline_webhook_deliveries_total",
        org=org.slug,
        provider="gitlab",
        outcome="filtered",
    )
    assert after == before + 1


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------

_RECEIVERS = (
    ("astrolift_pipelines/webhook_views.py", "pipeline_github_webhook"),
    ("astrolift_pipelines/gitlab_webhook_views.py", "pipeline_gitlab_webhook"),
)

_REQUIRED = frozenset({"check_payload_size", "check_webhook_rate_limit", "verify_signature"})


@pytest.mark.parametrize("module_path, view", _RECEIVERS)
def test_every_receiver_runs_the_security_checks(module_path, view):
    """A third receiver added without these is the way this goes dark again.

    `verify_signature` is in the required set as well as the two new checks,
    because `core/tests/test_webhook_signature_guard.py` only guarantees a
    verifier is called for views it can reach through the URL conf, and this
    is the pipeline-specific half of that promise.
    """
    import pathlib

    backend = pathlib.Path(webhook_security.__file__).resolve().parents[1]
    tree = ast.parse((backend / module_path).read_text())

    target = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == view),
        None,
    )
    assert target is not None, f"{view} is gone or renamed; this ratchet is now blind"

    called = {n.func.id for n in ast.walk(target) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    missing = sorted(_REQUIRED - called)
    assert not missing, f"{view} does not run {missing}"
