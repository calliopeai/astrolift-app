"""Typed brain read tokens (#2470), staged and enabled per brain.

A brain issues the typed contract-1 claims only after a platform operator
staged it (its workloads verify typed tokens, pinned to its organization),
redeployed every serving environment and enabled issuance with an attestation.
Every other brain issues the legacy shapes unchanged. A person's token carries
their live teams in the brain's organization within the calling API token's
team; a hosted app's carries its RegisteredApp GUID. More than 64 teams or
more than 8192 bytes refuses. Real Postgres throughout; run tokens are in
``astrolift_agents/tests/test_typed_run_brain_tokens_2470.py``.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

import jwt
import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.utils import timezone

from astrolift_agents.views.mcp import _connect_brain
from astrolift_identity.api_tokens import (
    SCOPE_MCP_READ,
    SCOPE_READ_APPS,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken, Member, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry import brain_token_rollout, brain_tokens
from astrolift_registry.brain_token_rollout import RolloutRefused
from astrolift_registry.brain_tokens import BrainTokenError
from astrolift_registry.models import BrainTokenKey, BrainTypedTokenRollout, RegisteredApp
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.tests.test_brain_apps_2243 import _deploy, _hosted, _materialize
from astrolift_registry.tests.test_brain_followups_2441_2444 import BRAIN_MANIFEST
from core.app_deploy import render_resources_for_deployment
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db

ISSUER = "https://astrolift.example.net"
BRAIN_URL = "https://brain.acme.example"
ATTESTATION = "every serving replica accepted a legacy and a typed token"
LEGACY_USER_CLAIMS = {"iss", "aud", "sub", "email", "scope", "iat", "exp", "jti"}
LEGACY_APP_CLAIMS = {"iss", "aud", "sub", "scope", "env", "iat", "exp", "jti"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, settings):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr(
        "astrolift_registry.schema.mutations.registration._bootstrap_app_environments", lambda *a, **k: None
    )
    settings.APP_BASE_URL = ISSUER


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr("core.mutations._audit_writer", rows.append)
    return rows


def _world(suffix):
    from astrolift_clusters.models import TenantCluster

    world = ScopeWorld(suffix)
    world.user = make_user(suffix)
    world.cluster = make_cluster(world, suffix)
    TenantCluster.objects.filter(pk=world.cluster.pk).update(lifecycle=TenantCluster.Lifecycle.MANAGED.value)
    RegisteredApp.objects.filter(pk=world.medops_app.pk).update(app_kind="mimwell")
    world.medops_app.refresh_from_db()
    world.brain = world.medops_app
    AppEnvironment.objects.create(
        registered_app=world.brain, tenant_cluster=world.cluster, name="production", url=BRAIN_URL
    )
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug=f"builder-{suffix}",
    )
    # A live member of the organization and of both its teams.
    Member.objects.create(user=world.user, scope_kind=Member.ScopeKind.ORG, scope_id=world.org.pk)
    for team in (world.medops, world.platform):
        Member.objects.create(user=world.user, scope_kind=Member.ScopeKind.TEAM, scope_id=team.pk)
    world.operator = get_user_model().objects.create(
        username=f"op-{suffix}", email=f"op-{suffix}@astrolift.test", is_superuser=True
    )
    return world


@pytest.fixture
def world():
    return _world("tt2470")


@pytest.fixture
def other():
    return _world("ttx2470")


def _teams(*teams):
    return sorted(str(team.guid) for team in teams)


def _api_token(world, *, team):
    return ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=team,
        name="brains",
        token_hash=uuid.uuid4().hex,
        token_last_4="2470",
        scopes=[SCOPE_READ_APPS, SCOPE_MCP_READ],
    )


@contextmanager
def _as(world, token=None):
    team_id = token.team_id if token is not None else world.medops.pk
    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=team_id, actor_user_id=world.user.pk)
    ):
        marker = set_current_api_token(token) if token is not None else None
        try:
            yield
        finally:
            if marker is not None:
                reset_current_api_token(marker)


def _issue(world, token=None):
    with _as(world, token):
        return RegistryMutation().issue_brain_read_token(make_info(world.user), slug=world.brain.slug)


def _claims(token, brain):
    keys = {key["kid"]: key for key in brain_tokens.jwks()["keys"]}
    public = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(keys[jwt.get_unverified_header(token)["kid"]]))
    return jwt.decode(token, public, algorithms=["ES256"], audience=str(brain.guid), issuer=ISSUER)


def _deployed(brain):
    """A running deployment of every environment that serves ``brain``."""
    for environment in AppEnvironment.objects.filter(registered_app=brain).exclude(url=""):
        Deployment.objects.create(
            registered_app=brain,
            app_environment=environment,
            trigger_kind=Deployment.TriggerKind.MANUAL.value,
            status=Deployment.Status.RUNNING.value,
            image_tag="2",
        )


def typed(brain, operator):
    """Stage ``brain``, deploy it and enable typed issuance, as the procedure does."""
    brain_token_rollout.stage(brain, operator=operator, apply=True)
    _deployed(brain)
    brain_token_rollout.enable(brain, operator=operator, attestation=ATTESTATION, apply=True)


# ---------------------------------------------------------------------------
# Who gets which shape
# ---------------------------------------------------------------------------


def test_unstaged_and_staged_only_brains_issue_the_legacy_user_token(world):
    assert set(_claims(_issue(world).data.token, world.brain)) == LEGACY_USER_CLAIMS

    brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)

    claims = _claims(_issue(world).data.token, world.brain)
    assert set(claims) == LEGACY_USER_CLAIMS
    assert claims["email"] == world.user.email


def test_an_enabled_brain_issues_the_person_a_typed_token_with_their_live_teams(world, other):
    def joined(team, **fields):
        return Member.objects.create(
            user=world.user, scope_kind=Member.ScopeKind.TEAM, scope_id=team.pk, **fields
        )

    deleted_team = Team.objects.create(organization=world.org, name="Gone", slug="gone-tt2470")
    joined(deleted_team)
    deleted_team.soft_delete()
    joined(Team.objects.create(organization=world.org, name="Away", slug="away-tt2470"), is_active=False)
    joined(
        Team.objects.create(organization=world.org, name="Paused", slug="paused-tt2470"),
        lifecycle=Member.Lifecycle.SUSPENDED,
    )
    joined(Team.objects.create(organization=world.org, name="Left", slug="left-tt2470")).soft_delete()
    joined(other.medops)  # a team of another organization
    typed(world.brain, world.operator)

    claims = _claims(_issue(world).data.token, world.brain)

    assert (claims["contract"], claims["sub_kind"], claims["org"]) == ("1", "user", str(world.org.guid))
    assert claims["teams"] == _teams(world.medops, world.platform)
    assert "agent" not in claims and "app" not in claims
    assert set(claims) - brain_tokens.TYPED_CLAIMS == LEGACY_USER_CLAIMS
    assert claims["email"] == world.user.email and claims["scope"] == "brain:read"


def test_a_person_no_longer_in_the_organization_carries_no_teams(world):
    typed(world.brain, world.operator)
    Member.objects.get(user=world.user, scope_kind=Member.ScopeKind.ORG).soft_delete()

    assert _claims(_issue(world).data.token, world.brain)["teams"] == []


def test_the_calling_api_tokens_team_caps_the_teams(world):
    typed(world.brain, world.operator)
    scoped = _api_token(world, team=world.medops)

    assert _claims(_issue(world, scoped).data.token, world.brain)["teams"] == _teams(world.medops)
    with _as(world, scoped):
        connected = _connect_brain(SimpleNamespace(user=world.user), {"slug": world.brain.slug})
    assert _claims(connected["token"], world.brain)["teams"] == _teams(world.medops)

    wide = _api_token(world, team=None)
    assert _claims(_issue(world, wide).data.token, world.brain)["teams"] == _teams(
        world.medops, world.platform
    )


def test_another_organizations_rollout_row_types_nothing(world, other):
    BrainTypedTokenRollout.objects.create(
        organization=other.org, brain=world.brain, issuance_enabled_at=timezone.now()
    )
    typed(other.brain, other.operator)

    assert set(_claims(_issue(world).data.token, world.brain)) == LEGACY_USER_CLAIMS
    assert _claims(_issue(other).data.token, other.brain)["org"] == str(other.org.guid)


def test_a_hosted_app_gets_a_typed_app_token_once_its_brain_issues_them(world, monkeypatch):
    app, env = _hosted(world)
    legacy = _materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"]
    assert set(_claims(legacy, world.brain)) == LEGACY_APP_CLAIMS
    assert not BrainTokenKey.objects.exclude(typed_contract="").exists()

    typed(world.brain, world.operator)
    deployment = _deploy(app, env)
    claims = _claims(_materialize(deployment, monkeypatch)["BRAIN_TOKEN"], world.brain)

    assert {name: claims[name] for name in ("contract", "sub_kind", "org", "teams", "app", "sub")} == {
        "contract": "1",
        "sub_kind": "app",
        "org": str(world.org.guid),
        "teams": [],
        "app": str(app.guid),
        "sub": f"app:{app.guid}",
    }
    assert "agent" not in claims and set(claims) - brain_tokens.TYPED_CLAIMS == LEGACY_APP_CLAIMS
    key = BrainTokenKey.objects.get(binding__registered_app=app, deployment=deployment)
    assert key.typed_contract == brain_tokens.TYPED_CONTRACT


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_more_than_64_teams_refuses_and_64_fit(world):
    typed(world.brain, world.operator)
    for index in range(brain_tokens.MAX_TYPED_TEAMS - 2):
        team = Team.objects.create(organization=world.org, name=f"T{index}", slug=f"t{index}-tt2470")
        Member.objects.create(user=world.user, scope_kind=Member.ScopeKind.TEAM, scope_id=team.pk)

    assert len(_claims(_issue(world).data.token, world.brain)["teams"]) == brain_tokens.MAX_TYPED_TEAMS

    one_more = Team.objects.create(organization=world.org, name="T64", slug="t64-tt2470")
    Member.objects.create(user=world.user, scope_kind=Member.ScopeKind.TEAM, scope_id=one_more.pk)
    refused = _issue(world)
    assert not refused.ok
    assert refused.errors[0].code == "PRECONDITION"
    assert "at most 64 teams" in refused.errors[0].message


def test_a_typed_token_over_the_byte_limit_is_refused_not_trimmed(world, monkeypatch):
    typed(world.brain, world.operator)
    size = len(_issue(world).data.token)

    monkeypatch.setattr(brain_tokens, "MAX_TYPED_TOKEN_BYTES", size)
    assert _issue(world).ok
    monkeypatch.setattr(brain_tokens, "MAX_TYPED_TOKEN_BYTES", size - 1)
    refused = _issue(world)
    assert refused.errors[0].code == "PRECONDITION"
    assert f"{size} bytes, over this install's {size - 1}" in refused.errors[0].message


def test_a_hosted_apps_typed_token_over_the_byte_limit_fails_its_deploy(world, monkeypatch):
    from core.app_deploy import AppDeployError

    app, env = _hosted(world)
    typed(world.brain, world.operator)
    monkeypatch.setattr(brain_tokens, "MAX_TYPED_TOKEN_BYTES", 100)

    with pytest.raises(AppDeployError, match="over this install's 100"):
        _materialize(_deploy(app, env), monkeypatch)
    assert not BrainTokenKey.objects.exclude(typed_contract="").exists()


def test_typed_claims_never_come_from_the_caller(world):
    for name in sorted(brain_tokens.TYPED_CLAIMS):
        with pytest.raises(BrainTokenError, match="never from a caller"):
            brain_tokens.issue(user=world.user, brain=world.brain, claims={name: "1"})


def test_an_enabled_brain_refuses_a_token_without_a_typed_subject_or_an_email(world):
    assert brain_tokens.issue(user=world.user, brain=world.brain).contract == ""
    typed(world.brain, world.operator)

    with pytest.raises(BrainTokenError, match="names no typed subject"):
        brain_tokens.issue(user=world.user, brain=world.brain)
    get_user_model().objects.filter(pk=world.user.pk).update(email="")
    world.user.refresh_from_db()
    refused = _issue(world)
    assert refused.errors[0].code == "PRECONDITION" and "email" in refused.errors[0].message


def test_an_agent_or_app_subject_must_match_the_tokens_subject(world):
    typed(world.brain, world.operator)
    guid = str(uuid.uuid4())
    for kind in ("agent", "app"):
        wrong = SimpleNamespace(pk=None, guid=f"{kind}:{uuid.uuid4()}", email="")
        with pytest.raises(BrainTokenError, match="subject must be"):
            brain_tokens.issue(
                user=wrong, brain=world.brain, subject=brain_tokens.TypedSubject(kind, guid=guid)
            )
    with pytest.raises(BrainTokenError, match="unknown typed read token subject"):
        brain_tokens.issue(user=world.user, brain=world.brain, subject=brain_tokens.TypedSubject("team"))


# ---------------------------------------------------------------------------
# Staging, enabling and back
# ---------------------------------------------------------------------------


def test_only_the_platform_operator_stages_a_live_brain(world, audit):
    for user in (world.user, None):
        with pytest.raises(RolloutRefused, match="platform operator"):
            brain_token_rollout.stage(world.brain, operator=user, apply=True)
    with pytest.raises(RolloutRefused, match="not a brain"):
        brain_token_rollout.stage(world.platform_app, operator=world.operator, apply=True)

    checked = brain_token_rollout.stage(world.brain, operator=world.operator, apply=False)
    assert not checked.applied and not BrainTypedTokenRollout.objects.exists() and not audit

    assert brain_token_rollout.stage(world.brain, operator=world.operator, apply=True).applied
    row = BrainTypedTokenRollout.objects.get()
    assert (row.organization_id, row.brain_id, row.created_by_id) == (
        world.org.pk,
        world.brain.pk,
        world.operator.pk,
    )
    assert row.issuance_enabled_at is None
    with pytest.raises(RolloutRefused, match="already staged"):
        brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)
    [entry] = audit
    assert (entry.action, entry.actor_user_id, entry.organization_id, entry.target_id) == (
        "brain.typed_tokens.stage",
        world.operator.pk,
        world.org.pk,
        world.brain.slug,
    )

    RegisteredApp.objects.filter(pk=world.platform_app.pk).update(
        app_kind="mimwell", archived_at=timezone.now()
    )
    world.platform_app.refresh_from_db()
    with pytest.raises(RolloutRefused, match="archived or deleted"):
        brain_token_rollout.stage(world.platform_app, operator=world.operator, apply=True)


def test_enabling_needs_staging_an_attestation_and_a_deploy_made_after_staging(world, audit):
    def enable(attestation=ATTESTATION, apply=True):
        return brain_token_rollout.enable(
            world.brain, operator=world.operator, attestation=attestation, apply=apply
        )

    with pytest.raises(RolloutRefused, match="not staged"):
        enable()
    _deployed(world.brain)  # before staging: rendered without the typed settings
    brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)
    with pytest.raises(RolloutRefused, match="20 to 500 characters"):
        enable("checked")
    with pytest.raises(RolloutRefused, match="does not run a deployment made after staging"):
        enable()

    _deployed(world.brain)
    with pytest.raises(RolloutRefused, match="does not run a deployment made after staging"):
        enable()  # the older deployment still runs
    first = Deployment.objects.filter(registered_app=world.brain).order_by("pk").first()
    Deployment.objects.filter(pk=first.pk).update(status=Deployment.Status.SUPERSEDED.value)
    assert not enable(apply=False).applied
    assert not brain_tokens.typed_issuance(world.brain)

    assert enable().applied
    row = BrainTypedTokenRollout.objects.get()
    assert (row.issuance_enabled_by_id, row.attestation) == (world.operator.pk, ATTESTATION)
    assert brain_tokens.typed_issuance(world.brain)
    with pytest.raises(RolloutRefused, match="already issues typed tokens"):
        enable()
    assert [entry.action for entry in audit] == ["brain.typed_tokens.stage", "brain.typed_tokens.enable"]
    assert audit[-1].extra["attestation"] == ATTESTATION


def test_an_unserved_brain_cannot_enable(world):
    brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)
    AppEnvironment.objects.filter(registered_app=world.brain).update(url="")

    with pytest.raises(RolloutRefused, match="no environment serves"):
        brain_token_rollout.enable(world.brain, operator=world.operator, attestation=ATTESTATION, apply=True)


def test_unstaging_waits_for_typed_tokens_to_expire_and_hosted_apps_to_redeploy(world, monkeypatch, audit):
    app, env = _hosted(world)
    typed(world.brain, world.operator)
    _materialize(_deploy(app, env), monkeypatch)

    def unstage(now=None):
        return brain_token_rollout.unstage(world.brain, operator=world.operator, apply=True, now=now)

    with pytest.raises(RolloutRefused, match="disable it first"):
        unstage()
    brain_token_rollout.disable(world.brain, operator=world.operator, apply=True)
    assert not brain_tokens.typed_issuance(world.brain)
    assert set(_claims(_issue(world).data.token, world.brain)) == LEGACY_USER_CLAIMS
    disabled_at = BrainTypedTokenRollout.objects.get().issuance_disabled_at

    with pytest.raises(RolloutRefused, match="still verify until"):
        unstage(disabled_at + brain_tokens.TOKEN_TTL)
    later = disabled_at + brain_tokens.TOKEN_TTL + dt.timedelta(minutes=2)
    with pytest.raises(RolloutRefused, match=f"still hold typed tokens: {app.slug}"):
        unstage(later)
    _materialize(_deploy(app, env), monkeypatch)
    with pytest.raises(RolloutRefused, match="still hold typed tokens"):
        unstage(later)  # the release it replaced still reads
    _materialize(_deploy(app, env), monkeypatch)

    assert unstage(later).applied
    assert not BrainTypedTokenRollout.objects.exists()
    assert BrainTypedTokenRollout.all_objects.get().deleted_by_id == world.operator.pk
    assert not brain_tokens.consumer_staged(world.brain)
    steps = [entry.action for entry in audit if entry.action.startswith("brain.typed_tokens.")]
    assert steps[-2:] == ["brain.typed_tokens.disable", "brain.typed_tokens.unstage"]
    brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)  # and again, from scratch
    assert BrainTypedTokenRollout.objects.get().issuance_enabled_at is None


def test_disable_and_unstage_need_the_state_they_undo(world):
    with pytest.raises(RolloutRefused, match="does not issue typed tokens"):
        brain_token_rollout.disable(world.brain, operator=world.operator, apply=True)
    with pytest.raises(RolloutRefused, match="is not staged"):
        brain_token_rollout.unstage(world.brain, operator=world.operator, apply=True)
    brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)
    with pytest.raises(RolloutRefused, match="does not issue typed tokens"):
        brain_token_rollout.disable(world.brain, operator=world.operator, apply=True)
    with pytest.raises(RolloutRefused, match="platform operator"):
        brain_token_rollout.unstage(world.brain, operator=world.user, apply=True)
    assert brain_token_rollout.unstage(world.brain, operator=world.operator, apply=True).applied


# ---------------------------------------------------------------------------
# The brain's own verifier settings
# ---------------------------------------------------------------------------


def _rendered_env(brain, manifest_env=""):
    brain.manifest_raw = BRAIN_MANIFEST.replace("acme-brain", brain.slug) + manifest_env
    brain.save(update_fields=["manifest_raw"])
    environment = AppEnvironment.objects.get(registered_app=brain, name="production")
    deployment = Deployment.objects.create(
        registered_app=brain,
        app_environment=environment,
        trigger_kind="manual",
        status="pending",
        image_tag="1",
    )
    [web] = [r for r in render_resources_for_deployment(deployment) if r["kind"] == "Deployment"]
    return {e["name"]: e.get("value") for e in web["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_a_staged_brain_verifies_typed_tokens_for_its_own_organization_only(world):
    forged = f'BRAIN_MCP_TYPED_PRINCIPALS = "true"\nBRAIN_MCP_EXPECTED_ORG_GUID = "{uuid.uuid4()}"\n'

    unstaged = _rendered_env(world.brain, forged)
    assert "BRAIN_MCP_TYPED_PRINCIPALS" not in unstaged and "BRAIN_MCP_EXPECTED_ORG_GUID" not in unstaged
    assert unstaged["KEEP"] == "me" and unstaged["BRAIN_MCP_AUDIENCE"] == str(world.brain.guid)

    brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)
    staged = _rendered_env(world.brain, forged)
    assert staged["BRAIN_MCP_TYPED_PRINCIPALS"] == "true"
    assert staged["BRAIN_MCP_EXPECTED_ORG_GUID"] == str(world.org.guid)


def test_without_an_https_install_url_a_brain_keeps_no_typed_settings(world, settings):
    def stamped(env):
        resources = [
            {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [{"name": "c", **env}]}}}}
        ]
        brain_tokens.stamp_mcp_verification_env(resources, world.brain)
        return resources[0]["spec"]["template"]["spec"]["containers"][0]

    brain_token_rollout.stage(world.brain, operator=world.operator, apply=True)
    settings.APP_BASE_URL = "http://astrolift.internal"
    settings.DEBUG = False

    assert stamped({}) == {"name": "c"}  # nothing to remove, nothing added
    kept = {"name": "KEEP", "value": "me"}
    assert stamped({"env": [kept, {"name": "BRAIN_MCP_TYPED_PRINCIPALS", "value": "true"}]})["env"] == [kept]


# ---------------------------------------------------------------------------
# manage.py brain_typed_tokens
# ---------------------------------------------------------------------------


def _command(*args):
    out = StringIO()
    call_command("brain_typed_tokens", *args, stdout=out)
    return out.getvalue()


def test_the_command_checks_first_and_applies_on_request(world):
    brain, operator = str(world.brain.guid), world.operator.email

    assert "No brain is staged" in _command("status")
    assert "passes its checks" in _command("stage", brain, "--operator", operator)
    assert not BrainTypedTokenRollout.objects.exists()
    assert "stage: " in _command("stage", brain, "--operator", operator, "--apply")
    _deployed(world.brain)
    _command("enable", brain, "--operator", operator, "--attestation", ATTESTATION, "--apply")

    status = _command("status")
    assert f"{world.org.slug}/{world.brain.slug} {brain}" in status
    assert f"by {operator}: {ATTESTATION}" in status
    with pytest.raises(CommandError, match="disable it first"):
        _command("unstage", brain, "--operator", operator, "--apply")
    with pytest.raises(CommandError, match="exactly one active user"):
        _command("disable", brain, "--operator", "nobody@astrolift.test")
    with pytest.raises(CommandError, match="platform operator"):
        _command("disable", brain, "--operator", world.user.email, "--apply")
    with pytest.raises(CommandError, match="app guid"):
        _command("disable", "not-a-guid", "--operator", operator)
    assert brain_tokens.typed_issuance(world.brain)
