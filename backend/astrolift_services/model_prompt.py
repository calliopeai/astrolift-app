"""Advisory readiness for the existing bounded in-cluster prompt relay."""

from enum import StrEnum
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from astrolift_clusters.heartbeat_status import HeartbeatStatus, is_live, resolve
from astrolift_services.models import ManagedService
from astrolift_services.scopes import _org_id, live_managed_services


class PromptReadinessState(StrEnum):
    READY = "ready"
    UNSUPPORTED = "unsupported"
    INACTIVE = "inactive"
    UNAVAILABLE = "unavailable"
    UNKNOWN_HEARTBEAT = "unknown_heartbeat"
    STALE_HEARTBEAT = "stale_heartbeat"
    UNCONFIGURED_RELAY = "unconfigured_relay"
    UNCONFIGURED_MODEL = "unconfigured_model"


def live_model_prompt_service(guid):
    try:
        parsed = UUID(str(guid))
    except (ValueError, TypeError, AttributeError):
        return None
    return (
        live_managed_services(ManagedService.objects.all())
        .filter(
            Q(registered_app__organization_id=_org_id()) | Q(project__organization_id=_org_id()), guid=parsed
        )
        .select_related("registered_app__organization", "app_environment__tenant_cluster", "tenant_cluster")
        .first()
    )


def prompt_readiness(service):
    """A READY result permits an attempt; no heartbeat advertises relay support.

    The mutation and the model/agent still determine the real outcome. No
    provider request or cache read occurs while computing these persisted facts.
    """
    if (
        service.kind != ManagedService.Kind.MODEL_ENDPOINT
        or service.variant != "vllm"
        or service.registered_app_id is None
    ):
        return PromptReadinessState.UNSUPPORTED
    if service.status != ManagedService.Status.ACTIVE:
        return PromptReadinessState.INACTIVE
    cluster = service.app_environment.tenant_cluster if service.app_environment_id else None
    if cluster is None or not cluster.is_active:
        return PromptReadinessState.UNAVAILABLE
    heartbeat = resolve(
        last_heartbeat_at=cluster.last_heartbeat_at,
        interval_seconds=cluster.heartbeat_interval_seconds,
        now=timezone.now(),
    )
    if heartbeat == HeartbeatStatus.NEVER_SEEN:
        return PromptReadinessState.UNKNOWN_HEARTBEAT
    if not is_live(heartbeat):
        return PromptReadinessState.STALE_HEARTBEAT
    config = cluster.provider_config if isinstance(cluster.provider_config, dict) else {}
    relay = config.get("vllm_agent_test")
    namespace = relay.get("namespace") if isinstance(relay, dict) else None
    if not isinstance(namespace, str) or not namespace.strip():
        return PromptReadinessState.UNCONFIGURED_RELAY
    config = service.config if isinstance(service.config, dict) else {}
    model = config.get("model")
    if not isinstance(model, str) or not model.strip():
        return PromptReadinessState.UNCONFIGURED_MODEL
    return PromptReadinessState.READY
