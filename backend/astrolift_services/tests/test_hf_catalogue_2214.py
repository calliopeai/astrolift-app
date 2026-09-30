"""Real authentication and bounded public HTTP catalogue contracts."""

import io
import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client
from django.utils import timezone
from graphql import GraphQLError

from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_services import hf_catalogue as hf
from astrolift_services.schema.model_reads import ModelReadsQuery
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, make_info, make_user

pytestmark = pytest.mark.django_db
SHA = "a" * 40


class Response(io.BytesIO):
    def __init__(self, payload, link=""):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers = {"Link": link}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *a: None))
    w = ScopeWorld("hf2214")
    w.user = make_user("hf2214")
    w.calls = []
    w.payload = [
        {
            "id": "org/model",
            "sha": SHA,
            "gated": "manual",
            "cardData": {"license": "apache-2.0"},
            "config": {"architectures": ["TestArchitecture"]},
            "downloads": 3_000_000_000,
        }
    ]
    w.link = ""

    def open_request(request, timeout):
        w.calls.append((request, timeout))
        if isinstance(w.payload, Exception):
            raise w.payload
        return Response(w.payload, w.link)

    monkeypatch.setattr(hf, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    return w


@contextmanager
def caller(w, token=None):
    current = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk)):
            yield
    finally:
        reset_current_api_token(current)


def search(w, **kwargs):
    with caller(w):
        return ModelReadsQuery().astrolift_hugging_face_models(make_info(w.user), **kwargs)


def test_fixed_anonymous_public_wire_filters_and_truthful_identity(world, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "never-forward-this-secret")
    page = search(
        world,
        search="model",
        author="org",
        library="transformers",
        pipeline_tag="text-generation",
        license="apache-2.0",
        gated=True,
        first=2,
    )
    assert page.state == hf.CatalogueState.AVAILABLE and page.source == "huggingface_public_api"
    item = page.items[0]
    assert item.repo_id == "org/model" and item.revision_sha == SHA
    assert item.license == "apache-2.0" and item.gated == hf.ModelGating.MANUAL
    assert item.compatibility == hf.ModelCompatibility.UNKNOWN and item.architectures == ["TestArchitecture"]
    assert item.downloads == 3_000_000_000
    request, timeout = world.calls[0]
    url = urlsplit(request.full_url)
    assert (url.scheme, url.netloc, url.path) == ("https", "huggingface.co", "/api/models")
    params = parse_qs(url.query)
    assert params["filter"] == ["transformers", "license:apache-2.0"] and params["gated"] == ["true"]
    assert "config" in params["expand"] and timeout == 5
    assert request.get_header("Authorization") is None


def test_cursor_follows_actual_next_page_without_repeating_and_binds_filters_and_identity(world):
    filters = hf.CatalogueFilters(first=1)
    world.link = f'<https://huggingface.co/api/models?{urlencode(filters.params() + [("cursor", "server-cursor")])}>; rel="next"'
    first = search(world, first=1)
    assert first.next_cursor
    world.payload = [{"id": "org/other"}]
    world.link = ""
    second = search(world, first=1, after=first.next_cursor)
    assert second.items[0].repo_id == "org/other" and second.items[0].revision_sha is None
    assert second.items[0].gated == hf.ModelGating.UNKNOWN and second.next_cursor is None
    assert parse_qs(urlsplit(world.calls[-1][0].full_url).query)["cursor"] == ["server-cursor"]
    for kwargs in [{"first": 2}, {"first": 1, "search": "changed"}]:
        with pytest.raises(GraphQLError, match="invalid or expired catalogue cursor"):
            search(world, after=first.next_cursor, **kwargs)
    other = make_user("hf2214-other")
    with caller(world), pytest.raises(GraphQLError, match="invalid or expired catalogue cursor"):
        ModelReadsQuery().astrolift_hugging_face_models(make_info(other), first=1, after=first.next_cursor)
    assert len(world.calls) == 2


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.invalid/api/models",
        "http://huggingface.co/api/models",
        "https://huggingface.co/api/whoami",
        "https://huggingface.co:443/api/models",
        "https://huggingface.co/api/models?cursor=x&limit=1000",
    ],
)
def test_upstream_pagination_cannot_redirect_or_widen_filters(world, link):
    world.link = f'<{link}>; rel="next"'
    assert search(world).state == hf.CatalogueState.UNAVAILABLE
    assert len(world.calls) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"first": 31},
        {"first": 0},
        {"author": "../admin"},
        {"license": "mit&token=secret"},
        {"search": "x" * 121},
        {"search": "bad\nquery"},
        {"sort_by": "made-up"},
        {"after": "tampered"},
    ],
)
def test_invalid_arguments_fail_before_http(world, kwargs):
    with pytest.raises(GraphQLError):
        search(world, **kwargs)
    assert not world.calls


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"x" * (hf.MAX_BODY_BYTES + 1),
        {"unexpected": []},
        [{"id": "../../config"}],
        [{"id": "org/model"}, {"id": "org/model"}],
        URLError("private diagnostic"),
    ],
    ids=["invalid-json", "oversize", "not-array", "invalid-id", "duplicates", "unreachable"],
)
def test_invalid_or_unreachable_catalogue_is_not_simulated_success(world, payload):
    world.payload = payload
    page = search(world)
    assert page.state == hf.CatalogueState.UNAVAILABLE and page.items == [] and page.next_cursor is None


def test_no_data_and_unknown_metadata_are_not_fabricated(world):
    world.payload = []
    assert search(world).state == hf.CatalogueState.NO_DATA
    world.payload = [{"id": "org/model", "sha": "bad", "gated": True}]
    item = search(world).items[0]
    assert item.revision_sha is None and item.license is None and item.downloads is None
    assert item.gated == hf.ModelGating.UNKNOWN and item.architectures == []
    world.payload = [{"id": "org/private", "private": True}, {"id": "org/disabled", "disabled": True}]
    assert search(world).items == []


def test_rate_limit_reports_only_bounded_numeric_retry(world):
    world.payload = HTTPError(
        "https://huggingface.co/api/models", 429, "limited", {"Retry-After": "90"}, None
    )
    page = search(world)
    assert page.state == hf.CatalogueState.RATE_LIMITED and page.retry_after_seconds == 90


@pytest.mark.parametrize("changed", ["missing-sha", "mismatched-sha", "other-repo"])
def test_detail_requires_actual_immutable_identity(world, changed):
    world.payload = {"id": "org/model", "sha": SHA}
    if changed == "missing-sha":
        del world.payload["sha"]
    elif changed == "mismatched-sha":
        world.payload["sha"] = "b" * 40
    else:
        world.payload["id"] = "org/other"
    with caller(world):
        result = ModelReadsQuery().astrolift_hugging_face_model(make_info(world.user), "org/model", SHA)
    assert result.state == hf.CatalogueState.UNAVAILABLE and result.model is None


def test_branch_resolves_actual_sha_and_never_fetches_or_executes_model_files(world):
    world.payload = {"id": "org/model", "sha": SHA}
    with caller(world):
        result = ModelReadsQuery().astrolift_hugging_face_model(
            make_info(world.user), "org/model", "refs/pr/2"
        )
    assert result.model.revision_sha == SHA
    assert "/api/models/org/model/revision/refs%2Fpr%2F2?" in world.calls[0][0].full_url
    assert len(world.calls) == 1


@pytest.mark.parametrize("user_state", ["anonymous", "inactive"])
def test_authentication_denies_before_http_even_for_catalogue(world, user_state):
    user = AnonymousUser() if user_state == "anonymous" else world.user
    if user_state == "inactive":
        type(user).objects.filter(pk=user.pk).update(is_active=False)
    with caller(world), pytest.raises(GraphQLError, match="Authentication required"):
        ModelReadsQuery().astrolift_hugging_face_models(make_info(user))
    assert not world.calls


def test_revoked_bearer_continuation_rechecks_authentication(world):
    from astrolift_identity.models import Member

    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="catalogue",
        token_hash=minted.token_hash,
        scopes=["read:apps"],
    )
    plaintext = minted.plaintext
    client = Client()
    query = "{astroliftHuggingFaceModels(first:1){state items{repoId revisionSha compatibility} source}}"
    first = client.post(
        "/app/gql/config/",
        json.dumps({"query": query}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
        HTTP_X_ASTROLIFT_ORG=str(world.org.guid),
    )
    assert first.json()["data"]["astroliftHuggingFaceModels"]["items"][0]["repoId"] == "org/model"
    type(token).objects.filter(pk=token.pk).update(is_revoked=True)
    second = client.post(
        "/app/gql/config/",
        json.dumps({"query": query}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
        HTTP_X_ASTROLIFT_ORG=str(world.org.guid),
    )
    assert second.status_code in (401, 403) or second.json().get("errors")
    assert len(world.calls) == 1
    with caller(world, token), pytest.raises(GraphQLError, match="Authentication required"):
        ModelReadsQuery().astrolift_hugging_face_models(make_info(world.user))


def test_expired_token_direct_read_refuses_before_http(world):
    from astrolift_identity.models import Member

    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="catalogue",
        token_hash=minted.token_hash,
        scopes=["read:apps"],
    )
    type(token).objects.filter(pk=token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with caller(world, token), pytest.raises(GraphQLError, match="Authentication required"):
        ModelReadsQuery().astrolift_hugging_face_models(make_info(world.user))
    assert not world.calls


def test_discovery_advertises_support_without_publishing_catalogue_data(world):
    from config.schema_public import schema_public as public_schema
    from core.capabilities_registry import SHIPPED_CAPABILITIES

    assert "models.hugging_face_catalogue" in SHIPPED_CAPABILITIES
    assert "astroliftHuggingFaceModels" not in public_schema.as_str()
    assert "astroliftHuggingFaceModel" not in public_schema.as_str()
    assert hf._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.invalid") is None
    assert not world.calls
