"""Per-org modules gated by the install's signed licence (#1879).

The licence is a real RS256 JWT signed with a key made here and verified with
its public half, the shape Client Cove mints and the deployment agent pulls.
Real Postgres throughout; the mutation uses the controllable resolver the
#1859 tests use.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import jwt
import pytest
from constance.test import override_config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.management import call_command

from astrolift_identity import org_modules
from astrolift_identity.models import Organization, OrganizationModule
from astrolift_identity.schema.mutations import IdentityMutation, SetOrganizationModuleInput
from astrolift_identity.schema.queries import MeType
from core import mutations as core_mutations
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

CHAT = org_modules.CHAT_STUDIO_INTEGRATION
ATTACH = org_modules.AGENT_LIVE_ATTACH
LICENCE = OrganizationModule.Source.LICENCE
ORG_ADMIN = OrganizationModule.Source.ORG_ADMIN

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


def _licence(key=_KEY, **claims):
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    # ``exp`` long past: the modules claim stands until the next licence arrives.
    return jwt.encode({"iss": "client-cove", "aud": "junohub", "exp": 1, **claims}, pem, algorithm="RS256")


@pytest.fixture
def licence(settings):
    """Install a licence granting ``modules``; ``None`` leaves the claim out."""

    def install(modules=None, *, token=None, public_key=_PUBLIC):
        claims = {} if modules is None else {"astrolift_modules": list(modules)}
        settings.CALLIOPE_LICENSE = token if token is not None else _licence(**claims)
        settings.CALLIOPE_LICENSE_PUBLIC_KEY = public_key

    return install


@pytest.fixture
def org_a():
    return Organization.objects.create(name="Org A", slug="org-a-licence")


@pytest.fixture
def org_b():
    return Organization.objects.create(name="Org B", slug="org-b-licence")


def _row(org, key):
    return OrganizationModule.objects.filter(organization=org, key=key).first()


def _set(org, key, enabled):
    info = SimpleNamespace(context=SimpleNamespace(user=None, request=None))
    with tenant_context(TenantContext(organization_id=org.id)):
        return IdentityMutation().set_organization_module(
            info, input=SetOrganizationModuleInput(key=key, enabled=enabled)
        )


# ---- when the licence has a say -----------------------------------------


def test_no_licence_changes_nothing(org_a):
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True)
    assert org_modules.sync_from_licence() == []
    assert org_modules.module_state(org_a.id, CHAT) == (True, None)


def test_a_licence_without_the_claim_changes_nothing(org_a, licence):
    licence(None)
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True)
    assert org_modules.sync_from_licence() == []
    assert org_modules.module_state(org_a.id, CHAT) == (True, None)


@pytest.mark.parametrize("variant", ["wrong_key", "no_key", "wrong_issuer"])
def test_an_unverified_licence_is_never_acted_on(org_a, licence, variant):
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True)
    if variant == "wrong_key":
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        licence(token=_licence(other, astrolift_modules=[]))
    elif variant == "no_key":
        licence([], public_key="")
    else:
        licence(token=_licence(iss="someone-else", astrolift_modules=[]))
    assert org_modules.sync_from_licence() == []
    assert org_modules.module_state(org_a.id, CHAT) == (True, None)


# ---- seeding --------------------------------------------------------------
# A grant only allows a module; it never turns one on (#1859's "off until the
# org admin turns it on" holds). Only withholding writes rows.


def test_a_grant_never_turns_a_module_on(org_a, org_b, licence):
    licence([CHAT])
    assert org_modules.sync_from_licence() == []
    assert not OrganizationModule.objects.exists()
    for org in (org_a, org_b):
        assert org_modules.module_state(org.id, CHAT) == (False, org_modules.REASON_NOT_ENABLED)
        # A module the licence withholds is off for a different reason.
        assert org_modules.module_state(org.id, ATTACH) == (False, org_modules.REASON_NOT_LICENSED)


def test_a_grant_leaves_an_org_admin_off_alone(org_a, licence):
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=False, source=ORG_ADMIN)
    licence([CHAT])
    assert org_modules.sync_from_licence() == []
    assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_NOT_ENABLED)


def test_a_withdrawal_turns_an_org_admin_on_off(org_a, org_b, licence):
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True, source=ORG_ADMIN)
    OrganizationModule.objects.create(organization=org_b, key=CHAT, enabled=False, source=ORG_ADMIN)
    licence([])
    assert org_modules.sync_from_licence() == [(org_a.id, CHAT, False)]
    assert (_row(org_a, CHAT).enabled, _row(org_a, CHAT).source) == (False, LICENCE)
    # Already off: the org admin's row is left as it was.
    assert _row(org_b, CHAT).source == ORG_ADMIN
    assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_NOT_LICENSED)
    assert org_modules.enabled_modules(org_a.id) == frozenset()
    # Idempotent: a second start changes nothing.
    assert org_modules.sync_from_licence() == []


def test_a_regrant_allows_but_does_not_restore(org_a, licence, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True, source=ORG_ADMIN)
    licence([])
    org_modules.sync_from_licence()
    licence([CHAT])
    assert org_modules.sync_from_licence() == []
    assert (_row(org_a, CHAT).enabled, _row(org_a, CHAT).source) == (False, LICENCE)
    assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_NOT_ENABLED)
    # The org admin turns it back on.
    assert _set(org_a, CHAT, True).ok is True
    assert org_modules.module_state(org_a.id, CHAT) == (True, None)


def test_a_grant_never_overrides_the_install_force_off(org_a, licence):
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True)
    licence([CHAT])
    org_modules.sync_from_licence()
    assert org_modules.module_state(org_a.id, CHAT) == (True, None)
    with override_config(CHAT_STUDIO_INTEGRATION_ALLOWED=False):
        assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_DISABLED_BY_INSTALL)
        assert org_modules.enabled_modules(org_a.id) == frozenset()


def test_each_change_is_audited(org_a, licence):
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True)
    licence([ATTACH])
    captured = []
    original = core_mutations._audit_writer
    core_mutations.register_audit_writer(captured.append)
    try:
        org_modules.sync_from_licence()
    finally:
        core_mutations.register_audit_writer(original)
    assert [(e.action, e.organization_id, e.target_id, e.extra["enabled"]) for e in captured] == [
        ("org.module.licence", org_a.id, CHAT, False)
    ]
    # The licence is named by fingerprint, never carried.
    assert len(captured[0].extra["licence"]) == 12


def test_the_startup_command_applies_a_withdrawal(org_a, licence):
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True)
    licence([])
    out = io.StringIO()
    call_command("sync_licence_modules", stdout=out)
    assert "1 module change(s)" in out.getvalue()
    assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_NOT_LICENSED)


# ---- the org admin --------------------------------------------------------


def test_an_org_admin_cannot_enable_what_the_licence_withholds(org_a, licence, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    licence([])
    refused = _set(org_a, CHAT, True)
    assert refused.ok is False
    # A code the modules card translates, not English-only text.
    assert refused.errors[0].code == "MODULE_NOT_LICENSED"
    assert not OrganizationModule.objects.filter(organization=org_a).exists()
    assert _set(org_a, CHAT, False).ok is True


def test_an_org_admin_turning_a_licence_off_back_on_records_its_source(org_a, licence, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True)
    licence([])
    org_modules.sync_from_licence()
    assert _row(org_a, CHAT).source == LICENCE
    licence([CHAT])
    assert _set(org_a, CHAT, True).ok is True
    assert (_row(org_a, CHAT).enabled, _row(org_a, CHAT).source) == (True, ORG_ADMIN)


# ---- me.modules -----------------------------------------------------------


def _modules(user, org):
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        rows = MeType(id=str(user.pk), profile=None).modules(info)
    return {r.key: r for r in rows}


def test_me_modules_says_what_the_licence_withholds_and_who_switched(org_a, licence):
    user = get_user_model().objects.create(username="licence-user", email="licence-user@astrolift.dev")
    OrganizationModule.objects.create(organization=org_a, key=CHAT, enabled=True, source=ORG_ADMIN)
    OrganizationModule.objects.create(organization=org_a, key=ATTACH, enabled=True, source=ORG_ADMIN)
    licence([CHAT])
    org_modules.sync_from_licence()
    mods = _modules(user, org_a)
    assert (mods[CHAT].enabled, mods[CHAT].licensed, mods[CHAT].source) == (True, True, ORG_ADMIN)
    assert (mods[ATTACH].enabled, mods[ATTACH].licensed, mods[ATTACH].source) == (False, False, LICENCE)
    # Never switched: no source; install-wide modules have no licence say.
    runs = org_modules.CHAT_STUDIO_AGENT_RUNS
    assert (mods[runs].licensed, mods[runs].source) == (False, None)
    assert (mods["apps"].licensed, mods["apps"].source) == (True, None)


def test_me_modules_without_a_licence_is_all_licensed(org_a):
    user = get_user_model().objects.create(username="licence-user2", email="licence-user2@astrolift.dev")
    assert all(row.licensed for row in _modules(user, org_a).values())
