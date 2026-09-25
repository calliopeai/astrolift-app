"""API-served manifest digests are keyed, and secret files are withheld (#1993)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from astrolift_registry.services.staged_manifest import public_manifest_hash
from astrolift_services.secret_visibility import _SECRET_FILE_PLACEHOLDER, redacted_repo_file


def test_the_served_hash_is_not_the_unkeyed_digest_and_still_compares():
    app = SimpleNamespace(guid="a1")
    raw = hashlib.sha256(b'[env]\nPIN = "1234"\n').hexdigest()

    served = public_manifest_hash(app, raw)

    assert served and served != raw
    assert served == public_manifest_hash(app, raw)
    assert served != public_manifest_hash(SimpleNamespace(guid="a2"), raw)
    assert public_manifest_hash(app, "") == ""


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/.env.production",
        "infra/prod.tfvars",
        "certs/server.key",
        "secrets.yaml",
        "deploy/id_rsa",
    ],
)
def test_secret_files_are_withheld_from_a_caller_who_cannot_reveal(path, monkeypatch):
    monkeypatch.setattr("astrolift_services.secret_visibility.can_reveal_org_secrets", lambda info: False)
    assert redacted_repo_file(None, path=path, content="TOKEN=abc123\n") == _SECRET_FILE_PLACEHOLDER


def test_ordinary_files_and_revealers_are_unchanged(monkeypatch):
    monkeypatch.setattr("astrolift_services.secret_visibility.can_reveal_org_secrets", lambda info: False)
    assert redacted_repo_file(None, path="README.md", content="# hi\n") == "# hi\n"
    monkeypatch.setattr("astrolift_services.secret_visibility.can_reveal_org_secrets", lambda info: True)
    assert redacted_repo_file(None, path=".env", content="TOKEN=abc123\n") == "TOKEN=abc123\n"
