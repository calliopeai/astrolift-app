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
    exception into exists=false + an error string rather than a 500.

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
        self._get_raises = get_raises

    def get(self, path):
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


def _spec(org, *, refs=None, slug="claude-dev"):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug=slug,
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        secret_refs=refs if refs is not None else [{"uri": "sm:gh", "env_var": "GITHUB_TOKEN"}],
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
    assert result.data.uri == "sm:gh"
    assert result.data.exists is True
    # The exact path + kvs the spawner reads back: get(uri)["value"].
    assert fake_store.upserts == [("sm:gh", {"value": "ghp_secret"})]


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
    fake_store.store = {"sm:gh": {"value": "ghp_x"}}
    spec = _spec(org)
    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )
    assert result.ok is True, result.errors
    assert result.data.exists is False
    assert fake_store.deletes == ["sm:gh"]


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
    fake_store.store = {"sm:gh": {}}
    spec = _spec(org)

    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_secret_value(
            info, env_spec_slug=spec.slug, env_var="GITHUB_TOKEN"
        )

    assert result.ok is True, result.errors
    assert fake_store.deletes == ["sm:gh"]


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
    fake_store.store = {"sm:a": {"value": "AAA"}}  # sm:b absent
    spec = _spec(
        org,
        refs=[
            {"uri": "sm:a", "env_var": "TOKEN_A"},
            {"uri": "sm:b", "env_var": "TOKEN_B"},
        ],
    )
    with with_tenant_org(org):
        rows = AgentsQuery().agent_environment_spec_secret_status(info, slug=spec.slug)

    by_var = {r.env_var: r for r in rows}
    assert by_var["TOKEN_A"].exists is True and by_var["TOKEN_A"].error is None
    assert by_var["TOKEN_B"].exists is False and by_var["TOKEN_B"].error is None
    assert by_var["TOKEN_A"].uri == "sm:a"


def test_status_driver_error_is_exists_false_not_raised(
    permission_resolver, info, org, with_tenant_org, monkeypatch
):
    permission_resolver.grant(Permission.SECRET_LIST)
    backend = _FakeSecrets(get_raises=True)
    import astrolift_agents.services.agent_cluster as agent_cluster
    import core.app_deploy as app_deploy

    monkeypatch.setattr(agent_cluster, "resolve_agent_cluster", lambda _org: object())
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: backend)

    spec = _spec(org, refs=[{"uri": "sm:x", "env_var": "TOKEN_X"}])
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
            uri="sm:ui",
        )
        removed = AgentsMutation().remove_agent_secret_ref(
            info,
            env_spec_slug=spec.slug,
            env_var="SOURCE_TOKEN",
        )

    assert added.ok is True, added.errors
    assert removed.ok is True, removed.errors
    assert effective_secret_refs(spec) == [{"env_var": "UI_TOKEN", "uri": "sm:ui"}]
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
    fake_store.store = {"sm:gh": {"value": "ghp_revealed"}}
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
    spec = _spec(org, refs=[{"uri": "sm:direct", "env_var": "SHARED_TOKEN"}])
    fake_store.store["sm:direct"] = {"value": "direct-wins"}

    with with_tenant_org(org):
        created = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="EMR shared",
            slug="emr-shared",
            backend_ref="bundles/emr",
        )
        assert created.ok is True, created.errors
        bundle_id = created.data.id
        duplicate = AgentsMutation().create_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            name="Duplicate",
            slug="emr-shared",
            backend_ref="bundles/duplicate",
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
            backend_ref="bundles/emr",
        )
        blocked_move = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=bundle_id,
            name="EMR moved",
            backend_ref="bundles/emr-moved",
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
    assert "bundles/emr" in fake_store.deletes


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
            backend_ref="bundles/external",
        )
    assert created.ok is True, created.errors
    fake_store.store["bundles/external"] = {"EXTERNAL_TOKEN": "present"}

    with with_tenant_org(org):
        moved = AgentsMutation().update_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=created.data.id,
            name="External bundle",
            backend_ref="bundles/moved",
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
            backend_ref="bundles/production",
        )
        assert created.ok is True, created.errors
        attached = AgentsMutation().attach_agent_secret_bundle(
            info,
            env_spec_slug=spec.slug,
            bundle_id=created.data.id,
            environment="production",
        )
        assert attached.ok is True, attached.errors

    fake_store.store["bundles/production"] = {"PROD_TOKEN": "must-not-leak"}
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
