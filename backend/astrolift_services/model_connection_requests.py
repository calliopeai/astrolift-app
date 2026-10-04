"""Locked request decisions never create access before current-owner finalization."""

from dataclasses import replace
from types import SimpleNamespace

from django.utils import timezone

from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken, AstroliftSession
from astrolift_identity.scopes import identity_organization_scope
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import live_app_owners
from astrolift_services.model_connection_policy import (
    admission,
    effective_policy,
    fresh_actor,
    locked_organization,
)
from astrolift_services.model_settings import allows_app
from astrolift_services.models import ModelConnectionRequest
from core.current_credential import current_dispatch_credential
from core.permissions import Permission, PermissionDenied, check_permission
from core.tenancy import TenantContext, get_current_tenant, tenant_context


class ConnectionUnavailable(ValueError):
    pass


def locked_targets(
    info, input, *, ready=True, request_only=True, approvals=0, permission=Permission.APP_UPDATE
):
    from astrolift_services.schema.cluster_model_mutations import _idle, _locked_model, _ModelIdentity
    from astrolift_services.schema.model_types import cluster_model_to_type

    locked_organization()
    service = _locked_model(
        _ModelIdentity(
            input.model_deployment_id,
            input.organization_id,
            input.expected_cluster_id,
            input.expected_provider_id,
        )
    )
    if service is None:
        raise ConnectionUnavailable("Model connection target is unavailable.")
    org_id = get_current_tenant().organization_id
    initial = (
        AppEnvironment.objects.filter(
            guid=str(input.app_environment_id),
            registered_app__organization_id=org_id,
        )
        .values_list("pk", "registered_app_id")
        .first()
    )
    if initial is None:
        raise ConnectionUnavailable("Model connection target is unavailable.")
    app = live_app_owners(
        RegisteredApp.objects.select_for_update(of=("self",)).filter(pk=initial[1], organization_id=org_id)
    ).first()
    env = (
        AppEnvironment.objects.select_for_update().filter(pk=initial[0], registered_app=app).first()
        if app
        else None
    )
    if (
        env is None
        or env.tenant_cluster_id != service.tenant_cluster_id
        or app.provisioning_status in ("tearing_down", "deregistered")
        or not allows_app(service, app)
    ):
        raise ConnectionUnavailable("Model or destination is unavailable for this app.")
    env.registered_app = app
    env.tenant_cluster = service.tenant_cluster
    if ready and (
        not _idle(service)
        or service.status != "active"
        or not cluster_model_to_type(service).ready
        or (service.config or {}).get("allow_subscriptions") is not True
    ):
        raise ConnectionUnavailable("Model connection target is not ready for subscriptions.")
    if ready and not source_current(service):
        raise ConnectionUnavailable("Reviewed model source is unavailable.")
    need = admission(info, env, permission, request_only=request_only, approvals=approvals)
    policy = effective_policy(service, approval_minimum=need)
    if ready and policy.mode == "DENY":
        raise ConnectionUnavailable("Organization policy denies model connections.")
    return service, env, policy


def reviewed_versions(service, env):
    app = env.registered_app
    config = service.config or {}
    source = {
        "kind": config.get("model_source") or "huggingface",
        "model_repo": config.get("model"),
        "revision_sha": config.get("model_revision"),
        "local_artifact_id": config.get("model_artifact_id"),
        "local_artifact_version": config.get("model_artifact_version"),
        "local_manifest_sha256": config.get("model_artifact_manifest_sha256"),
        "connection_id": str(service.model_hf_connection.guid) if service.model_hf_connection_id else None,
        "connection_version": service.model_hf_connection_version,
    }
    return {
        "source": source,
        "model": service.version,
        "app": app.version,
        "environment": env.version,
        "cluster": service.tenant_cluster.version,
        "provider": service.tenant_cluster.provider_plugin.version,
        "app_team": app.team_id,
        "app_project": app.project_id,
    }


def source_current(service):
    config = service.config or {}
    if config.get("model_source") == "local_artifact":
        from _sdk.local_model_artifact import local_source_identity

        from astrolift_services.models import LocalModelArtifact

        try:
            identity, version, digest = local_source_identity(config)
        except ValueError:
            return False
        return LocalModelArtifact.objects.filter(
            guid=identity,
            version=version,
            organization_id=service.organization_id,
            state="verified",
            manifest_sha256=digest,
        ).exists()
    if config.get("model_source") not in (None, "huggingface"):
        return False
    if service.model_hf_connection_id:
        from astrolift_services.models import HuggingFaceConnection

        return HuggingFaceConnection.objects.filter(
            pk=service.model_hf_connection_id,
            organization_id=service.organization_id,
            version=service.model_hf_connection_version,
        ).exists()
    return config.get("hf_token_secret_ref") is None


def request_input(row):
    from astrolift_graphql import GUID
    from astrolift_services.schema.cluster_model_mutations import SubscribeClusterModelInput

    return SubscribeClusterModelInput(
        organization_id=GUID(str(row.organization.guid)),
        model_deployment_id=GUID(str(row.model_deployment.guid)),
        expected_cluster_id=GUID(str(row.tenant_cluster.guid)),
        expected_provider_id=GUID(str(row.provider_plugin.guid)),
        app_environment_id=GUID(str(row.app_environment.guid)),
        alias=row.alias,
        if_match_version=row.reviewed_versions["model"],
        if_match_environment_version=row.reviewed_versions["environment"],
    )


def locked_request(info, input, *, ready=False, permission=Permission.APP_UPDATE):
    tenant = get_current_tenant()
    initial = request_rows().filter(guid=str(input.id)).first()
    if initial is None:
        raise ConnectionUnavailable("Model connection request is unavailable.")
    # Model/app/environment order matches subscriptions and model access updates.
    service, env, policy = locked_targets(
        info,
        request_input(initial),
        ready=ready,
        permission=permission,
        request_only=permission == Permission.APP_UPDATE,
    )
    row = request_rows().select_for_update(of=("self",)).filter(pk=initial.pk).first()
    if row is None or row.organization_id != tenant.organization_id:
        raise ConnectionUnavailable("Model connection request is unavailable.")
    minimum = admission(info, env, permission, request_only=permission == Permission.APP_UPDATE)
    policy = effective_policy(service, approval_minimum=minimum)
    return row, service, env, policy


def request_rows():
    tenant = get_current_tenant()
    return ModelConnectionRequest.objects.filter(
        organization_id=tenant.organization_id if tenant else None,
        organization__deleted_at__isnull=True,
    ).select_related(
        "organization",
        "requester",
        "model_deployment",
        "registered_app",
        "app_environment",
        "tenant_cluster",
        "provider_plugin",
        "subscription",
    )


def stale_request(row, service, env, policy):
    if row.subscription_id is not None or row.status not in ("pending", "approved"):
        return False
    if (
        row.policy_version != policy.version
        or row.reviewed_versions != reviewed_versions(service, env)
        or not source_current(service)
    ):
        row.status = "stale"
        row.decided_at = timezone.now()
        row.save(update_fields=["status", "decided_at", "updated_at", "version"])
        return True
    return False


def approver_admission(info, env):
    actor = fresh_actor(info)
    with current_dispatch_credential(Permission.ORG_UPDATE):
        attrs = replace(
            abac.attributes_from_request(
                SimpleNamespace(
                    META=getattr(info.context.request, "META", {}),
                    session=info.context.request._model_connection_auth_session,
                ),
                actor.pk,
            ),
            cache={},
            environment=env.name,
            region=env.tenant_cluster.region or None,
            approvals=0,
            approval_request=False,
        )
        with abac.request_attributes(attrs):
            check_permission(
                Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE)({})
            )
    admission(info, env, Permission.APP_APPROVE_DEPLOY, request_only=False)
    return actor


def vote_credential(info):
    token = get_current_api_token()
    if token is not None:
        return {"api_token": ApiToken.objects.get(pk=token.pk)}
    key = getattr(getattr(info.context.request, "session", None), "session_key", None)
    session = AstroliftSession.objects.filter(
        session_key=key, user_id=get_current_tenant().actor_user_id, revoked_at__isnull=True
    ).first()
    if session is None:
        raise ConnectionUnavailable("Current reviewer session is unavailable.")
    return {"session": session}


def eligible_vote_count(row, env):
    count = 0
    for vote in row.approval_votes.select_related("voter", "api_token", "session"):
        if vote.voter_id == row.requester_id and not row.allow_self_approval:
            continue
        token = vote.api_token
        if token is None and vote.session is None:
            continue
        if token is None:
            from django.contrib.sessions.backends.db import SessionStore

            session = vote.session
            if (
                session.deleted_at is not None
                or session.revoked_at is not None
                or session.user_id != vote.voter_id
            ):
                continue
            request = SimpleNamespace(user=vote.voter, session=SessionStore(session.session_key), META={})
        else:
            request = SimpleNamespace(user=vote.voter, META={})
        info = SimpleNamespace(context=SimpleNamespace(request=request))
        marker = set_current_api_token(token)
        try:
            with tenant_context(
                TenantContext(organization_id=row.organization_id, actor_user_id=vote.voter_id)
            ):
                with abac.request_attributes(abac.attributes_from_request(request, vote.voter_id)):
                    approver_admission(info, env)
            count += 1
        except (PermissionDenied, ConnectionUnavailable):
            continue
        finally:
            reset_current_api_token(marker)
    return count


def connection_effect_checkpoint(info, row, service, env):
    """Recheck after every subscription lock; a detached count is never authority."""
    if row.requester_id != get_current_tenant().actor_user_id:
        raise PermissionDenied(Permission.APP_UPDATE, None, "requester is unavailable")
    need = admission(info, env, request_only=True)
    policy = effective_policy(service, approval_minimum=need)
    if stale_request(row, service, env, policy):
        return False
    count = eligible_vote_count(row, env)
    if row.status != "approved" or count < row.required_approvals or policy.mode != "REQUIRE_APPROVAL":
        row.status = "stale"
        row.decided_at = timezone.now()
        row.save(update_fields=["status", "decided_at", "updated_at", "version"])
        return False
    # Discard the pre-wait operation's count and request-only deferral explicitly.
    admission(info, env, approvals=count, request_only=False)
    return True
