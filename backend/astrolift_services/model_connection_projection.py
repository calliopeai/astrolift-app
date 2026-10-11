"""Request-local read snapshots. Effect admission never consumes these hints."""

from dataclasses import replace
from types import SimpleNamespace

from astrolift_identity import abac
from astrolift_identity.permission_resolver import (
    _app_scope_chains,
    _decide_from_grants,
    _org_confined_bindings,
    _prefetch_app_slugs,
    _share_grants,
    actor_groups,
)
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import _credential_app_ids, _credential_scope, live_app_owners
from astrolift_services.model_connection_policy import fresh_actor
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    _check_permission_decision,
)
from core.tenancy import get_current_tenant


class PageAuthority:
    """One fresh principal, with per-target evaluation of current batched facts."""

    def __init__(self, info, environments):
        self.tenant = get_current_tenant()
        self.actor = fresh_actor(info)
        self.attrs = replace(
            abac.attributes_from_request(
                SimpleNamespace(
                    META=getattr(info.context.request, "META", {}),
                    session=info.context.request._model_connection_auth_session,
                ),
                self.actor.pk,
            ),
            cache={},
        )
        app_ids = {env.registered_app_id for env in environments}
        with abac.request_attributes(self.attrs):
            live_ids = set(
                live_app_owners(
                    RegisteredApp.objects.filter(
                        pk__in=app_ids,
                        organization_id=self.tenant.organization_id,
                        organization__deleted_at__isnull=True,
                    )
                ).values_list("pk", flat=True)
            )
            self.chains = _app_scope_chains(self.tenant, live_ids)
            self.grants = [
                grant
                for grant in _org_confined_bindings(self.tenant)
                if grant.role.organization_id in (None, self.tenant.organization_id)
            ]
            self.shares = {
                app_id: [
                    share
                    for share in shares
                    if share.grant.role.organization_id in (None, self.tenant.organization_id)
                ]
                for app_id, shares in _share_grants(self.tenant, live_ids).items()
            }
            self.groups = actor_groups(self.tenant)
            _prefetch_app_slugs(self.tenant, live_ids)
            from astrolift_identity.models import Project

            project_ids = {
                ident for chain in self.chains.values() for kind, ident in chain if kind == "PROJECT"
            }
            self.attrs.cache.update(
                {
                    ("slug", "PROJECT", pk): slug
                    for pk, slug in Project.all_objects.filter(pk__in=project_ids).values_list("pk", "slug")
                }
            )
            self.policies = abac.org_policies(self.tenant.organization_id, self.attrs)
        self.credential_apps = {
            permission: _credential_app_ids(live_ids, permission)
            for permission in (Permission.APP_UPDATE, Permission.APP_APPROVE_DEPLOY)
        }

    def _check(self, permission, chain, attrs, *, model_operation=False):
        scope = PermissionScope(ScopeKind(chain[0][0]), chain[0][1])
        with abac.request_attributes(attrs):
            result = None
            if self.actor.is_superuser:
                granted = True
                if model_operation:
                    result = abac.evaluate(
                        abac.new_subject(
                            organization_id=self.tenant.organization_id,
                            actor_user_id=self.actor.pk,
                            permission=permission.value,
                            chain=chain,
                            roles_at_target=[],
                            groups=[],
                            attrs=attrs,
                        ),
                        self.policies,
                    )
                    granted = not result.denied
            else:
                decision = _decide_from_grants(
                    self.tenant,
                    permission,
                    chain,
                    tuple(self.grants),
                    tuple(self.shares.get(chain[0][1], ())) if chain[0][0] == "APP" else (),
                    self.groups,
                )
                granted, result = decision.granted, decision.abac
            _check_permission_decision(
                permission, scope, lambda: (granted, "model connection authority is unavailable")
            )
            required = 0
            if result:
                policies = {str(policy.guid): policy for policy in self.policies}
                for outcome in result.applied:
                    for condition in policies[outcome.policy_guid].conditions:
                        if condition.get("kind") == "approval_required":
                            need = condition.get("min_approvers", 1)
                            if type(need) is not int or not 1 <= need <= 16:
                                raise PermissionDenied(permission, scope, "approval quorum is unavailable")
                            required = max(required, need)
            return required

    def admission(self, env, permission=Permission.APP_UPDATE, *, request_only=True):
        chain = self.chains.get(env.registered_app_id)
        if not chain:
            raise PermissionDenied(permission, None, "app ownership is unavailable")
        _credential_scope(
            PermissionScope(ScopeKind.APP, env.registered_app_id),
            permission,
            allowed_app_ids=self.credential_apps[permission],
        )
        attrs = replace(
            self.attrs,
            environment=env.name,
            region=env.tenant_cluster.region or None,
            approval_request=request_only,
            approvals=0,
        )
        return self._check(permission, chain, attrs, model_operation=True)

    def organization(self, permission, env):
        scope = _credential_scope(PermissionScope(ScopeKind.ORG, self.tenant.organization_id), permission)
        attrs = replace(
            self.attrs,
            environment=env.name,
            region=env.tenant_cluster.region or None,
            approval_request=False,
            approvals=0,
        )
        self._check(permission, [("ORG", scope.id)], attrs)

    def approver(self, env):
        self.organization(Permission.ORG_UPDATE, env)
        self.admission(env, Permission.APP_APPROVE_DEPLOY, request_only=False)


def policy_page_facts(organization_id):
    from constance import config

    from astrolift_identity.models import Policy
    from astrolift_services.models import ModelConnectionPolicy

    return (
        list(ModelConnectionPolicy.objects.filter(organization_id=organization_id).order_by("pk")),
        list(Policy.objects.filter(organization_id=organization_id).order_by("pk")),
        [
            config.MODEL_CONNECTION_DEFAULT_MODE,
            config.MODEL_CONNECTION_DEFAULT_QUORUM,
            config.MODEL_CONNECTION_ALLOW_SELF_APPROVAL,
        ],
    )


def source_page_facts(services):
    from _sdk.local_model_artifact import local_source_identity

    from astrolift_services.models import HuggingFaceConnection, LocalModelArtifact

    services = list(services)
    connections = {
        (row.pk, row.organization_id, row.version)
        for row in HuggingFaceConnection.objects.filter(
            pk__in=[service.model_hf_connection_id for service in services if service.model_hf_connection_id]
        )
    }
    artifacts = {}
    for service in services:
        if (service.config or {}).get("model_source") == "local_artifact":
            try:
                artifacts[service.pk] = local_source_identity(service.config)
            except ValueError:
                pass
    stored = (
        {
            (str(row.guid), row.organization_id, row.version, row.manifest_sha256)
            for row in LocalModelArtifact.objects.filter(
                guid__in=[value[0] for value in artifacts.values()], state="verified"
            )
        }
        if artifacts
        else set()
    )
    result = {}
    for service in services:
        config = service.config or {}
        if config.get("model_source") == "local_artifact":
            identity = artifacts.get(service.pk)
            result[service.pk] = bool(
                identity and (identity[0], service.organization_id, identity[1], identity[2]) in stored
            )
        elif config.get("model_source") == "bedrock":
            from astrolift_services.native_model_connections import current

            result[service.pk] = current(service)
        elif config.get("model_source") not in (None, "huggingface"):
            result[service.pk] = False
        elif service.model_hf_connection_id:
            result[service.pk] = (
                service.model_hf_connection_id,
                service.organization_id,
                service.model_hf_connection_version,
            ) in connections
        else:
            result[service.pk] = config.get("hf_token_secret_ref") is None
    return result


class VotePrincipalFacts:
    """Current page voters, separated by actor and credential, never an approval count cache."""

    def __init__(self, votes, environments):
        from django.db.models import Q

        from astrolift_identity.idp_groups import member_groups_for_users
        from astrolift_identity.models import GroupRoleMapping, Project, RoleBinding, Team
        from astrolift_identity.permission_resolver import Grant, ShareGrant
        from astrolift_registry.models import AppTeamAccess

        self.organization_id = get_current_tenant().organization_id
        user_ids = {vote.voter_id for vote in votes}
        self.groups = member_groups_for_users(user_ids, self.organization_id)
        self.live_apps = {
            app.pk: app
            for app in live_app_owners(
                RegisteredApp.objects.filter(
                    pk__in={env.registered_app_id for env in environments},
                    organization_id=self.organization_id,
                    organization__deleted_at__isnull=True,
                )
            ).select_related("project")
        }
        self.chains = _app_scope_chains(get_current_tenant(), self.live_apps)
        self.shares = list(
            AppTeamAccess.objects.filter(
                registered_app_id__in=self.live_apps,
                team__organization_id=self.organization_id,
                team__deleted_at__isnull=True,
            )
        )
        groups = set().union(*self.groups.values()) if self.groups else set()
        bindings = list(
            RoleBinding.objects.filter(
                Q(user_id__in=user_ids) | Q(user__isnull=True, group_external_id__in=groups),
                Q(role__organization_id=self.organization_id) | Q(role__organization__isnull=True),
                role__deleted_at__isnull=True,
            )
            .select_related("role")
            .order_by("granted_at", "pk")
        )
        mappings = list(
            GroupRoleMapping.objects.filter(
                Q(role__organization_id=self.organization_id) | Q(role__organization__isnull=True),
                organization_id=self.organization_id,
                group_external_id__in=groups,
                role__deleted_at__isnull=True,
            )
            .select_related("role")
            .order_by("created_at", "pk")
        )
        all_bindings = [*bindings, *mappings]
        valid = {"ORG": {self.organization_id}}
        for kind, model in (("TEAM", Team), ("PROJECT", Project), ("APP", RegisteredApp)):
            valid[kind] = set(
                model.objects.filter(
                    organization_id=self.organization_id,
                    pk__in={row.scope_id for row in all_bindings if row.scope_kind == kind},
                ).values_list("pk", flat=True)
            )
        from django.utils import timezone

        now = timezone.now()
        self.grants = {}
        self.share_grants = {}
        for user_id in user_ids:
            grants = []
            ordered = [
                *[row for row in bindings if row.user_id == user_id],
                *[
                    row
                    for row in bindings
                    if row.user_id is None and row.group_external_id in self.groups.get(user_id, ())
                ],
                *[row for row in mappings if row.group_external_id in self.groups.get(user_id, ())],
            ]
            for row in ordered:
                expiry = getattr(row, "expires_at", None)
                if row.scope_id not in valid.get(row.scope_kind, ()) or expiry is not None and expiry <= now:
                    continue
                source = (
                    "user"
                    if getattr(row, "user_id", None) is not None
                    else "group_mapping"
                    if row in mappings
                    else "group"
                )
                grants.append(
                    Grant(
                        role=row.role,
                        scope_kind=row.scope_kind,
                        scope_id=row.scope_id,
                        inherits=getattr(row, "inherits", True),
                        source=source,
                        guid=str(row.guid),
                        group_external_id=getattr(row, "group_external_id", ""),
                        expires_at=expiry,
                    )
                )
            self.grants[user_id] = grants
            shared = {}
            for share in self.shares:
                for grant in grants:
                    if grant.scope_kind == "TEAM" and grant.scope_id == share.team_id and grant.inherits:
                        shared.setdefault(share.registered_app_id, []).append(
                            ShareGrant(
                                grant,
                                str(share.guid),
                                share.team_id,
                                share.access_level,
                                share.registered_app_id,
                            )
                        )
            self.share_grants[user_id] = shared
        self.policies = policy_page_facts(self.organization_id)[1]
        self.slugs = {("slug", "APP", app.pk): app.slug for app in self.live_apps.values()}
        project_ids = {app.project_id for app in self.live_apps.values() if app.project_id}
        self.slugs.update(
            {
                ("slug", "PROJECT", pk): slug
                for pk, slug in Project.all_objects.filter(pk__in=project_ids).values_list("pk", "slug")
            }
        )

    def authority(self, actor, token, request, *, session_created_at=None):
        from datetime import UTC, datetime

        from astrolift_identity.permission_resolver import share_levels
        from astrolift_identity.step_up_sso import SESSION_SSO_AUTH_TIME_KEY
        from core.tenancy import TenantContext

        authority = PageAuthority.__new__(PageAuthority)
        authority.actor = actor
        authority.tenant = TenantContext(organization_id=self.organization_id, actor_user_id=actor.pk)
        authority.attrs = replace(
            abac.attributes_from_request(request, actor.pk),
            cache={
                **self.slugs,
                ("policies", self.organization_id): self.policies,
            },
        )
        raw = request.session.get(SESSION_SSO_AUTH_TIME_KEY)
        authority.attrs.authenticated_at = (
            datetime.fromtimestamp(int(raw), tz=UTC) if isinstance(raw, (int, float)) else session_created_at
        )
        authority.chains = self.chains
        authority.grants = self.grants.get(actor.pk, [])
        authority.groups = self.groups.get(actor.pk, frozenset())
        authority.shares = self.share_grants.get(actor.pk, {})
        authority.policies = self.policies
        authority.credential_apps = {}
        for permission in (Permission.APP_UPDATE, Permission.APP_APPROVE_DEPLOY):
            allowed = set(self.live_apps)
            if token is not None and token.team_id is not None:
                allowed = {
                    app.pk
                    for app in self.live_apps.values()
                    if app.team_id == token.team_id
                    or app.team_id is None
                    and app.project_id
                    and app.project.team_id == token.team_id
                }
                allowed.update(
                    share.registered_app_id
                    for share in self.shares
                    if share.team_id == token.team_id and share.access_level in share_levels(permission)
                )
            authority.credential_apps[permission] = allowed
        return authority


def _page_session(stored, actor):
    """Reuse the configured ModelBackend's hash checks with the hydrated current user."""
    from django.conf import settings
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user
    from django.contrib.auth.backends import ModelBackend
    from django.utils.crypto import constant_time_compare

    from core.current_session import _ReadOnlyAuthSession

    store = _ReadOnlyAuthSession(stored.session_key)
    store._session_cache = stored.get_decoded()
    backend = store.get(BACKEND_SESSION_KEY)
    if backend not in settings.AUTHENTICATION_BACKENDS:
        return None
    if backend != "django.contrib.auth.backends.ModelBackend":
        user = get_user(SimpleNamespace(session=store))
        return (
            store
            if user.is_authenticated and user.pk == actor.pk and store.session_key == stored.session_key
            else None
        )
    try:
        user_id = actor._meta.pk.to_python(store.get(SESSION_KEY))
    except (TypeError, ValueError):
        return None
    if user_id != actor.pk or not ModelBackend().user_can_authenticate(actor):
        return None
    session_hash = store.get(HASH_SESSION_KEY)
    if not session_hash:
        return None
    current_hash = actor.get_session_auth_hash()
    if not constant_time_compare(session_hash, current_hash):
        if not any(
            constant_time_compare(session_hash, fallback)
            for fallback in actor.get_session_auth_fallback_hash()
        ):
            return None
        store[HASH_SESSION_KEY] = current_hash
    return store


def eligible_page_vote_counts(rows):
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import AstroliftSession
    from astrolift_services.models import ModelConnectionApproval
    from core.tenancy import tenant_context

    counts = {row.pk: 0 for row in rows}
    by_id = {row.pk: row for row in rows}
    votes = list(
        ModelConnectionApproval.objects.filter(request_id__in=by_id).select_related(
            "voter", "api_token", "session"
        )
    )
    if not votes:
        return counts
    environments = [row.app_environment for row in rows]
    facts = VotePrincipalFacts(votes, environments)
    now = timezone.now()
    keys = {vote.session.session_key for vote in votes if vote.session_id and not vote.api_token_id}
    sessions = {
        row.session_key: row for row in Session.objects.filter(session_key__in=keys, expire_date__gt=now)
    }
    sidecars = list(AstroliftSession.all_objects.filter(session_key__in=keys))
    invalid_keys = {
        sidecar.session_key
        for sidecar in sidecars
        if sidecar.deleted_at is not None
        or sidecar.revoked_at is not None
        or sidecar.expires_at is not None
        and sidecar.expires_at <= now
        or any(
            vote.session_id
            and vote.session.session_key == sidecar.session_key
            and vote.voter_id != sidecar.user_id
            for vote in votes
        )
    }
    session_created = {}
    for sidecar in sidecars:
        if sidecar.session_key not in invalid_keys:
            session_created[sidecar.session_key] = max(
                session_created.get(sidecar.session_key, sidecar.created_at), sidecar.created_at
            )
    principals = {}
    seen = {row.pk: set() for row in rows}
    for vote in votes:
        row = by_id[vote.request_id]
        actor, token = vote.voter, vote.api_token
        if not actor.is_active or vote.voter_id == row.requester_id and not row.allow_self_approval:
            continue
        if not actor.is_superuser and actor.pk not in facts.groups:
            continue
        if token is not None:
            if (
                actor.pk not in facts.groups
                or token.deleted_at is not None
                or token.is_revoked
                or token.user_id != actor.pk
                or token.organization_id != row.organization_id
                or token.expires_at is not None
                and token.expires_at <= now
            ):
                continue
            identity = (actor.pk, "token", token.pk)
            request = SimpleNamespace(user=actor, session={}, META={})
        else:
            if vote.session_id is None or vote.session.user_id != actor.pk:
                continue
            key = vote.session.session_key
            stored = sessions.get(key)
            if stored is None or key in invalid_keys:
                continue
            identity = (actor.pk, "session", key)
            if identity in principals:
                request = principals[identity][0]
            else:
                bag = _page_session(stored, actor)
                if bag is None:
                    continue
                request = SimpleNamespace(user=actor, session=bag, META={})
        if identity not in principals:
            principals[identity] = (
                request,
                facts.authority(
                    actor,
                    token,
                    request,
                    session_created_at=session_created.get(
                        vote.session.session_key if token is None else None
                    ),
                ),
            )
        authority = principals[identity][1]
        marker = set_current_api_token(token)
        try:
            with tenant_context(authority.tenant):
                authority.approver(row.app_environment)
            seen[row.pk].add(actor.pk)
        except PermissionDenied:
            pass
        finally:
            reset_current_api_token(marker)
    return {pk: len(voters) for pk, voters in seen.items()}


def project_request_page(info, rows, *, review=False):
    from copy import copy

    from astrolift_clusters.models import TenantCluster
    from astrolift_services.cluster_models import available_model_clusters, live_cluster_models
    from astrolift_services.model_connection_policy import effective_policy
    from astrolift_services.model_connection_requests import reviewed_versions
    from astrolift_services.models import ManagedService
    from astrolift_services.schema.model_connections import _request_type
    from astrolift_services.schema.model_types import dedicated_apps_for_services

    environments = []
    services = {}
    for row in rows:
        environments.append(row.app_environment)
        services[row.model_deployment_id] = row.model_deployment
    authority = PageAuthority(info, environments)
    facts = policy_page_facts(authority.tenant.organization_id)
    sources = source_page_facts(services.values())
    live_models = set(
        live_cluster_models(ManagedService.objects.filter(pk__in=services), authority.tenant.organization_id)
        .filter(
            tenant_cluster__in=available_model_clusters(
                TenantCluster.objects.all(), authority.tenant.organization_id
            )
        )
        .values_list("pk", flat=True)
    )
    dedicated = dedicated_apps_for_services(list(services.values()))
    votes = eligible_page_vote_counts(rows)
    projected = []
    for row in rows:
        service, env = row.model_deployment, row.app_environment
        count = votes[row.pk]
        own = row.requester_id == authority.actor.pk
        approve = cancel = finalize = False
        current = copy(row)
        try:
            if (
                row.model_deployment_id not in live_models
                or service.organization_id != row.organization_id
                or service.tenant_cluster_id != row.tenant_cluster_id
                or env.deleted_at is not None
                or env.registered_app_id != row.registered_app_id
                or env.tenant_cluster_id != row.tenant_cluster_id
                or row.tenant_cluster.provider_plugin_id != row.provider_plugin_id
                or row.registered_app.provisioning_status in ("tearing_down", "deregistered")
                or (service.config or {}).get("sharing_mode", "shared") != "shared"
                and (dedicated.get(service.pk) is None or dedicated[service.pk].pk != row.registered_app_id)
            ):
                raise PermissionDenied(Permission.APP_UPDATE, None, "model target is unavailable")
            permission = Permission.APP_APPROVE_DEPLOY if review else Permission.APP_UPDATE
            minimum = authority.admission(env, permission, request_only=not review)
            policy = effective_policy(service, approval_minimum=minimum, page_facts=facts)
            if (
                row.subscription_id is None
                and row.status in ("pending", "approved")
                and (
                    row.policy_version != policy.version
                    or row.reviewed_versions != reviewed_versions(service, env)
                    or not sources.get(service.pk)
                )
            ):
                current.status = "stale"
            if current.status == "pending" and (not own or current.allow_self_approval):
                try:
                    authority.approver(env)
                    approve = True
                except PermissionDenied:
                    pass
            if review:
                authority.approver(env)
            cancel = own and current.status in ("pending", "approved") and not current.subscription_id
            finalize = (
                own
                and current.status == "approved"
                and not current.subscription_id
                and count >= current.required_approvals
            )
        except PermissionDenied:
            approve = cancel = finalize = False
        projected.append(
            _request_type(current, approve=approve, cancel=cancel, finalize=finalize, approval_count=count)
        )
    return projected
