"""The users of a cluster's edge identity provider, over GraphQL (#2131).

Adding a login to the apps behind central auth used to mean the AWS CLI
against the install's Cognito pool. These resolve the cluster's
``identity_users`` driver and act on its provider: list, create, disable,
delete, reset or set a password, and groups.

Passwords are write-only. No response carries one, the audit log masks the
``password`` variable, and a provider error that echoes one is scrubbed.

Tenancy: the pool behind a shared (org-less) cluster holds every org's
logins, so reading it is the platform operator's as much as writing it.
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import nullcontext

import strawberry
from django.db import transaction
from django.db.models import Q
from django.utils.crypto import constant_time_compare, salted_hmac
from strawberry.types import Info

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import _require_operator_for_shared
from astrolift_clusters.scopes import cluster_org_scope
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

# Shown wherever users are managed until per-app access (#2132) narrows it.
REACH_NOTE = "A user of this pool can sign in to every app on the cluster that has no access rule of its own."


@strawberry.type(name="AstroliftClusterAuthUser")
class ClusterAuthUserType:
    username: str
    email: str
    enabled: bool
    status: str
    created_at: dt.datetime | None
    groups: list[str]
    provider_user_id: str | None = None


@strawberry.type(name="AstroliftClusterAuthSource")
class ClusterAuthSourceType:
    provider_plugin_id: GUID
    provider_pool_id: str
    source_version: str


@strawberry.input
class ExpectedClusterAuthSourceInput:
    provider_plugin_id: GUID
    provider_pool_id: str
    source_version: str


@strawberry.type(name="AstroliftClusterAuthUsers")
class ClusterAuthUsersType:
    supported: bool
    """False when the cluster's central auth is not a provider Astrolift
    manages (an external or federated IdP), or its cloud has no driver yet."""
    reason: str
    provider: str
    reach_note: str
    users: list[ClusterAuthUserType]
    groups: list[str]
    source: ClusterAuthSourceType | None = None


@strawberry.type(name="AstroliftClusterAuthUserChange")
class ClusterAuthUserChangeType:
    username: str
    done: bool


@strawberry.input
class CreateClusterAuthUserInput:
    cluster_id: GUID
    expected_source: ExpectedClusterAuthSourceInput | None = None
    email: str
    # Optional: without one the provider generates a temporary password and
    # sends the invitation itself.
    password: str | None = None
    permanent: bool = False
    groups: list[str] = strawberry.field(default_factory=list)


@strawberry.input
class SetClusterAuthUserPasswordInput:
    cluster_id: GUID
    expected_source: ExpectedClusterAuthSourceInput | None = None
    username: str
    expected_user_id: str | None = None
    password: str
    permanent: bool = False


@strawberry.input
class ClusterAuthUserRefInput:
    cluster_id: GUID
    expected_source: ExpectedClusterAuthSourceInput | None = None
    username: str
    expected_user_id: str | None = None


@strawberry.input
class SetClusterAuthUserEnabledInput:
    cluster_id: GUID
    expected_source: ExpectedClusterAuthSourceInput | None = None
    username: str
    expected_user_id: str | None = None
    enabled: bool


@strawberry.input
class SetClusterAuthUserGroupsInput:
    cluster_id: GUID
    expected_source: ExpectedClusterAuthSourceInput | None = None
    username: str
    expected_user_id: str | None = None
    add: list[str] = strawberry.field(default_factory=list)
    remove: list[str] = strawberry.field(default_factory=list)


@strawberry.input
class CreateClusterAuthGroupInput:
    cluster_id: GUID
    expected_source: ExpectedClusterAuthSourceInput | None = None
    name: str
    description: str = ""


def _cluster(info: Info, cluster_id) -> TenantCluster | None:
    tenant = get_current_tenant()
    cluster = TenantCluster.objects.filter(
        Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
        guid=str(cluster_id),
        deleted_at__isnull=True,
    ).first()
    _require_operator_for_shared(info, cluster, Permission.CLUSTER_USERS)
    return cluster


def identity_users_driver(cluster: TenantCluster):
    """``(driver, "")`` or ``(None, why there is none)``."""
    from core.app_deploy import AppDeployError, driver_for_capability

    try:
        return driver_for_capability(cluster, "identity_users"), ""
    except AppDeployError as exc:
        return None, str(exc)


def _user_to_type(user) -> ClusterAuthUserType:
    return ClusterAuthUserType(
        username=user.username,
        email=user.email,
        enabled=user.enabled,
        status=user.status,
        created_at=user.created_at,
        groups=list(user.groups),
        provider_user_id=getattr(user, "provider_user_id", None),
    )


def _source(cluster, driver) -> ClusterAuthSourceType | None:
    pool_id = getattr(driver, "pool_id", None)
    if (
        not isinstance(pool_id, str)
        or not pool_id
        or not callable(getattr(driver, "verify_user", None))
        or not callable(getattr(driver, "guard_writes", None))
    ):
        return None
    plugin = cluster.provider_plugin
    # Revisions cover saved configuration ABA without encoding credential values.
    payload = json.dumps(
        [
            str(cluster.guid),
            cluster.version,
            cluster.updated_at,
            cluster.organization_id,
            cluster.is_active,
            cluster.region,
            cluster.auth_method,
            str(plugin.guid),
            plugin.version,
            plugin.updated_at,
            plugin.plugin_version,
            plugin.is_enabled,
            plugin.deleted_at is not None,
            pool_id,
        ],
        default=str,
        separators=(",", ":"),
    )
    return ClusterAuthSourceType(
        provider_plugin_id=GUID(str(plugin.guid)),
        provider_pool_id=pool_id,
        source_version=salted_hmac("cluster-auth-source-v1", payload, algorithm="sha256").hexdigest(),
    )


def _run(info: Info, input, act):
    from _sdk.identity_users import IdentityUsersError

    from astrolift_services.cluster_retirement import recheck_cluster_authority

    expected = input.expected_source
    expected_user = getattr(input, "expected_user_id", None)
    username = getattr(input, "username", None)
    if expected_user is not None and expected is None:
        return None, gql_failure(
            ErrorCode.VALIDATION.value, "Expected user requires a reviewed source.", field="expectedSource"
        )
    if expected is not None and username is not None and not expected_user:
        return None, gql_failure(
            ErrorCode.PRECONDITION.value,
            "The reviewed provider user identity is required.",
            field="expectedUserId",
        )
    with transaction.atomic():
        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.select_for_update()
            .filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
                guid=str(input.cluster_id),
                deleted_at__isnull=True,
            )
            .first()
        )
        _require_operator_for_shared(info, cluster, Permission.CLUSTER_USERS)
        if cluster is None:
            return None, gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        plugin = ProviderPlugin.all_objects.select_for_update().get(pk=cluster.provider_plugin_id)
        cluster.provider_plugin = plugin
        recheck_cluster_authority(cluster, Permission.CLUSTER_USERS)
        if not cluster.is_active or not plugin.is_enabled or plugin.deleted_at is not None:
            return None, gql_failure(
                ErrorCode.PRECONDITION.value,
                "The cluster or provider source is unavailable.",
                field="clusterId",
            )
        driver, reason = identity_users_driver(cluster)
        if driver is None:
            return None, gql_failure(ErrorCode.PRECONDITION.value, reason, field="clusterId")
        if expected is not None:
            actual = _source(cluster, driver)
            if actual is None or (
                str(actual.provider_plugin_id) != str(expected.provider_plugin_id)
                or actual.provider_pool_id != expected.provider_pool_id
                or not constant_time_compare(actual.source_version, expected.source_version)
            ):
                return None, gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "The reviewed identity provider source has changed or is unknown.",
                    field="expectedSource",
                )

        def verify():
            recheck_cluster_authority(cluster, Permission.CLUSTER_USERS)
            if expected_user is not None:
                driver.verify_user(username=username, expected_user_id=expected_user)
                recheck_cluster_authority(cluster, Permission.CLUSTER_USERS)

        try:
            guard = driver.guard_writes(verify) if expected is not None else nullcontext()
            with guard:
                if expected is None:
                    verify()
                return act(driver), None
        except IdentityUsersError as exc:
            return None, gql_failure(
                ErrorCode.PRECONDITION.value, str(exc), field="expectedUserId" if expected_user else None
            )


def _email(value: str) -> str:
    email = (value or "").strip()
    if "@" not in email or len(email) > 254:
        raise ValueError("a valid email address is required")
    return email


@strawberry.type
class ClusterAuthUsersQuery:
    @strawberry.field
    @require_permission(Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS))
    @tenant_scoped()
    def astrolift_cluster_auth_users(
        self, info: Info, cluster_id: GUID, search: str = ""
    ) -> ClusterAuthUsersType | None:
        from _sdk.identity_users import IdentityUsersError

        cluster = _cluster(info, cluster_id)
        if cluster is None:
            return None
        driver, reason = identity_users_driver(cluster)
        if driver is None:
            return ClusterAuthUsersType(
                supported=False, reason=reason, provider="", reach_note=REACH_NOTE, users=[], groups=[]
            )
        observed_source = _source(cluster, driver)
        try:
            users = driver.list_users(search=search)
            groups = driver.list_groups()
        except IdentityUsersError as exc:
            return ClusterAuthUsersType(
                supported=False,
                reason=str(exc),
                provider=driver.provider,
                reach_note=REACH_NOTE,
                users=[],
                groups=[],
            )
        current_cluster = (
            TenantCluster.objects.select_related("provider_plugin").filter(pk=cluster.pk).first()
        )
        current_source = _source(current_cluster, driver) if current_cluster is not None else None
        if observed_source is not None and current_source != observed_source:
            return ClusterAuthUsersType(
                supported=False,
                reason="The identity provider source changed during inventory. Retry the read.",
                provider=driver.provider,
                reach_note=REACH_NOTE,
                users=[],
                groups=[],
            )
        return ClusterAuthUsersType(
            supported=True,
            reason="",
            provider=driver.provider,
            reach_note=REACH_NOTE,
            users=[_user_to_type(u) for u in users],
            groups=groups,
            source=observed_source,
        )


@strawberry.type
class ClusterAuthUsersMutation:
    @strawberry.field
    @mutation_audit(action="cluster.auth_user.create")
    @require_permission(
        Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS, "input.cluster_id")
    )
    @tenant_scoped()
    def create_cluster_auth_user(
        self, info: Info, input: CreateClusterAuthUserInput
    ) -> MutationResultType[ClusterAuthUserType]:
        try:
            email = _email(input.email)
        except ValueError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="email")
        user, failure = _run(
            info,
            input,
            lambda d: d.create_user(
                email=email,
                password=input.password or None,
                permanent=input.permanent,
                groups=tuple(g for g in input.groups if g),
            ),
        )
        return failure or gql_success(_user_to_type(user))

    @strawberry.field
    @mutation_audit(action="cluster.auth_user.set_password")
    @require_permission(
        Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS, "input.cluster_id")
    )
    @tenant_scoped()
    def set_cluster_auth_user_password(
        self, info: Info, input: SetClusterAuthUserPasswordInput
    ) -> MutationResultType[ClusterAuthUserChangeType]:
        if not input.password:
            return gql_failure(ErrorCode.VALIDATION.value, "password is required", field="password")
        _, failure = _run(
            info,
            input,
            lambda d: d.set_password(
                username=input.username, password=input.password, permanent=input.permanent
            ),
        )
        return failure or gql_success(ClusterAuthUserChangeType(username=input.username, done=True))

    @strawberry.field
    @mutation_audit(action="cluster.auth_user.reset_password")
    @require_permission(
        Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS, "input.cluster_id")
    )
    @tenant_scoped()
    def reset_cluster_auth_user_password(
        self, info: Info, input: ClusterAuthUserRefInput
    ) -> MutationResultType[ClusterAuthUserChangeType]:
        _, failure = _run(info, input, lambda d: d.reset_password(username=input.username))
        return failure or gql_success(ClusterAuthUserChangeType(username=input.username, done=True))

    @strawberry.field
    @mutation_audit(action="cluster.auth_user.set_enabled")
    @require_permission(
        Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS, "input.cluster_id")
    )
    @tenant_scoped()
    def set_cluster_auth_user_enabled(
        self, info: Info, input: SetClusterAuthUserEnabledInput
    ) -> MutationResultType[ClusterAuthUserChangeType]:
        _, failure = _run(
            info, input, lambda d: d.set_enabled(username=input.username, enabled=input.enabled)
        )
        return failure or gql_success(ClusterAuthUserChangeType(username=input.username, done=True))

    @strawberry.field
    @mutation_audit(action="cluster.auth_user.delete")
    @require_permission(
        Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS, "input.cluster_id")
    )
    @tenant_scoped()
    def delete_cluster_auth_user(
        self, info: Info, input: ClusterAuthUserRefInput
    ) -> MutationResultType[ClusterAuthUserChangeType]:
        _, failure = _run(info, input, lambda d: d.delete_user(username=input.username))
        return failure or gql_success(ClusterAuthUserChangeType(username=input.username, done=True))

    @strawberry.field
    @mutation_audit(action="cluster.auth_user.set_groups")
    @require_permission(
        Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS, "input.cluster_id")
    )
    @tenant_scoped()
    def set_cluster_auth_user_groups(
        self, info: Info, input: SetClusterAuthUserGroupsInput
    ) -> MutationResultType[ClusterAuthUserChangeType]:
        def act(d):
            for group in input.add:
                d.add_to_group(username=input.username, group=group)
            for group in input.remove:
                d.remove_from_group(username=input.username, group=group)

        _, failure = _run(info, input, act)
        return failure or gql_success(ClusterAuthUserChangeType(username=input.username, done=True))

    @strawberry.field
    @mutation_audit(action="cluster.auth_group.create")
    @require_permission(
        Permission.CLUSTER_USERS, scope=cluster_org_scope(Permission.CLUSTER_USERS, "input.cluster_id")
    )
    @tenant_scoped()
    def create_cluster_auth_group(
        self, info: Info, input: CreateClusterAuthGroupInput
    ) -> MutationResultType[ClusterAuthUserChangeType]:
        name = (input.name or "").strip()
        if not name:
            return gql_failure(ErrorCode.VALIDATION.value, "a group name is required", field="name")
        _, failure = _run(info, input, lambda d: d.create_group(name=name, description=input.description))
        return failure or gql_success(ClusterAuthUserChangeType(username=name, done=True))
