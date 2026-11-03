"""A github_app_install row with no installation id finds its installation on
GitHub by account login, stores it, and mints a token. Without a match it
keeps the original INCOMPLETE_CONFIG error."""

from __future__ import annotations

import uuid

import pytest

from astrolift_scm.providers import github_app
from astrolift_scm.providers.github_app import GithubAppError, installation_token


class _Conn:
    def __init__(self, *, account_login="ConflictHQ", installation_id=""):
        self.guid = uuid.uuid4()
        self.kind = "github_app_install"
        self.app_client_id = "Iv23liKcR2jWTgzpuweb"
        self.oauth_client_id = ""
        self.account_login = account_login
        self.installation_id = installation_id
        self.saved_with: list[list[str]] = []

    def save(self, update_fields=None):
        self.saved_with.append(list(update_fields or []))


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(github_app, "_decrypt_pem", lambda connection: b"pem")
    monkeypatch.setattr(github_app, "_mint_jwt", lambda issuer, pem: "jwt")
    monkeypatch.setattr(github_app, "_api_base", lambda connection: "https://api.example")
    monkeypatch.setattr(
        github_app,
        "_exchange_for_installation_token",
        lambda api_base, jwt_token, installation_id: (f"tok-{installation_id}", 9_999_999_999.0),
    )

    def _set_installs(installs, err=None):
        monkeypatch.setattr(
            github_app,
            "list_app_installations",
            lambda api_base, jwt_token: (installs, err),
        )

    return _set_installs


def test_discovers_and_stores_installation_for_account(stub):
    stub(
        [
            {"id": 158055468, "account": {"login": "ConflictHQ"}},
            {"id": 167938702, "account": {"login": "calliopeai"}},
        ]
    )
    conn = _Conn(account_login="ConflictHQ")

    token = installation_token(conn)

    assert token == "tok-158055468"
    assert conn.installation_id == "158055468"
    assert conn.saved_with == [["installation_id", "updated_at", "version"]]


def test_account_match_is_case_insensitive(stub):
    stub([{"id": 42, "account": {"login": "conflicthq"}}])
    conn = _Conn(account_login="ConflictHQ")

    assert installation_token(conn) == "tok-42"


def test_no_installation_for_account_keeps_config_error(stub):
    stub([{"id": 167938702, "account": {"login": "calliopeai"}}])
    conn = _Conn(account_login="ConflictHQ")

    with pytest.raises(GithubAppError) as exc:
        installation_token(conn)
    assert exc.value.code == "INCOMPLETE_CONFIG"
    assert conn.installation_id == ""
    assert conn.saved_with == []


def test_lookup_failure_keeps_config_error(stub):
    stub([], err=GithubAppError("NETWORK", "down"))
    conn = _Conn(account_login="ConflictHQ")

    with pytest.raises(GithubAppError) as exc:
        installation_token(conn)
    assert exc.value.code == "INCOMPLETE_CONFIG"


def test_row_with_installation_skips_discovery(stub, monkeypatch):
    def _boom(api_base, jwt_token):
        raise AssertionError("discovery must not run when the installation is known")

    monkeypatch.setattr(github_app, "list_app_installations", _boom)
    conn = _Conn(account_login="ConflictHQ", installation_id="158055468")

    assert installation_token(conn) == "tok-158055468"
