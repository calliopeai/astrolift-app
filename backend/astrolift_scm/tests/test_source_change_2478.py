"""Which repository an app may be pointed at (#2478).

Only GitHub is called through a fake ``urlopen``; the connections are real
rows, so the installation is chosen the way the wiring will choose it.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.source_change import (
    SourceChangeRefused,
    installed_repository,
    parse_github_repo,
)
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Conflict", slug="conflict-2478")


def _connection(
    org, *, kind=SourceConnection.Kind.GITHUB_APP_INSTALL, login="ConflictHQ", installation="987"
):
    encrypted = encrypt_at_rest(b"fake-pem")
    return SourceConnection.objects.create(
        organization=org,
        kind=kind,
        account_login=login,
        installation_id=installation if kind == SourceConnection.Kind.GITHUB_APP_INSTALL else "",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def github(monkeypatch):
    """Each installation lists its repositories; every request is recorded
    with the installation token it carried."""
    listings: dict[str, list[str]] = {}
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: f"token-{connection.installation_id}",
    )

    def urlopen(request, timeout=None):
        token = request.get_header("Authorization").removeprefix("Bearer token-")
        calls.append((token, request.full_url))
        if "/installation/repositories" not in request.full_url:
            raise AssertionError(f"unexpected GitHub call {request.full_url}")
        names = listings.get(token, [])
        return _Response({"total_count": len(names), "repositories": [{"full_name": n} for n in names]})

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", urlopen)
    return listings, calls


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ConflictHQ/fossilrepos", "ConflictHQ/fossilrepos"),
        ("  octo_org/repo.name-2\n", "octo_org/repo.name-2"),
    ],
)
def test_an_owner_and_a_name_are_accepted(value, expected):
    assert parse_github_repo(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "fossilrepos",
        "ConflictHQ/",
        "/fossilrepos",
        "https://github.com/ConflictHQ/fossilrepos",
        "git@github.com:ConflictHQ/fossilrepos.git",
        "ConflictHQ/fossil/repos",
        "ConflictHQ/..",
        "../fossilrepos",
        "Conflict HQ/fossilrepos",
        "ConflictHQ/fossil?repos",
        "o/" + "r" * 254,
    ],
)
def test_anything_but_owner_slash_name_is_refused(value):
    with pytest.raises(SourceChangeRefused) as refused:
        parse_github_repo(value)

    assert refused.value.code == "VALIDATION"
    assert "owner/name" in refused.value.message


def test_the_name_github_lists_is_returned(org, github):
    listings, calls = github
    _connection(org)
    listings["987"] = ["ConflictHQ/fossilhub-docs", "ConflictHQ/FossilRepos"]

    assert installed_repository(org.pk, "conflicthq/fossilrepos") == "ConflictHQ/FossilRepos"
    assert [token for token, _ in calls] == ["987"]


def test_a_repository_the_installation_does_not_include_is_refused(org, github):
    listings, _ = github
    _connection(org)
    listings["987"] = ["ConflictHQ/fossilhub"]

    with pytest.raises(SourceChangeRefused) as refused:
        installed_repository(org.pk, "ConflictHQ/fossilrepos")

    assert refused.value.code == "PRECONDITION"
    assert refused.value.message == (
        "ConflictHQ/fossilrepos is not in the GitHub App installation on ConflictHQ. "
        "Give the App access to the repository on GitHub, then retry."
    )


def test_the_installation_on_the_repository_owner_is_the_one_asked(org, github):
    listings, calls = github
    _connection(org, login="acme", installation="111")
    _connection(org, login="ConflictHQ", installation="222")
    listings["111"] = ["acme/site"]
    listings["222"] = ["ConflictHQ/fossilrepos"]

    assert installed_repository(org.pk, "ConflictHQ/fossilrepos") == "ConflictHQ/fossilrepos"
    assert [token for token, _ in calls] == ["222"]


@pytest.mark.parametrize("kind", [SourceConnection.Kind.GITHUB_OAUTH_USER, SourceConnection.Kind.GITHUB_PAT])
def test_without_a_github_app_installation_nothing_is_accepted(org, github, kind):
    _, calls = github
    _connection(org, kind=kind)

    with pytest.raises(SourceChangeRefused) as refused:
        installed_repository(org.pk, "ConflictHQ/fossilrepos")

    assert refused.value.code == "PRECONDITION"
    assert "GitHub App is not installed" in refused.value.message
    assert calls == []


def test_another_organizations_installation_is_not_used(org, github):
    listings, calls = github
    other = Organization.objects.create(name="Other", slug="other-2478")
    _connection(other)
    listings["987"] = ["ConflictHQ/fossilrepos"]

    with pytest.raises(SourceChangeRefused) as refused:
        installed_repository(org.pk, "ConflictHQ/fossilrepos")

    assert refused.value.code == "PRECONDITION"
    assert calls == []


def test_a_github_failure_is_reported_not_taken_for_an_answer(org, monkeypatch):
    _connection(org)
    monkeypatch.setattr("astrolift_scm.providers.github_app.installation_token", lambda connection: "t")

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, None)

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", urlopen)

    with pytest.raises(SourceChangeRefused) as refused:
        installed_repository(org.pk, "ConflictHQ/fossilrepos")

    assert refused.value.code == "PRECONDITION"
    assert refused.value.message == (
        "couldn't read the repositories of the GitHub App installation on ConflictHQ: GitHub returned 502"
    )
