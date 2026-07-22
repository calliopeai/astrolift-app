"""Cross-tenant isolation for the SCM secret-touching mutations (#1183).

``rotateWebhookSecret`` and ``deleteSshDeployKey`` looked their target row
up by guid with no ``organization_id`` constraint. Because a guid is
unguessable that raised the bar, but it was still a cross-tenant write
primitive: a caller who learned another org's connection/key guid could
mint a fresh webhook secret against it (rotating — and thereby breaking —
the victim's live webhook) or soft-delete their deploy key. These tests
prove the fetch is org-scoped: a foreign caller gets NOT_FOUND and the
victim row is left completely untouched (no secret minted, not deleted).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection, SshDeployKey
from astrolift_scm.schema.mutations import (
    DeleteSshDeployKeyInput,
    RotateWebhookSecretInput,
    ScmMutation,
)
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None)))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def org_a():
    return Organization.objects.create(name="SCM Org A", slug="scm-org-a")


@pytest.fixture
def org_b():
    return Organization.objects.create(name="SCM Org B", slug="scm-org-b")


# ---------------------------------------------------------------------------
# rotateWebhookSecret — a cross-org secret-mint / webhook-revocation primitive
# ---------------------------------------------------------------------------


def test_rotate_webhook_secret_foreign_org_is_not_found_and_mints_nothing(permission_resolver, org_a, org_b):
    conn_b = SourceConnection.objects.create(
        organization=org_b,
        kind="github_pat",
        account_login="victim",
        is_active=True,
    )
    permission_resolver.grant(Permission.SCM_CONNECT)

    mut = ScmMutation()
    with _tenant(org_a):
        result = mut.rotate_webhook_secret(_info(), input=RotateWebhookSecretInput(connection_id=conn_b.guid))

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert result.data is None
    # The victim connection must have no secret minted against it.
    conn_b.refresh_from_db()
    assert not conn_b.webhook_secret_ciphertext


def test_rotate_webhook_secret_same_org_mints_secret(permission_resolver, org_b):
    conn_b = SourceConnection.objects.create(
        organization=org_b,
        kind="github_pat",
        account_login="owner",
        is_active=True,
    )
    permission_resolver.grant(Permission.SCM_CONNECT)

    mut = ScmMutation()
    with _tenant(org_b):
        result = mut.rotate_webhook_secret(_info(), input=RotateWebhookSecretInput(connection_id=conn_b.guid))

    assert result.ok, result.errors
    assert result.data.plaintext_secret
    conn_b.refresh_from_db()
    assert conn_b.webhook_secret_ciphertext


# ---------------------------------------------------------------------------
# deleteSshDeployKey — a cross-org soft-delete primitive
# ---------------------------------------------------------------------------


def _make_key(org):
    return SshDeployKey.objects.create(
        organization=org,
        name="deploy-key",
        public_key="ssh-ed25519 AAAAC3Nza deploy",
        fingerprint_sha256="SHA256:" + ("a" * 43),
        private_key_ciphertext=b"ciphertext",
    )


def test_delete_ssh_deploy_key_foreign_org_is_not_found_and_leaves_row(permission_resolver, org_a, org_b):
    key_b = _make_key(org_b)
    permission_resolver.grant(Permission.SCM_KEY_DELETE)

    mut = ScmMutation()
    with _tenant(org_a):
        result = mut.delete_ssh_deploy_key(_info(), input=DeleteSshDeployKeyInput(id=key_b.guid))

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    # The victim key must NOT have been soft-deleted.
    refreshed = SshDeployKey.all_objects.get(pk=key_b.pk)
    assert refreshed.deleted_at is None


def test_delete_ssh_deploy_key_same_org_soft_deletes(permission_resolver, org_b):
    key_b = _make_key(org_b)
    permission_resolver.grant(Permission.SCM_KEY_DELETE)

    mut = ScmMutation()
    with _tenant(org_b):
        result = mut.delete_ssh_deploy_key(_info(), input=DeleteSshDeployKeyInput(id=key_b.guid))

    assert result.ok, result.errors
    refreshed = SshDeployKey.all_objects.get(pk=key_b.pk)
    assert refreshed.deleted_at is not None
