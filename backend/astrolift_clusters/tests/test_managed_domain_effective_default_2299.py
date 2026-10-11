"""Actual PostgreSQL and authenticated HTTP default selection, no native I/O."""

from uuid import uuid4

import pytest

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, resolve_managed_domain
from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_identity.models import Member
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user

pytestmark = pytest.mark.django_db(transaction=True)
UPDATE = """mutation($input:UpdateManagedDomainInput!){updateManagedDomain(input:$input){ok errors{code message} data{id version defaultFor}}}"""


@pytest.fixture
def world():
    w = ScopeWorld("default-" + uuid4().hex[:8])
    w.user = make_user(uuid4().hex[:8])
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        w.user,
        permissions=[Permission.PROVIDER_PLUGIN_CONFIGURE, Permission.PROVIDER_PLUGIN_READ],
        kind="ORG",
        scope_id=w.org.pk,
        slug=uuid4().hex,
    )
    w.cluster = make_cluster(w, uuid4().hex[:8])
    aws, _ = ProviderPlugin.objects.get_or_create(slug="aws", defaults={"name": "AWS"})
    w.cluster.provider_plugin = aws
    w.cluster.save()
    common = {
        "organization": w.org,
        "dns_driver": "route53",
        "provision_state": "mark_active",
        "provision_zone_id": "ZOWNED",
        "dns_config": {"provision_cluster_id": w.cluster.pk},
    }
    w.old = ManagedDomain.objects.create(zone="old.test", default_for="both", **common)
    w.new = ManagedDomain.objects.create(zone="new.test", default_for="tenant_apps", **common)
    w.token, w.headers = http_token(w, scopes=("admin",))
    return w


def update(client, w, role, **fields):
    return graphql_http(
        client, w.headers, UPDATE, {"input": {"id": str(w.new.guid), "defaultFor": role, **fields}}
    )


def test_selecting_current_role_makes_exact_domain_effective(world, client):
    reply = update(client, world, "tenant_apps")
    assert reply["data"]["updateManagedDomain"]["ok"]
    world.org.refresh_from_db()
    assert resolve_managed_domain(world.org).pk == world.new.pk
    world.old.refresh_from_db()
    assert world.old.default_for == "preview_envs"
    assert resolve_managed_domain(world.org, for_preview=True).pk == world.old.pk


def test_invalid_default_role_is_refused_without_persistence(world, client):
    reply = update(client, world, "invented-default")
    assert not reply["data"]["updateManagedDomain"]["ok"]
    world.new.refresh_from_db()
    assert world.new.default_for == "tenant_apps"


@pytest.mark.parametrize("role,old_role", [("preview_envs", "tenant_apps"), ("both", "none")])
def test_role_overlap_is_split_without_metadata_rewrite(world, client, role, old_role):
    from astrolift_lifecycle.models import AppEnvironment

    before = dict(world.old.dns_config)
    other = ScopeWorld("other-" + uuid4().hex[:8])
    foreign = ManagedDomain.objects.create(
        organization=other.org, zone="foreign.test", dns_driver="aws", default_for="both"
    )
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        name="Production",
        tenant_cluster=world.cluster,
        url="https://existing.test",
    )
    reply = update(client, world, role, expectedVersion=world.new.version)
    assert reply["data"]["updateManagedDomain"]["ok"]
    world.old.refresh_from_db()
    foreign.refresh_from_db()
    env.refresh_from_db()
    assert world.old.default_for == old_role
    assert world.old.dns_config == before
    assert foreign.default_for == "both"
    assert env.url == "https://existing.test"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"verification_state": "pending"}, "DOMAIN_PROOF_PENDING"),
        ({"provision_state": ""}, "DOMAIN_PROVISIONING_INCOMPLETE"),
        ({"provision_zone_id": ""}, "DOMAIN_PROVISIONING_INCOMPLETE"),
        ({"dns_driver": "cloudflare_read_only"}, "READ_ONLY_DOMAIN"),
        ({"dns_driver": "uninstalled"}, "DOMAIN_WRITER_CHANGED"),
        ({"dns_config": {}}, "DOMAIN_WRITER_UNAVAILABLE"),
    ],
)
def test_unready_or_nonwriter_selection_is_refused(world, client, change, reason):
    ManagedDomain.objects.filter(pk=world.new.pk).update(**change)
    reply = update(client, world, "both")
    result = reply["data"]["updateManagedDomain"]
    assert not result["ok"] and result["errors"][0]["message"] == reason
    world.old.refresh_from_db()
    assert world.old.default_for == "both"


def test_explicit_override_refuses_role_selection_and_is_not_cleared(world, client):
    world.org.default_managed_domain = world.old
    world.org.save()
    reply = update(client, world, "both")
    assert reply["data"]["updateManagedDomain"]["errors"][0]["message"] == "ORGANIZATION_DEFAULT_OVERRIDE"
    world.org.refresh_from_db()
    assert world.org.default_managed_domain_id == world.old.pk
    assert resolve_managed_domain(world.org).pk == world.old.pk


def test_none_can_remove_flags_without_claiming_to_clear_override(world, client):
    world.org.default_managed_domain = world.new
    world.org.save()
    world.new.provision_state = ""
    world.new.save()
    reply = update(client, world, "none", expectedVersion=world.new.version)
    assert reply["data"]["updateManagedDomain"]["ok"]
    world.org.refresh_from_db()
    world.new.refresh_from_db()
    assert world.new.default_for == "none" and resolve_managed_domain(world.org).pk == world.new.pk


def test_stale_or_deleted_domain_cannot_change_overlap(world, client):
    from django.utils import timezone

    reply = update(client, world, "both", expectedVersion=world.new.version - 1)
    assert reply["data"]["updateManagedDomain"]["errors"][0]["code"] == "VERSION_MISMATCH"
    world.new.deleted_at = timezone.now()
    world.new.save()
    reply = update(client, world, "both")
    assert not reply["data"]["updateManagedDomain"]["ok"]
    world.old.refresh_from_db()
    assert world.old.default_for == "both"


@pytest.mark.parametrize("ceiling", ["read", "team", "foreign"])
def test_credential_scope_cannot_select_defaults(world, client, ceiling):
    if ceiling == "read":
        world.token, world.headers = http_token(world, scopes=("read:apps",))
    elif ceiling == "team":
        world.token, world.headers = http_token(world, scopes=("admin",), team=world.medops)
    else:
        other = ScopeWorld("foreign-" + uuid4().hex[:8])
        world.token, world.headers = http_token(other, scopes=("admin",), actor=world.user)
        import json

        reply = client.post(
            "/app/gql/config/",
            json.dumps(
                {"query": UPDATE, "variables": {"input": {"id": str(world.new.guid), "defaultFor": "both"}}}
            ),
            content_type="application/json",
            **world.headers,
        )
        assert reply.status_code == 401
        world.old.refresh_from_db()
        assert world.old.default_for == "both"
        return
    reply = update(client, world, "both")
    assert reply.get("errors") or not reply["data"]["updateManagedDomain"]["ok"]
    world.old.refresh_from_db()
    assert world.old.default_for == "both"


def test_diagnostics_offer_current_metadata_capabilities(world, client, monkeypatch):
    from django.utils import timezone

    from astrolift_clusters import domain_diagnostics as service
    from astrolift_clusters.schema.domain_diagnostics import (
        ManagedDomainCheckState,
        ManagedDomainProviderZone,
    )

    # Typed diagnostics wire, with network ports explicitly unavailable: no
    # DNS health/native provider success is implied by these advisory flags.
    monkeypatch.setattr(
        service,
        "_zone",
        lambda *_: ManagedDomainProviderZone(
            state=ManagedDomainCheckState.UNSUPPORTED,
            reason="FIXTURE_UNCONFIGURED",
            checked_at=timezone.now(),
            zone_id=None,
            zone_name=None,
            private_zone=None,
            nameservers=[],
            records=[],
            truncated=False,
        ),
    )
    monkeypatch.setattr(service.network, "dns_lookup", lambda *_, **__: ([], "UNCONFIGURED"))
    query = "query($id:GUID!,$version:Int!){astroliftManagedDomainDiagnostics(domainId:$id,expectedVersion:$version){actions{canUpdate canSetDefaults defaultUpdateReason}}}"
    variables = {"id": str(world.new.guid), "version": world.new.version}
    reply = graphql_http(client, world.headers, query, variables)
    action = reply["data"]["astroliftManagedDomainDiagnostics"]["actions"]
    assert action == {"canUpdate": True, "canSetDefaults": True, "defaultUpdateReason": ""}
    world.cluster.provider_plugin.is_enabled = False
    world.cluster.provider_plugin.save()
    reply = graphql_http(client, world.headers, query, variables)
    assert (
        reply["data"]["astroliftManagedDomainDiagnostics"]["actions"]["defaultUpdateReason"]
        == "DOMAIN_WRITER_UNAVAILABLE"
    )


@pytest.mark.parametrize(
    "withdraw",
    ["role", "member", "token", "policy", "provider", "actor", "scope", "organization", "replacement"],
)
def test_original_authority_is_fresh_after_real_org_lock_wait(world, monkeypatch, withdraw):
    import threading
    import time

    from django.db import connection, connections, transaction
    from django.test import Client
    from django.utils import timezone

    from astrolift_clusters import managed_domain_defaults as service
    from astrolift_identity.models import ApiToken, Policy, RoleBinding

    reached = threading.Event()
    original = service.update_domain
    pid = []
    result = []
    errors = []

    def called(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid.append(cursor.fetchone()[0])
        reached.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "update_domain", called)

    def request():
        try:
            result.append(update(Client(), world, "both", expectedVersion=world.new.version))
        except BaseException as exc:
            errors.append(type(exc).__name__)
        finally:
            connections.close_all()

    with transaction.atomic():
        type(world.org).objects.select_for_update().get(pk=world.org.pk)
        thread = threading.Thread(target=request)
        thread.start()
        assert reached.wait(10)
        deadline = time.monotonic() + 10
        blocked = False
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pid[0]])
                blocked = cursor.fetchone()[0] == "Lock"
            if blocked:
                break
            time.sleep(0.02)
        assert blocked, "actual HTTP request must wait for the owned org row"
        if withdraw == "role":
            RoleBinding.objects.filter(user=world.user).update(expires_at=timezone.now())
        elif withdraw == "member":
            Member.objects.filter(user=world.user).update(is_active=False)
        elif withdraw == "token":
            ApiToken.objects.filter(pk=world.token.pk).update(is_revoked=True)
        elif withdraw == "policy":
            Policy.objects.create(
                organization=world.org,
                name="Deny",
                slug=uuid4().hex,
                scope_level="ORG",
                effect="DENY",
                action_pattern=Permission.PROVIDER_PLUGIN_CONFIGURE.value,
                conditions=[],
            )
        elif withdraw == "provider":
            ProviderPlugin.objects.filter(pk=world.cluster.provider_plugin_id).update(is_enabled=False)
        elif withdraw == "actor":
            type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
        elif withdraw == "scope":
            ApiToken.objects.filter(pk=world.token.pk).update(scopes=["read:apps"])
        elif withdraw == "replacement":
            from astrolift_identity.api_tokens import mint_token

            ApiToken.objects.filter(pk=world.token.pk).update(token_hash=mint_token().token_hash)
        else:
            type(world.org).objects.filter(pk=world.org.pk).update(deleted_at=timezone.now())
    thread.join(20)
    assert not thread.is_alive() and not errors
    assert result[0].get("errors") or not result[0]["data"]["updateManagedDomain"]["ok"]
    world.old.refresh_from_db()
    world.new.refresh_from_db()
    assert world.old.default_for == "both" and world.new.default_for == "tenant_apps"


def review(world):
    from astrolift_clusters.managed_domain_defaults import defaults_fingerprint

    world.org.refresh_from_db()
    world.new.refresh_from_db()
    return {
        "expectedVersion": world.new.version,
        "expectedDefaultsFingerprint": defaults_fingerprint(world.new, world.org),
    }


@pytest.mark.parametrize("aba", [False, True])
def test_sibling_default_changes_invalidate_review_without_selected_version_change(world, client, aba):
    fields = review(world)
    original = world.old.default_for
    world.old.default_for = "none"
    world.old.save()
    if aba:
        world.old.default_for = original
        world.old.save()
    before = world.old.version
    reply = update(client, world, "both", **fields)
    result = reply["data"]["updateManagedDomain"]
    assert not result["ok"] and result["errors"][0]["message"] == "MANAGED_DOMAIN_DEFAULTS_CHANGED"
    world.old.refresh_from_db()
    world.new.refresh_from_db()
    assert world.old.version == before and world.new.version == fields["expectedVersion"]


def test_current_review_changes_only_selected_roles(world, client):
    fields = review(world)
    reply = update(client, world, "tenant_apps", **fields)
    assert reply["data"]["updateManagedDomain"]["ok"]
    assert review(world)["expectedDefaultsFingerprint"] != fields["expectedDefaultsFingerprint"]
    world.old.refresh_from_db()
    assert world.old.default_for == "preview_envs"


def test_foreign_cluster_association_cannot_become_default(world, client):
    other = ScopeWorld("other-" + uuid4().hex[:8])
    cluster = make_cluster(other, uuid4().hex[:8])
    reply = update(client, world, "both", dnsConfig={"provision_cluster_id": cluster.pk})
    assert reply["data"]["updateManagedDomain"]["errors"][0]["message"] == "DOMAIN_WRITER_UNAVAILABLE"
    world.new.refresh_from_db()
    assert world.new.dns_config == {"provision_cluster_id": world.cluster.pk}


@pytest.mark.parametrize("operator,own_default", [(False, False), (True, False), (True, True)])
def test_shared_defaults_operator_and_own_precedence_are_exact(world, client, operator, own_default):
    world.user.is_superuser = operator
    world.user.save()
    if not own_default:
        ManagedDomain.objects.filter(organization=world.org).update(default_for="none")
    world.cluster.organization = None
    world.cluster.save()
    shared_old = ManagedDomain.objects.create(
        zone="shared-old.test",
        dns_driver="aws",
        default_for="both",
        provision_state="mark_active",
        provision_zone_id="ZSHARED",
        dns_config={"provision_cluster_id": world.cluster.pk},
    )
    shared = ManagedDomain.objects.create(
        zone="shared-new.test",
        dns_driver="aws",
        default_for="none",
        provision_state="mark_active",
        provision_zone_id="ZSHARED2",
        dns_config={"provision_cluster_id": world.cluster.pk},
    )
    world.new = shared
    reply = update(client, world, "tenant_apps", **review(world))
    if operator and not own_default:
        assert reply["data"]["updateManagedDomain"]["ok"]
        world.org.refresh_from_db()
        assert resolve_managed_domain(world.org).pk == shared.pk
        shared_old.refresh_from_db()
        assert shared_old.default_for == "preview_envs"
    else:
        assert reply.get("errors") or not reply["data"]["updateManagedDomain"]["ok"]
        shared_old.refresh_from_db()
        shared.refresh_from_db()
        assert shared_old.default_for == "both" and shared.default_for == "none"


@pytest.mark.parametrize("withdraw", ["revoked", "expired", "deleted", "foreign"])
def test_browser_sidecar_is_fresh_after_lock_wait_and_is_not_revived(world, monkeypatch, withdraw):
    import datetime as dt
    import threading

    from django.db import connections, transaction
    from django.test import Client
    from django.utils import timezone

    from astrolift_clusters import managed_domain_defaults as service
    from astrolift_identity.models import AstroliftSession as Session

    world.user.is_superuser = True
    world.user.save()
    browser = Client()
    browser.force_login(world.user)
    row = Session.objects.create(user=world.user, session_key=browser.session.session_key)
    world.headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    reached = threading.Event()
    original = service.update_domain
    result = []
    errors = []

    def called(*args, **kwargs):
        reached.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "update_domain", called)

    def request():
        try:
            result.append(update(browser, world, "both"))
        except BaseException as exc:
            errors.append(type(exc).__name__)
        finally:
            connections.close_all()

    with transaction.atomic():
        type(world.org).objects.select_for_update().get(pk=world.org.pk)
        thread = threading.Thread(target=request)
        thread.start()
        assert reached.wait(10)
        values = {
            "revoked": {"revoked_at": timezone.now()},
            "expired": {"expires_at": timezone.now() - dt.timedelta(seconds=1)},
            "deleted": {"deleted_at": timezone.now()},
            "foreign": {"user_id": make_user(uuid4().hex[:8]).pk},
        }[withdraw]
        Session.all_objects.filter(pk=row.pk).update(**values)
    thread.join(20)
    assert not thread.is_alive() and not errors
    assert result[0].get("errors") or not result[0]["data"]["updateManagedDomain"]["ok"]
    row.refresh_from_db()
    for key, value in values.items():
        assert getattr(row, key) == value
    world.old.refresh_from_db()
    assert world.old.default_for == "both"


@pytest.mark.parametrize("shared", [False, True])
def test_competing_reviewed_choices_are_serialized_and_one_requires_rereview(world, monkeypatch, shared):
    import threading
    from types import SimpleNamespace

    from django.db import connections
    from django.test import Client

    from astrolift_clusters import managed_domain_defaults as service

    world.new.default_for = "none"
    world.new.save()
    third = ManagedDomain.objects.create(
        organization=world.org,
        zone="third.test",
        dns_driver="aws",
        default_for="none",
        provision_state="mark_active",
        provision_zone_id="ZTHIRD",
        dns_config={"provision_cluster_id": world.cluster.pk},
    )
    other = SimpleNamespace(org=world.org, new=third, headers=world.headers)
    if shared:
        world.user.is_superuser = True
        world.user.save()
        world.cluster.organization = None
        world.cluster.save()
        ManagedDomain.objects.filter(pk__in=[world.old.pk, world.new.pk, third.pk]).update(organization=None)
        world.new.refresh_from_db()
        third.refresh_from_db()
        other_org = ScopeWorld("shared-review-" + uuid4().hex[:8]).org
        Member.objects.create(user=world.user, scope_kind="ORG", scope_id=other_org.pk)
        other.org = other_org
        _, other.headers = http_token(other, scopes=("admin",), actor=world.user)
    fields = [review(world), review(other)]
    barrier = threading.Barrier(2)
    original = service.update_domain
    results = []
    errors = []

    def called(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "update_domain", called)

    def request(target, reviewed):
        try:
            results.append(update(Client(), target, "tenant_apps", **reviewed)["data"]["updateManagedDomain"])
        except BaseException as exc:
            errors.append(type(exc).__name__)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=request, args=(target, reviewed))
        for target, reviewed in zip((world, other), fields, strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not any(thread.is_alive() for thread in threads) and not errors
    assert sum(result["ok"] for result in results) == 1
    rejected = next(result for result in results if not result["ok"])
    assert rejected["errors"][0]["message"] == "MANAGED_DOMAIN_DEFAULTS_CHANGED"
    success = next(result for result in results if result["ok"])
    world.org.refresh_from_db()
    assert str(resolve_managed_domain(world.org).guid) == success["data"]["id"]
