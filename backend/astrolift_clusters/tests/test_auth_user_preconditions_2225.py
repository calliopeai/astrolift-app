"""Reviewed provider targets, exercised with actual PostgreSQL rows and Cognito shapes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aws.identity_users_cognito import (
    CognitoIdentityUsersDriver,
    CognitoUsersConfig,
    cognito_pool_from_issuer,
)
from django.contrib.auth import get_user_model

from astrolift_clusters.schema.auth_users import (
    ClusterAuthUsersQuery,
)
from astrolift_clusters.tests.test_cluster_auth_users_2131 import _cluster, _info
from astrolift_graphql import GUID
from config.schema import schema
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


class Idp:
    """Stateful AWS SDK fake: immutable subjects can change independently of usernames."""

    def __init__(self):
        self.subject = "00000000-0000-0000-0000-000000000001"
        self.calls = []
        self.after_read = None
        self.after_write = None
        self.after_get = None
        self.after_create = None

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            user = {
                "Username": "USER_LITERAL",
                "Attributes": [
                    {"Name": "sub", "Value": self.subject},
                    {"Name": "email", "Value": "user@example.test"},
                ],
                "Enabled": True,
                "UserStatus": "CONFIRMED",
            }
            if name == "list_users":
                if self.after_read:
                    self.after_read()
                return {"Users": [user]}
            if name == "admin_get_user":
                if self.after_get:
                    self.after_get()
                return {"Username": user["Username"], "UserAttributes": user["Attributes"]}
            if name == "admin_create_user":
                if self.after_create:
                    self.after_create()
                return {"User": user}
            if name in {"list_groups", "admin_list_groups_for_user"}:
                return {"Groups": [{"GroupName": "GROUP_LITERAL"}]}
            if self.after_write:
                self.after_write(name)
            return {}

        return call


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


@pytest.fixture
def setup(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.CLUSTER_USERS)
    cluster, org = _cluster()
    actor = get_user_model().objects.create_user(username="actor-2225")
    idp = Idp()

    def driver(current, capability):
        region, pool = cognito_pool_from_issuer(current.oidc_auth_config["discovery_url"])
        return CognitoIdentityUsersDriver(CognitoUsersConfig(region=region, pool_id=pool), client=idp)

    monkeypatch.setattr("core.app_deploy.driver_for_capability", driver)
    return SimpleNamespace(cluster=cluster, org=org, actor=actor, idp=idp, permission=permission_resolver)


def context(s):
    return tenant_context(TenantContext(organization_id=s.org.pk, actor_user_id=s.actor.pk))


def inventory(s):
    with context(s):
        return ClusterAuthUsersQuery().astrolift_cluster_auth_users(_info(s.actor), GUID(str(s.cluster.guid)))


def source(s):
    observed = inventory(s).source
    return {
        "providerPluginId": str(observed.provider_plugin_id),
        "providerPoolId": observed.provider_pool_id,
        "sourceVersion": observed.source_version,
    }


OPERATIONS = [
    ("createClusterAuthUser", "CreateClusterAuthUserInput", {"email": "new@example.test"}, False),
    ("createClusterAuthGroup", "CreateClusterAuthGroupInput", {"name": "NEW_GROUP"}, False),
    (
        "setClusterAuthUserPassword",
        "SetClusterAuthUserPasswordInput",
        {"username": "USER_LITERAL", "password": "WRITE_ONLY_TEST_PASSWORD"},
        True,
    ),
    ("resetClusterAuthUserPassword", "ClusterAuthUserRefInput", {"username": "USER_LITERAL"}, True),
    (
        "setClusterAuthUserEnabled",
        "SetClusterAuthUserEnabledInput",
        {"username": "USER_LITERAL", "enabled": False},
        True,
    ),
    ("deleteClusterAuthUser", "ClusterAuthUserRefInput", {"username": "USER_LITERAL"}, True),
    (
        "setClusterAuthUserGroups",
        "SetClusterAuthUserGroupsInput",
        {"username": "USER_LITERAL", "add": ["NEW_GROUP"], "remove": ["GROUP_LITERAL"]},
        True,
    ),
]


def execute(s, operation, expected, subject=None):
    field, kind, values, _ = operation
    input = {"clusterId": str(s.cluster.guid), **values}
    if expected is not None:
        input["expectedSource"] = expected
    if subject is not None:
        input["expectedUserId"] = subject
    with context(s):
        result = schema.execute_sync(
            f"mutation($input:{kind}!){{{field}(input:$input){{ok errors{{code message field}}}}}}",
            variable_values={"input": input},
            context_value=_info(s.actor).context,
        )
    assert not result.errors, result.errors
    return result.data[field]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_all_seven_reviewed_writes_target_literal_source_and_subject(setup, operation):
    s = setup
    expected = source(s)
    s.idp.calls.clear()
    result = execute(s, operation, expected, s.idp.subject if operation[3] else None)
    assert result["ok"], result
    assert all(kw["UserPoolId"] == expected["providerPoolId"] for _, kw in s.idp.calls)
    if operation[3]:
        assert s.idp.calls[0][0] == "admin_get_user"
        assert s.idp.calls[0][1]["Username"] == "USER_LITERAL"


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("change", ["pool", "aba", "credential", "plugin", "withdrawn"])
def test_changed_source_refuses_all_operations_before_sdk(setup, operation, change):
    s = setup
    expected = source(s)
    if change in {"pool", "aba"}:
        original = dict(s.cluster.oidc_auth_config)
        s.cluster.oidc_auth_config = {
            **original,
            "discovery_url": original["discovery_url"].replace("xU96Y7DAg", "OTHERPOOL"),
        }
        s.cluster.save(update_fields=["oidc_auth_config"])
        if change == "aba":
            s.cluster.oidc_auth_config = original
            s.cluster.save(update_fields=["oidc_auth_config"])
    elif change == "credential":
        s.cluster.auth_config = {"kubeconfig": "CHANGED_TEST_ONLY_CREDENTIAL"}
        s.cluster.save(update_fields=["auth_config"])
    elif change == "plugin":
        plugin = s.cluster.provider_plugin
        plugin.capabilities_manifest = {"changed": True}
        plugin.save(update_fields=["capabilities_manifest"])
    else:
        s.cluster.is_active = False
        s.cluster.save(update_fields=["is_active"])
    s.idp.calls.clear()
    result = execute(s, operation, expected, s.idp.subject if operation[3] else None)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "PRECONDITION"
    assert s.idp.calls == []


@pytest.mark.parametrize("operation", [o for o in OPERATIONS if o[3]])
@pytest.mark.parametrize("subject", ["00000000-0000-0000-0000-000000000002", ""])
def test_recreated_or_unknown_subject_cannot_write_current_username(setup, operation, subject):
    s = setup
    expected = source(s)
    reviewed = s.idp.subject
    s.idp.subject = subject
    s.idp.calls.clear()
    result = execute(s, operation, expected, reviewed)
    assert result["ok"] is False
    assert [n for n, _ in s.idp.calls] == ["admin_get_user"]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_optional_legacy_inputs_keep_current_target_semantics(setup, operation):
    assert execute(setup, operation, None)["ok"] is True


@pytest.mark.parametrize("operation", OPERATIONS)
def test_current_permission_withdrawal_refuses_before_provider(setup, operation):
    s = setup
    expected = source(s)
    s.permission.deny(Permission.CLUSTER_USERS)
    s.idp.calls.clear()
    result = execute(s, operation, expected, s.idp.subject if operation[3] else None)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "PERMISSION_DENIED"
    assert s.idp.calls == []


def test_inventory_changed_during_provider_read_has_no_current_proof(setup):
    s = setup

    def change():
        s.cluster.auth_config = {"kubeconfig": "CHANGED_TEST_ONLY"}
        s.cluster.save(update_fields=["auth_config"])

    s.idp.after_read = change
    result = inventory(s)
    assert result.supported is False
    assert result.source is None
    assert result.users == []


def test_partial_source_save_persists_existing_revision_and_time(setup):
    s = setup
    for row, field, value in [
        (s.cluster, "auth_config", {"kubeconfig": "TEST_ONLY"}),
        (s.cluster.provider_plugin, "capabilities_manifest", {"changed": True}),
    ]:
        revision, time = row.version, row.updated_at
        setattr(row, field, value)
        row.save(update_fields=[field])
        row.refresh_from_db()
        assert row.version == revision + 1
        assert row.updated_at > time


@pytest.mark.parametrize("operation", [o for o in OPERATIONS if o[3]])
def test_reviewed_existing_user_requires_subject_and_source(setup, operation):
    s = setup
    expected = source(s)
    s.idp.calls.clear()
    assert execute(s, operation, expected)["ok"] is False
    assert execute(s, operation, None, s.idp.subject)["ok"] is False
    assert s.idp.calls == []


def test_membership_subject_recheck_prevents_later_effects_but_first_effect_remains(setup):
    s = setup
    expected = source(s)
    reviewed = s.idp.subject

    def replace(name):
        if name == "admin_add_user_to_group":
            s.idp.subject = "replacement-subject"

    s.idp.after_write = replace
    s.idp.calls.clear()
    result = execute(s, OPERATIONS[6], expected, reviewed)
    assert result["ok"] is False
    assert [name for name, _ in s.idp.calls] == [
        "admin_get_user",
        "admin_add_user_to_group",
        "admin_get_user",
    ]


def test_actor_withdrawn_during_subject_read_refuses_before_password_write(setup):
    s = setup
    expected = source(s)

    def withdraw():
        s.actor.is_active = False
        s.actor.save(update_fields=["is_active"])

    s.idp.after_get = withdraw
    s.idp.calls.clear()
    result = execute(s, OPERATIONS[2], expected, s.idp.subject)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "PERMISSION_DENIED"
    assert [name for name, _ in s.idp.calls] == ["admin_get_user"]


@pytest.mark.parametrize("field", ["providerPluginId", "providerPoolId", "sourceVersion"])
def test_mismatched_review_identity_refuses_without_sdk(setup, field):
    s = setup
    expected = source(s)
    expected[field] = (
        "00000000-0000-0000-0000-000000000099" if field == "providerPluginId" else "OTHER_LITERAL"
    )
    s.idp.calls.clear()
    assert execute(s, OPERATIONS[5], expected, s.idp.subject)["ok"] is False
    assert s.idp.calls == []


def test_provider_withdrawal_and_replacement_refuse_without_sdk(setup):
    from astrolift_clusters.models import ProviderPlugin

    s = setup
    expected = source(s)
    original = s.cluster.provider_plugin
    original.soft_delete()
    s.idp.calls.clear()
    assert execute(s, OPERATIONS[5], expected, s.idp.subject)["ok"] is False
    assert s.idp.calls == []
    replacement = ProviderPlugin.objects.create(slug="aws", name="replacement")
    s.cluster.provider_plugin = replacement
    s.cluster.save(update_fields=["provider_plugin"])
    assert execute(s, OPERATIONS[5], expected, s.idp.subject)["ok"] is False
    assert s.idp.calls == []


@pytest.mark.parametrize("reviewed", [True, False])
def test_creation_followups_use_subject_reads_only_for_reviewed_callers(setup, reviewed):
    s = setup
    expected = source(s) if reviewed else None
    operation = (
        "createClusterAuthUser",
        "CreateClusterAuthUserInput",
        {
            "email": "new@example.test",
            "password": "WRITE_ONLY_TEST_PASSWORD",
            "permanent": True,
            "groups": ["NEW_GROUP"],
        },
        False,
    )
    s.idp.calls.clear()
    assert execute(s, operation, expected)["ok"] is True
    expected_calls = (
        [
            "admin_create_user",
            "admin_get_user",
            "admin_set_user_password",
            "admin_get_user",
            "admin_add_user_to_group",
        ]
        if reviewed
        else ["admin_create_user", "admin_set_user_password", "admin_add_user_to_group"]
    )
    assert [name for name, _ in s.idp.calls] == expected_calls


@pytest.mark.parametrize("change", ["subject", "actor"])
def test_reviewed_creation_rechecks_subject_and_authority_before_followups(setup, change):
    s = setup
    expected = source(s)
    operation = (
        "createClusterAuthUser",
        "CreateClusterAuthUserInput",
        {
            "email": "new@example.test",
            "password": "WRITE_ONLY_TEST_PASSWORD",
            "permanent": True,
            "groups": ["NEW_GROUP"],
        },
        False,
    )

    def replace():
        if change == "subject":
            s.idp.subject = "replacement-subject"
        else:
            s.actor.is_active = False
            s.actor.save(update_fields=["is_active"])

    s.idp.after_create = replace
    s.idp.calls.clear()
    result = execute(s, operation, expected)
    assert result["ok"] is False
    assert [name for name, _ in s.idp.calls] == ["admin_create_user", "admin_get_user"]
    assert result["errors"][0]["code"] == ("PRECONDITION" if change == "subject" else "PERMISSION_DENIED")
