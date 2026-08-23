"""
Pipeline TOML fetch resolves a real credential (#1608).

The path this covers could never succeed. ``_get_github_token`` and
``_get_gitlab_token`` read ``conn.access_token_ciphertext``, a column
SourceConnection has never had, and handed it to ``core.encryption``,
a module that does not exist. Both lookups sat inside a bare ``except``,
so a private-repo fetch reported "no credentials configured" and the two
real defects stayed invisible. Every private-repo pipeline trigger has
failed the same way since #105.

These tests pin the behaviour to the connection resolver, which is the
component that already owns the question the old code answered with
``.first()``: which connection wins when an org has several.
"""

from __future__ import annotations

from unittest import mock

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.toml_fetcher import (
    TomlFetchError,
    _connection_token,
    _org_connection,
)
from astrolift_scm.models import SourceConnection

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-1608")


def _connection(org, kind: str, **over):
    fields = {
        "organization": org,
        "kind": kind,
        "display_name": kind,
        "secret_backend_kind": "local_fernet",
        "secret_ciphertext": b"sealed",
        "is_active": True,
    }
    fields.update(over)
    return SourceConnection.objects.create(**fields)


def test_a_pat_connection_resolves_to_its_decrypted_token(org):
    _connection(org, "github_pat")

    connection = _org_connection(org, source_kind="github")
    with mock.patch("astrolift_scm.providers.github._token", return_value="ghp_live") as accessor:
        assert _connection_token(connection, source_kind="github") == "ghp_live"

    # The provider driver is the only code that knows how each connection
    # kind stores its secret; going around it is what produced #1608.
    accessor.assert_called_once_with(connection)


def test_an_app_install_outranks_a_pat(org):
    """The ranking the old ``.first()`` lookup could not express.

    An org with both must authenticate as the App installation, not as
    whichever row Postgres returned first.
    """
    _connection(org, "github_pat")
    _connection(org, "github_app_install", installation_id="987")

    assert _org_connection(org, source_kind="github").kind == "github_app_install"


def test_no_connection_raises_with_the_resolver_reason(org):
    """The old code returned None here, which the caller reported as
    'no credentials configured' whether the org had none, had an
    unusable one, or had one the dead lookup could not read. Only the
    first was ever true."""
    with pytest.raises(TomlFetchError) as excinfo:
        _org_connection(org, source_kind="github")

    assert "GitHub" in str(excinfo.value)


def test_an_unusable_credential_is_reported_not_swallowed(org):
    """A connection that exists but cannot mint a token must fail loudly.

    Silently returning None is what let two independent defects hide
    behind one misleading message for the life of the feature.
    """
    _connection(org, "github_pat")
    connection = _org_connection(org, source_kind="github")

    with mock.patch("astrolift_scm.providers.github._token", side_effect=RuntimeError("key rotated out")):
        with pytest.raises(TomlFetchError, match="key rotated out"):
            _connection_token(connection, source_kind="github")


def test_an_empty_credential_is_rejected(org):
    _connection(org, "github_pat")
    connection = _org_connection(org, source_kind="github")

    with mock.patch("astrolift_scm.providers.github._token", return_value=""):
        with pytest.raises(TomlFetchError, match="no usable credential"):
            _connection_token(connection, source_kind="github")


def test_gitlab_resolves_through_its_own_provider(org):
    _connection(org, "gitlab_pat")

    connection = _org_connection(org, source_kind="gitlab")
    with mock.patch("astrolift_scm.providers.gitlab._token", return_value="glpat_live") as accessor:
        assert _connection_token(connection, source_kind="gitlab") == "glpat_live"

    accessor.assert_called_once_with(connection)


def test_gitlab_fetch_uses_the_connections_api_base_not_the_repo_host(org):
    """A self-hosted GitLab can serve its API somewhere other than
    ``https://<repo-host>/api/v4``. The old code derived the host from the
    repo URL and could only ever reach the guess."""
    from astrolift_scm.providers.gitlab import _api_base

    connection = _connection(org, "gitlab_pat", api_base_url="https://git.internal.example/api-gateway")

    assert _api_base(connection) == "https://git.internal.example/api-gateway"


def test_the_dead_field_and_module_are_gone():
    """Anti-rot, over the AST rather than the text.

    Both breaks were invisible because a bare ``except`` turned each into
    a None return, so reintroducing either would be silent again. Reading
    the parse tree rather than the source means the docstrings above,
    which name both dead symbols on purpose, do not fake a pass or a fail.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("astrolift_pipelines/toml_fetcher.py").read_text())

    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "access_token_ciphertext" not in attributes, (
        "SourceConnection has no such column; its secret lives in "
        "secret_ciphertext behind the provider driver"
    )

    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names
    }
    assert "core.encryption" not in modules, "the envelope module is core.secrets"


def test_the_anti_rot_check_can_actually_fail():
    """A matcher that matches nothing passes over an empty set."""
    import ast

    tree = ast.parse("conn.access_token_ciphertext\nfrom core.encryption import decrypt\n")
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "access_token_ciphertext" in attributes
    assert "core.encryption" in modules
