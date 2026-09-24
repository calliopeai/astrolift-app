"""Agent secret VALUE management — mutations + status query (#1173).

Against a real database, these pin:

  * ``setAgentSecretValue`` is denied without ``secret.write``;
  * a granted write passes the value through to the store's ``upsert`` with
    the exact ``(uri, {"value": ...})`` shape the spawner reads back
    (write/read symmetry);
  * an unknown env var errors cleanly (NOT_FOUND, nothing written);
  * the audit row records the action + target (slug:env-var) and NEVER the
    value;
  * ``deleteAgentSecretValue`` removes the stored value (and is idempotent
    when already absent);
  * the status query reports exists true/false per ref, and turns a driver
    exception into exists=false + an error string rather than a 500;
  * a ref or bundle location outside the org's secret namespace is refused
    when written, and a stored one is never read, written or deleted by any
    of these surfaces (#1921).

Resolvers are exercised by direct invocation (the agents-test convention)
with the controllable permission resolver + a bound tenant context. The
cluster resolution and secrets driver are monkeypatched to an in-memory
fake so no real cluster/store is needed.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.models import Organization
from core.mutations import ErrorCode
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Secret Org", slug="secret-org")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


class _FakeSecrets:
    def __init__(self, store=None, *, get_raises=False):
        self.store = dict(store or {})
        self.upserts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self.reads: list[str] = []
        self._get_raises = get_raises

    def get(self, path):
        self.reads.append(path)
        if self._get_raises:
            raise RuntimeError("store unreachable")
        return self.store.get(path)

    def upsert(self, path, kvs):
        self.upserts.append((path, dict(kvs)))
        self.store[path] = dict(kvs)

    def delete(self, path):
        self.deletes.append(path)
        self.store.pop(path, None)


@pytest.fixture
def fake_store(monkeypatch):
    """Patch the cluster resolution + secrets driver to an in-memory fake.

    Returns the backend so a test can seed/inspect it. The default store is
    empty; assign ``.store`` before calling to seed values.
    """
    backend = _FakeSecrets()
    import astrolift_agents.services.agent_cluster as agent_cluster
    import core.app_deploy as app_deploy

    monkeypatch.setattr(agent_cluster, "resolve_agent_cluster", lambda _org: object())
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: backend)
    return backend


def _ns(org, name):
    """A ref inside the org's own secret namespace (#1921)."""
    return f"sm:agents/{org.guid}/{name}"


def _bundle_ns(org, name):
    return f"agent-bundles/{org.guid}/{name}"


def _spec(org, *, refs=None, slug="claude-dev"):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug=slug,
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        secret_refs=refs if refs is not None else [{"uri": _ns(org, "gh"), "env_var": "GITHUB_TOKEN"}],
    )


# ---------------------------------------------------------------------------
# setAgentSecretValue
# ---------------------------------------------------------------------------


def test_set_denied_without_secret_write(permission_resolver, info, org, with_tenant_org, fake_store):
    spec = _spec(org)
    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN", value="ghp_x"
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    # Gate is first — nothing reached the store.
    assert fake_store.upserts == []


def test_set_writes_value_through_with_symmetric_shape(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN", value="ghp_secret"
        )
    assert result.ok is True, result.errors
    assert result.data.env_var == "GITHUB_TOKEN"
    assert result.data.uri == _ns(org, "gh")
    assert result.data.exists is True
    # The exact path + kvs the spawner reads back: get(uri)["value"].
    assert fake_store.upserts == [(_ns(org, "gh"), {"value": "ghp_secret"})]


def test_set_unknown_env_var_is_not_found(permission_resolver, info, org, with_tenant_org, fake_store):
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="NOT_A_REF", value="x"
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert fake_store.upserts == []


def test_set_rejects_empty_value(permission_resolver, info, org, with_tenant_org, fake_store):
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN", value=""
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert fake_store.upserts == []


def test_set_cross_org_spec_is_not_found(permission_resolver, info, org, with_tenant_org, fake_store):
    permission_resolver.grant(Permission.SECRET_WRITE)
    other = Organization.objects.create(name="Other", slug="other-org")
    spec = _spec(other)  # owned by a different org
    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN", value="x"
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


def test_set_audit_records_action_and_target_without_value(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)

    captured = []
    from core import mutations as core_mutations

    original = core_mutations._audit_writer
    core_mutations.register_audit_writer(lambda e: captured.append(e))
    try:
        with with_tenant_org(org):
            AgentsMutation().set_agent_secret_value(
                info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN", value="TOP_SECRET_VALUE"
            )
    finally:
        core_mutations.register_audit_writer(original)

    sets = [e for e in captured if e.action == "agents.secret.set"]
    assert len(sets) == 1
    assert sets[0].decision == "ALLOW"
    assert sets[0].target_kind == "AgentSecret"
    assert sets[0].target_id == f"{spec.slug}:GITHUB_TOKEN"
    # The value must not appear anywhere in the audit row.
    assert "TOP_SECRET_VALUE" not in str(dataclasses.asdict(sets[0]))


# ---------------------------------------------------------------------------
# deleteAgentSecretValue
# ---------------------------------------------------------------------------


def test_delete_removes_stored_value(permission_resolver, info, org, with_tenant_org, fake_store):
    permission_resolver.grant(Permission.SECRET_WRITE)
    fake_store.store = {_ns(org, "gh"): {"value": "ghp_x"}}
    spec = _spec(org)
    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )
    assert result.ok is True, result.errors
    assert result.data.exists is False
    assert fake_store.deletes == [_ns(org, "gh")]


def test_delete_absent_value_is_idempotent_success(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)  # empty store
    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )
    assert result.ok is True, result.errors
    assert result.data.exists is False
    # Already absent — probe short-circuits, no delete call issued.
    assert fake_store.deletes == []


def test_delete_removes_present_empty_provider_shell(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    fake_store.store = {_ns(org, "gh"): {}}
    spec = _spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )

    assert result.ok is True, result.errors
    assert fake_store.deletes == [_ns(org, "gh")]


def test_delete_denied_without_secret_write(permission_resolver, info, org, with_tenant_org, fake_store):
    spec = _spec(org)
    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


# ---------------------------------------------------------------------------
# agentEnvironmentSpecSecretStatus
# ---------------------------------------------------------------------------


def test_status_reports_exists_true_and_false(permission_resolver, info, org, with_tenant_org, fake_store):
    permission_resolver.grant(Permission.SECRET_LIST)
    fake_store.store = {_ns(org, "a"): {"value": "AAA"}}  # b absent
    spec = _spec(
        org,
        refs=[
            {"uri": _ns(org, "a"), "env_var": "TOKEN_A"},
            {"uri": _ns(org, "b"), "env_var": "TOKEN_B"},
        ],
    )
    with with_tenant_org(org):
        rows = AgentsQuery().agent_environment_spec_secret_status(info, slug=spec.slug)

    by_var = {r.env_var: r for r in rows}
    assert by_var["TOKEN_A"].exists is True and by_var["TOKEN_A"].error is None
    assert by_var["TOKEN_B"].exists is False and by_var["TOKEN_B"].error is None
    assert by_var["TOKEN_A"].uri == _ns(org, "a")


def test_status_driver_error_is_exists_false_not_raised(
    permission_resolver, info, org, with_tenant_org, monkeypatch
):
    permission_resolver.grant(Permission.SECRET_LIST)
    backend = _FakeSecrets(get_raises=True)
    import astrolift_agents.services.agent_cluster as agent_cluster
    import core.app_deploy as app_deploy

    monkeypatch.setattr(agent_cluster, "resolve_agent_cluster", lambda _org: object())
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: backend)

    spec = _spec(org, refs=[{"uri": _ns(org, "x"), "env_var": "TOKEN_X"}])
    with with_tenant_org(org):
        rows = AgentsQuery().agent_environment_spec_secret_status(info, slug=spec.slug)

    assert len(rows) == 1
    assert rows[0].exists is False
    assert rows[0].error  # a non-empty error string, not a raised exception


def test_status_denied_without_list_permission(permission_resolver, info, org, with_tenant_org, fake_store):
    spec = _spec(org)
    with pytest.raises(PermissionDenied):
        with with_tenant_org(org):
            AgentsQuery().agent_environment_spec_secret_status(info, slug=spec.slug)


def test_binding_crud_is_a_durable_override_layer(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_dispatch.agent_secrets import effective_secret_refs

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org, refs=[{"uri": "sm:source", "env_var": "SOURCE_TOKEN"}])
    with with_tenant_org(org):
        added = AgentsMutation().upsert_agent_secret_ref(
            info,
            env_spec_slug=spec.slug,
            env_var="UI_TOKEN",
            uri=_ns(org, "ui"),
        )
        removed = AgentsMutation().remove_agent_secret_ref(
            info,
            env_spec_slug=spec.slug,
            env_var="SOURCE_TOKEN",
        )

    assert added.ok is True, added.errors
    assert removed.ok is True, removed.errors
    assert effective_secret_refs(spec) == [{"env_var": "UI_TOKEN", "uri": _ns(org, "ui")}]
    # Source ownership stays intact; the tombstone survives a later manifest
    # sync that writes this field again.
    spec.refresh_from_db()
    assert spec.secret_refs == [{"uri": "sm:source", "env_var": "SOURCE_TOKEN"}]


def test_binding_rejects_identifiers_that_exceed_storage_limits(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)

    with with_tenant_org(org):
        long_env = AgentsMutation().upsert_agent_secret_ref(
            info,
            env_spec_slug=spec.slug,
            env_var="A" * 256,
            uri="sm:valid",
        )
        long_uri = AgentsMutation().upsert_agent_secret_ref(
            info,
            env_spec_slug=spec.slug,
            env_var="VALID_TOKEN",
            uri="x" * 513,
        )
        reserved = AgentsMutation().upsert_agent_secret_ref(
            info,
            env_spec_slug=spec.slug,
            env_var="AGENT_CALLBACK_URL",
            uri="sm:redirect",
        )

    assert long_env.ok is False
    assert long_env.errors[0].field == "envVar"
    assert long_uri.ok is False
    assert long_uri.errors[0].field == "uri"
    assert reserved.ok is False
    assert reserved.errors[0].field == "envVar"

    # The oversized caller input is also used in the mutation's audit target.
    # Audit fitting must not poison the surrounding test/request transaction.
    assert AgentEnvironmentSpec.objects.filter(pk=spec.pk).exists()


def test_reveal_agent_secret_is_explicit_read(permission_resolver, info, org, with_tenant_org, fake_store):
    permission_resolver.grant(Permission.SECRET_READ)
    fake_store.store = {_ns(org, "gh"): {"value": "ghp_revealed"}}
    spec = _spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().reveal_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )

    assert result.ok is True, result.errors
    assert result.data.value == "ghp_revealed"
    assert result.data.env_var == "GITHUB_TOKEN"
    assert result.data.provider == "external-secret-store"


def test_reveal_reports_write_only_provider_limitation(
    permission_resolver, info, org, with_tenant_org, monkeypatch
):
    class GitHubActionsSecrets:
        provider_id = "github-actions"
        supports_value_reveal = False
        value_reveal_limitation = "GitHub Actions secret values are write-only."

        def get(self, path):  # pragma: no cover - capability gate runs first
            return None

    permission_resolver.grant(Permission.SECRET_READ)
    backend = GitHubActionsSecrets()
    import astrolift_agents.services.agent_cluster as agent_cluster
    import core.app_deploy as app_deploy

    monkeypatch.setattr(agent_cluster, "resolve_agent_cluster", lambda _org: object())
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: backend)
    spec = _spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().reveal_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert "write-only" in result.errors[0].message


def test_bundle_crud_attach_and_runtime_precedence(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_dispatch.agent_secrets import resolve_task_secret_manifest

    permission_resolver.grant(Permission.SECRET_WRITE)
    permission_resolver.grant(Permission.SECRET_READ)
    permission_resolver.grant(Permission.SECRET_LIST)
    spec = _spec(org, refs=[{"uri": _ns(org, "direct"), "env_var": "SHARED_TOKEN"}])
    fake_store.store[_ns(org, "direct")] = {"value": "direct-wins"}

    with with_tenant_org(org):
        created = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="EMR shared",
            slug="emr-shared",
            backend_ref=_bundle_ns(org, "emr"),
        )
        assert created.ok is True, created.errors
        bundle_id = created.data.id
        duplicate = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="Duplicate",
            slug="emr-shared",
            backend_ref=_bundle_ns(org, "duplicate"),
        )
        empty = AgentsMutation().set_agent_bundle_secret_value(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            key="EMPTY_VALUE",
            value="",
        )
        reserved = AgentsMutation().set_agent_bundle_secret_value(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            key="ASTROLIFT_CLUSTER_KEY",
            value="must-not-shadow-runtime-auth",
        )
        first = AgentsMutation().set_agent_bundle_secret_value(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            key="SHARED_TOKEN",
            value="bundle-loses",
        )
        second = AgentsMutation().set_agent_bundle_secret_value(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            key="BUNDLE_ONLY",
            value="bundle-value",
        )
        attached = AgentsMutation().attach_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            prefix="",
            position=5,
        )
        bundles = AgentsQuery().agent_secret_bundles(info, env_spec_slug=spec.slug)
        attachments = AgentsQuery().agent_environment_spec_secret_bundle_attachments(info, slug=spec.slug)

    assert duplicate.ok is False
    assert duplicate.errors[0].code == ErrorCode.CONFLICT.value
    assert empty.ok is False
    assert empty.errors[0].code == ErrorCode.VALIDATION.value
    assert reserved.ok is False
    assert reserved.errors[0].code == ErrorCode.VALIDATION.value
    assert first.ok is True and second.ok is True
    assert attached.ok is True, attached.errors
    assert bundles[0].key_names == ["BUNDLE_ONLY", "SHARED_TOKEN"]
    assert bundles[0].can_reveal is True
    assert attachments[0].position == 5

    manifest = resolve_task_secret_manifest(
        cluster=object(),
        spec=spec,
        secret_name="agent-task-x-secrets",
        namespace="ns",
        task_guid="task-x",
    )
    assert manifest["stringData"] == {
        "BUNDLE_ONLY": "bundle-value",
        "SHARED_TOKEN": "direct-wins",
    }

    with with_tenant_org(org):
        renamed = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            name="EMR shared renamed",
            backend_ref=_bundle_ns(org, "emr"),
        )
        blocked_move = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            name="EMR moved",
            backend_ref=_bundle_ns(org, "emr-moved"),
        )
        revealed = AgentsMutation().reveal_agent_bundle_secret_value(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            key="BUNDLE_ONLY",
        )
        blocked_delete = AgentsMutation().delete_agent_secret_bundle(
            info, env_spec_slug=spec.slug, bundle_id=bundle_id
        )
        detached = AgentsMutation().detach_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            attachment_id=attached.data.id,
        )
        deleted = AgentsMutation().delete_agent_secret_bundle(
            info, env_spec_slug=spec.slug, bundle_id=bundle_id
        )

    assert renamed.ok is True
    assert blocked_move.ok is False
    assert blocked_move.errors[0].code == ErrorCode.PRECONDITION.value
    assert revealed.ok is True and revealed.data.value == "bundle-value"
    assert blocked_delete.ok is False
    assert blocked_delete.errors[0].code == ErrorCode.PRECONDITION.value
    assert detached.ok is True
    assert deleted.ok is True
    assert _bundle_ns(org, "emr") in fake_store.deletes


def test_default_bundle_backend_path_is_organization_scoped(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="Shared defaults",
            slug="shared-defaults",
        )

    assert result.ok is True, result.errors
    assert result.data.backend_ref == f"agent-bundles/{org.guid}/shared-defaults"


def test_bundle_backend_move_checks_live_store_not_stale_key_cache(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    """Out-of-band provider writes may precede key-cache enumeration."""
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    with with_tenant_org(org):
        created = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="External bundle",
            slug="external-bundle",
            backend_ref=_bundle_ns(org, "external"),
        )
    assert created.ok is True, created.errors
    fake_store.store[_bundle_ns(org, "external")] = {"EXTERNAL_TOKEN": "present"}

    with with_tenant_org(org):
        moved = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=created.data.id,
            name="External bundle",
            backend_ref=_bundle_ns(org, "moved"),
        )

    assert moved.ok is False
    assert moved.errors[0].code == ErrorCode.PRECONDITION.value
    assert "contains keys" in moved.errors[0].message


def test_non_default_bundle_is_not_injected_into_default_agent_run(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_dispatch.agent_secrets import resolve_task_secret_manifest

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org, refs=[])
    with with_tenant_org(org):
        created = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="Production only",
            slug="production-only",
            backend_ref=_bundle_ns(org, "production"),
        )
        assert created.ok is True, created.errors
        attached = AgentsMutation().attach_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=created.data.id,
            environment="production",
        )
        assert attached.ok is True, attached.errors

    fake_store.store[_bundle_ns(org, "production")] = {"PROD_TOKEN": "must-not-leak"}
    assert (
        resolve_task_secret_manifest(
            cluster=object(),
            spec=spec,
            secret_name="agent-task-x-secrets",
            namespace="ns",
            task_guid="task-x",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Org secret namespace (#1921)
# ---------------------------------------------------------------------------
#
# The store is shared by every org without its own cluster, and a relative
# name lands in its install-wide root: the AWS driver resolves ``_VICTIM`` to
# ``astrolift/managed/rds-orders/master``, another tenant's database password.
# Rows written straight to the database stand in for refs stored before
# write-time validation existed.

_VICTIM = "managed/rds-orders/master"


@pytest.mark.parametrize(
    "uri",
    [
        pytest.param(_VICTIM, id="relative-spelling-of-another-tenants-secret"),
        pytest.param("sm:github-token", id="bare-name-in-the-shared-root"),
        pytest.param("astrolift/agents/{other}/gh", id="another-org"),
        pytest.param(
            "arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/managed/rds-orders/master-AbCdEf",
            id="arn-of-another-tenants-secret",
        ),
        pytest.param(
            "arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/agents/{org}/gh-AbCdEf",
            id="arn-even-of-an-own-name",
        ),
        pytest.param("agent-bundles/{org}/shared", id="a-bundle-location"),
        pytest.param("services/{org}/{other}/db-password", id="a-managed-service-secret"),
    ],
)
def test_upsert_ref_rejects_a_location_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store, uri
):
    from astrolift_agents.models.agent_secret_binding import AgentSecretBindingOverride

    other = Organization.objects.create(name="Other Org", slug="other-org-1921")
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().upsert_agent_secret_ref(
            info,
            env_spec_slug=spec.slug,
            env_var="EXFIL_TOKEN",
            uri=uri.format(other=other.guid, org=org.guid),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "uri"
    assert not AgentSecretBindingOverride.objects.filter(
        environment_spec=spec, env_var="EXFIL_TOKEN"
    ).exists()
    assert fake_store.reads == []


def test_upsert_ref_accepts_a_location_inside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_dispatch.agent_secrets import effective_secret_refs

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    uri = f"astrolift/agents/{org.guid}/gh"

    with with_tenant_org(org):
        result = AgentsMutation().upsert_agent_secret_ref(
            info, env_spec_slug=spec.slug, env_var="GH_TOKEN", uri=uri
        )

    assert result.ok is True, result.errors
    assert {"env_var": "GH_TOKEN", "uri": uri} in effective_secret_refs(spec)


def _planted_spec(org):
    return _spec(org, refs=[{"uri": _VICTIM, "env_var": "DATABASE_PASSWORD"}])


def test_set_refuses_a_stored_ref_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    fake_store.store = {_VICTIM: {"value": "victim-pw"}}
    spec = _planted_spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="DATABASE_PASSWORD", value="attacker-pw"
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert f"agents/{org.guid}/" in result.errors[0].message
    assert fake_store.upserts == []
    assert fake_store.store[_VICTIM] == {"value": "victim-pw"}


def test_delete_refuses_a_stored_ref_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    fake_store.store = {_VICTIM: {"value": "victim-pw"}}
    spec = _planted_spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="DATABASE_PASSWORD"
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert fake_store.deletes == []
    assert fake_store.store[_VICTIM] == {"value": "victim-pw"}


def test_reveal_refuses_a_stored_binding_override_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_agents.models.agent_secret_binding import AgentSecretBindingOverride

    permission_resolver.grant(Permission.SECRET_READ)
    fake_store.store = {_VICTIM: {"value": "victim-pw"}}
    spec = _spec(org)
    AgentSecretBindingOverride.objects.create(environment_spec=spec, env_var="DATABASE_PASSWORD", uri=_VICTIM)

    with with_tenant_org(org):
        result = AgentsMutation().reveal_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="DATABASE_PASSWORD"
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert result.data is None
    assert fake_store.reads == []


def test_remove_still_clears_a_stored_ref_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    """Removing the binding is how an operator clears such a ref; it never
    touches the store, so it stays allowed."""
    from astrolift_dispatch.agent_secrets import effective_secret_refs

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _planted_spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().remove_agent_secret_ref(
            info, env_spec_slug=spec.slug, env_var="DATABASE_PASSWORD"
        )

    assert result.ok is True, result.errors
    assert effective_secret_refs(spec) == []
    assert fake_store.reads == []


def test_status_reports_a_stored_ref_outside_the_org_namespace_without_probing(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_LIST)
    fake_store.store = {_VICTIM: {"value": "victim-pw"}, _ns(org, "gh"): {"value": "ghp_x"}}
    spec = _spec(
        org,
        refs=[
            {"uri": _VICTIM, "env_var": "DATABASE_PASSWORD"},
            {"uri": _ns(org, "gh"), "env_var": "GITHUB_TOKEN"},
        ],
    )

    with with_tenant_org(org):
        rows = AgentsQuery().agent_environment_spec_secret_status(info, slug=spec.slug)

    by_var = {r.env_var: r for r in rows}
    assert by_var["DATABASE_PASSWORD"].exists is False
    assert f"agents/{org.guid}/" in (by_var["DATABASE_PASSWORD"].error or "")
    assert by_var["GITHUB_TOKEN"].exists is True
    assert fake_store.reads == [_ns(org, "gh")]


def test_every_value_surface_hands_the_store_the_location_a_reference_names(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    """``secret://agents/<org guid>/gh`` is the secret at ``agents/<org guid>/gh``
    on every surface (#1921): set, reveal, the status probe and delete hand the
    store the location, never the scheme a driver would file as part of the
    name."""
    permission_resolver.grant(Permission.SECRET_WRITE)
    permission_resolver.grant(Permission.SECRET_READ)
    permission_resolver.grant(Permission.SECRET_LIST)
    location = f"agents/{org.guid}/gh"
    spec = _spec(org, refs=[{"uri": f"secret://{location}", "env_var": "GITHUB_TOKEN"}])

    with with_tenant_org(org):
        written = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN", value="ghp_new"
        )
        revealed = AgentsMutation().reveal_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )
        rows = AgentsQuery().agent_environment_spec_secret_status(info, slug=spec.slug)
        deleted = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )

    assert written.ok is True, written.errors
    assert revealed.ok is True, revealed.errors
    assert revealed.data.value == "ghp_new"
    assert [(row.env_var, row.uri, row.exists) for row in rows] == [("GITHUB_TOKEN", location, True)]
    assert deleted.ok is True, deleted.errors
    assert fake_store.upserts == [(location, {"value": "ghp_new"})]
    assert set(fake_store.reads) == {location}
    assert fake_store.deletes == [location]


def test_upsert_ref_probes_the_location_a_reference_names(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    location = f"agents/{org.guid}/gh"
    fake_store.store = {location: {"value": "ghp_x"}}
    spec = _spec(org, refs=[])

    with with_tenant_org(org):
        result = AgentsMutation().upsert_agent_secret_ref(
            info, env_spec_slug=spec.slug, env_var="GH_TOKEN", uri=f"secret://{location}"
        )

    assert result.ok is True, result.errors
    assert (result.data.uri, result.data.exists) == (location, True)
    assert fake_store.reads == [location]


def test_set_refuses_a_stored_ref_naming_a_bundle_location(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    """A typed ref under ``agent-bundles/`` would let setAgentSecretValue
    replace an attached bundle's keys with ``{"value": ...}`` (#1921)."""
    permission_resolver.grant(Permission.SECRET_WRITE)
    location = _bundle_ns(org, "shared")
    fake_store.store = {location: {"API_KEY": "k", "OTHER_KEY": "o"}}
    spec = _spec(org, refs=[{"uri": location, "env_var": "SHADOW"}])

    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="SHADOW", value="clobbered"
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert fake_store.upserts == []
    assert fake_store.store[location] == {"API_KEY": "k", "OTHER_KEY": "o"}


_VICTIM_BUNDLE = "managed/rds-orders/credentials"


@pytest.mark.parametrize(
    "backend_ref",
    [
        pytest.param(_VICTIM_BUNDLE, id="relative-spelling-of-another-tenants-secret"),
        pytest.param(f"astrolift/{_VICTIM_BUNDLE}", id="absolute-spelling"),
        pytest.param("agent-bundles/{other}/shared", id="another-orgs-bundle"),
        pytest.param("project-bundles/{org}/{other}/jira", id="a-project-bundle-location"),
        pytest.param("secret://agent-bundles/{org}/shared", id="a-secret-reference-not-a-location"),
        pytest.param("agents/{org}/shared", id="an-org-root-that-is-not-for-bundles"),
    ],
)
def test_create_bundle_rejects_a_location_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store, backend_ref
):
    from astrolift_services.models import SecretBundle

    other = Organization.objects.create(name="Other Org", slug="other-org-1921")
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="Exfil",
            slug="exfil",
            backend_ref=backend_ref.format(org=org.guid, other=other.guid),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "backendRef"
    assert not SecretBundle.objects.filter(organization=org, slug="exfil").exists()


def test_update_bundle_rejects_a_location_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_services.models import SecretBundle

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    with with_tenant_org(org):
        created = AgentsMutation().create_agent_secret_bundle(
            info, env_spec_slug=spec.slug, name="Shared", slug="shared"
        )
        assert created.ok is True, created.errors
        moved = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=created.data.id,
            name="Shared",
            backend_ref=_VICTIM_BUNDLE,
        )

    assert moved.ok is False
    assert moved.errors[0].code == ErrorCode.VALIDATION.value
    assert moved.errors[0].field == "backendRef"
    assert SecretBundle.objects.get(slug="shared", organization=org).backend_ref == _bundle_ns(org, "shared")


def _planted_bundle(org):
    from astrolift_services.models import SecretBundle

    return SecretBundle.objects.create(
        organization=org, name="Planted", slug="planted", backend_ref=_VICTIM_BUNDLE
    )


@pytest.mark.parametrize(
    ("operation", "kwargs"),
    [
        pytest.param("set_agent_bundle_secret_value", {"key": "PASSWORD", "value": "attacker"}, id="set-key"),
        pytest.param("delete_agent_bundle_secret_value", {"key": "PASSWORD"}, id="delete-key"),
        pytest.param("reveal_agent_bundle_secret_value", {"key": "PASSWORD"}, id="reveal-key"),
    ],
)
def test_bundle_key_operations_refuse_a_stored_location_outside_the_org_namespace(
    permission_resolver, info, org, with_tenant_org, fake_store, operation, kwargs
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    permission_resolver.grant(Permission.SECRET_READ)
    fake_store.store = {_VICTIM_BUNDLE: {"PASSWORD": "victim-pw"}}
    spec = _spec(org)
    bundle = _planted_bundle(org)

    with with_tenant_org(org):
        result = getattr(AgentsMutation(), operation)(
            info, env_spec_slug=spec.slug, bundle_id=str(bundle.guid), **kwargs
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert f"agent-bundles/{org.guid}/" in result.errors[0].message
    assert fake_store.reads == []
    assert fake_store.upserts == []
    assert fake_store.store[_VICTIM_BUNDLE] == {"PASSWORD": "victim-pw"}


def test_delete_bundle_outside_the_org_namespace_leaves_the_store_alone(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_services.models import SecretBundle

    permission_resolver.grant(Permission.SECRET_WRITE)
    fake_store.store = {_VICTIM_BUNDLE: {"PASSWORD": "victim-pw"}}
    spec = _spec(org)
    bundle = _planted_bundle(org)

    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_bundle(
            info, env_spec_slug=spec.slug, bundle_id=str(bundle.guid)
        )

    assert result.ok is True, result.errors
    assert not SecretBundle.objects.filter(pk=bundle.pk).exists()
    assert fake_store.reads == []
    assert fake_store.deletes == []
    assert fake_store.store[_VICTIM_BUNDLE] == {"PASSWORD": "victim-pw"}


def test_update_bundle_moves_off_a_location_outside_the_org_namespace_without_reading_it(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    permission_resolver.grant(Permission.SECRET_WRITE)
    fake_store.store = {_VICTIM_BUNDLE: {"PASSWORD": "victim-pw"}}
    spec = _spec(org)
    bundle = _planted_bundle(org)

    with with_tenant_org(org):
        result = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=str(bundle.guid),
            name="Planted",
            backend_ref=_bundle_ns(org, "planted"),
        )

    assert result.ok is True, result.errors
    bundle.refresh_from_db()
    assert bundle.backend_ref == _bundle_ns(org, "planted")
    assert fake_store.reads == []
    assert fake_store.store[_VICTIM_BUNDLE] == {"PASSWORD": "victim-pw"}


# ---- one store location holds one bundle (#1921) --------------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param("agent-bundles/{org}/shared-defaults", id="the-same-location"),
        pytest.param("/agent-bundles/{org}/shared-defaults", id="leading-slash"),
        pytest.param("astrolift/agent-bundles/{org}/shared-defaults", id="install-root"),
        pytest.param("sm:agent-bundles/{org}/shared-defaults", id="store-scheme"),
        pytest.param("agent-bundles/{org}/Shared-Defaults", id="case-key-vault-ignores"),
        pytest.param("agent-bundles/{org}/shared_defaults", id="underscore-key-vault-folds"),
        pytest.param("agent-bundles/{org}/shared/defaults", id="slash-gcp-folds"),
    ],
)
def test_create_bundle_refuses_a_location_another_bundle_holds(
    permission_resolver, info, org, with_tenant_org, fake_store, spelling
):
    """Deleting either of two bundles on one location would delete the other's
    keys, and the delete guard only knows its own attachments."""
    from astrolift_services.models import SecretBundle

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    with with_tenant_org(org):
        first = AgentsMutation().create_agent_secret_bundle(
            info, env_spec_slug=spec.slug, name="Shared defaults", slug="shared-defaults"
        )
        alias = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="Alias",
            slug="alias",
            backend_ref=spelling.format(org=org.guid),
        )

    assert first.ok is True, first.errors
    assert alias.ok is False
    assert alias.errors[0].code == ErrorCode.CONFLICT.value
    assert alias.errors[0].field == "backendRef"
    assert "'shared-defaults'" in alias.errors[0].message
    assert not SecretBundle.objects.filter(organization=org, slug="alias").exists()


def test_create_bundle_accepts_a_distinct_location_and_another_orgs_same_name(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    other = Organization.objects.create(name="Other Org", slug="other-org-unique-1921")
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    other_spec = _spec(other, slug="other-dev")
    with with_tenant_org(org):
        first = AgentsMutation().create_agent_secret_bundle(
            info, env_spec_slug=spec.slug, name="Shared", slug="shared"
        )
        second = AgentsMutation().create_agent_secret_bundle(
            info, env_spec_slug=spec.slug, name="Shared 2", slug="shared-2"
        )
    with with_tenant_org(other):
        theirs = AgentsMutation().create_agent_secret_bundle(
            info, env_spec_slug=other_spec.slug, name="Shared", slug="shared"
        )

    assert [first.ok, second.ok, theirs.ok] == [True, True, True], (
        first.errors,
        second.errors,
        theirs.errors,
    )


def test_update_bundle_refuses_to_move_onto_another_bundles_location(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_services.models import SecretBundle

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec(org)
    with with_tenant_org(org):
        held = AgentsMutation().create_agent_secret_bundle(
            info, env_spec_slug=spec.slug, name="Held", slug="held"
        )
        mover = AgentsMutation().create_agent_secret_bundle(
            info, env_spec_slug=spec.slug, name="Mover", slug="mover"
        )
        moved = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=mover.data.id,
            name="Mover",
            backend_ref=f"/{_bundle_ns(org, 'held')}",
        )

    assert held.ok is True, held.errors
    assert moved.ok is False
    assert moved.errors[0].code == ErrorCode.CONFLICT.value
    assert SecretBundle.objects.get(organization=org, slug="mover").backend_ref == _bundle_ns(org, "mover")


def test_delete_leaves_a_location_another_live_bundle_still_holds(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    """Two bundles stored on one location before backendRef was unique:
    deleting one drops only its row, and the last one out deletes the store.
    Both carry the same spelling, so the in-memory store, which has no
    driver's name mapping, sees the one location they share."""
    from astrolift_services.models import SecretBundle

    permission_resolver.grant(Permission.SECRET_WRITE)
    location = _bundle_ns(org, "shared")
    fake_store.store = {location: {"API_KEY": "k"}}
    spec = _spec(org)
    holder = SecretBundle.objects.create(organization=org, name="Holder", slug="holder", backend_ref=location)
    alias = SecretBundle.objects.create(organization=org, name="Alias", slug="alias", backend_ref=location)

    with with_tenant_org(org):
        dropped = AgentsMutation().delete_agent_secret_bundle(
            info, env_spec_slug=spec.slug, bundle_id=str(alias.guid)
        )
        assert dropped.ok is True, dropped.errors
        assert fake_store.deletes == []
        assert fake_store.store[location] == {"API_KEY": "k"}
        assert SecretBundle.objects.filter(pk=holder.pk).exists()

        last = AgentsMutation().delete_agent_secret_bundle(
            info, env_spec_slug=spec.slug, bundle_id=str(holder.guid)
        )

    assert last.ok is True, last.errors
    assert fake_store.deletes == [location]


# ---- binding-derived refs belong to the service (#1921) -------------------------


def _spec_with_project_binding(org, *, binding_ref="astrolift/rds/orders-db/url"):
    """A spec attached to a project database whose ``DATABASE_URL`` the
    platform derived from the service's binding."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Project, Team
    from astrolift_services.models import ManagedService, ManagedServiceAttachment, ManagedServiceBinding

    team = Team.objects.create(organization=org, name="Eng", slug="eng-value-1921")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-value-1921")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s value 1921", slug="k8s-value-1921", plugin_version="1.0.0")]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=ProviderPlugin.objects.get(slug="k8s-value-1921"),
        name="Shared",
        slug="shared-value-1921",
        endpoint="https://cluster.example.com",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
    )
    service = ManagedService.objects.create(
        project=project,
        tenant_cluster=cluster,
        kind="postgres",
        name="orders-db",
        backend_ref="postgres/orders-db",
        status="active",
    )
    ManagedServiceBinding.objects.create(
        managed_service=service, env_key="DATABASE_URL", env_value_ref=binding_ref, is_secret=True
    )
    spec = _spec(org)
    ManagedServiceAttachment.objects.create(managed_service=service, agent_environment_spec=spec)
    return spec


@pytest.mark.parametrize(
    ("operation", "kwargs", "permission"),
    [
        pytest.param("set_agent_secret_value", {"value": "attacker"}, Permission.SECRET_WRITE, id="set"),
        pytest.param("delete_agent_secret_value", {}, Permission.SECRET_WRITE, id="delete"),
        pytest.param("reveal_agent_secret_value", {}, Permission.SECRET_READ, id="reveal"),
    ],
)
def test_value_mutations_leave_a_binding_derived_ref_to_its_service(
    permission_resolver, info, org, with_tenant_org, fake_store, operation, kwargs, permission
):
    """The agent surface manages refs typed on the spec. ``DATABASE_URL`` here
    is the database's own credential: overwriting, deleting or revealing it
    through the agent would act on the service."""
    permission_resolver.grant(permission)
    fake_store.store = {"astrolift/rds/orders-db/url": {"value": "postgres://orders"}}
    spec = _spec_with_project_binding(org)

    with with_tenant_org(org):
        result = getattr(AgentsMutation(), operation)(
            info, env_spec_slug=spec.slug, env_var="DATABASE_URL", **kwargs
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert result.errors[0].field == "envVar"
    assert "managed by the platform" in result.errors[0].message
    assert fake_store.reads == []
    assert fake_store.upserts == []
    assert fake_store.deletes == []
    assert fake_store.store == {"astrolift/rds/orders-db/url": {"value": "postgres://orders"}}


def test_remove_still_hides_a_binding_derived_ref_from_the_spec(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    from astrolift_dispatch.agent_secrets import effective_secret_refs

    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec_with_project_binding(org)

    with with_tenant_org(org):
        result = AgentsMutation().remove_agent_secret_ref(
            info, env_spec_slug=spec.slug, env_var="DATABASE_URL"
        )

    assert result.ok is True, result.errors
    assert "DATABASE_URL" not in {ref["env_var"] for ref in effective_secret_refs(spec)}
    assert fake_store.reads == []


def test_value_mutations_still_manage_a_typed_ref_that_overrides_a_binding(
    permission_resolver, info, org, with_tenant_org, fake_store
):
    """A typed ref that takes over the env var is the spec's again."""
    permission_resolver.grant(Permission.SECRET_WRITE)
    spec = _spec_with_project_binding(org)
    own = _ns(org, "db-url")
    spec.secret_refs = [{"uri": own, "env_var": "DATABASE_URL"}]
    spec.save()

    with with_tenant_org(org):
        result = AgentsMutation().set_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="DATABASE_URL", value="postgres://own"
        )

    assert result.ok is True, result.errors
    assert fake_store.upserts == [(own, {"value": "postgres://own"})]
