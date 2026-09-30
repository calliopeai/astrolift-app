"""AHP terminal attachments over the existing exec backend.

Each actor/client gets its own PTY view of a box's tmux session. This avoids
mixing duplicate output from independent exec attachments into one stream.
The durable channel survives reconnect; detaching closes only the exec view.
"""

import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from astrolift_agents.models import AgentBox, AgentHostAction, AgentHostAuthority, AgentHostTerminal
from astrolift_agents.services.agent_cluster import resolve_agent_cluster
from astrolift_agents.services.agent_host_projection import append_action, envelope
from astrolift_operations.models import AuditEvent
from core.cluster_management import _context_for_cluster, _driver_for_cluster
from core.permissions import Permission, PermissionDenied, check_permission

MAX_CONTENT = 128 * 1024


def channel(box, user, client):
    digest = hashlib.sha256(f"{box.guid}:{user.pk}:{client}".encode()).hexdigest()
    return f"ahp-terminal:/{box.guid}/{digest}"


def boxes(org):
    check_permission(Permission.AGENT_BOX_ATTACH)
    return AgentBox.objects.filter(organization_id=org).select_related(
        "organization", "model_gateway_connection"
    )


def availability(box):
    missing = []
    if box.status != "running" or not box.external_id or not box.pod_name or not box.namespace:
        missing.append("running box pod")
    if (
        not box.model_gateway_agent_id
        or box.model_gateway_connection_id is None
        or not box.model_gateway_expires_at
        or box.model_gateway_expires_at <= timezone.now()
    ):
        missing.append("live model gateway identity")
    connection = box.model_gateway_connection
    if connection is not None and (
        connection.organization_id != box.organization_id or connection.status != "connected"
    ):
        missing.append("active model gateway connection")
    if not box.pod_name or not box.namespace:
        return {
            "ahp_available": False,
            "reason": "Missing: " + ", ".join(missing),
            "fallback": "agent_task.watch",
        }
    try:
        cluster = resolve_agent_cluster(box.organization)
        driver, ctx = _driver_for_cluster(cluster), _context_for_cluster(cluster)
        pod = driver.get_manifest(ctx.slug, box.namespace, "Pod", box.pod_name) or {}
        runtime = pod.get("spec", {}).get("runtimeClassName")
        registered = driver.get_manifest(ctx.slug, None, "RuntimeClass", runtime) if runtime else None
        if (
            not registered
            or registered.get("handler") != "runsc"
            or registered.get("metadata", {}).get("deletionTimestamp")
        ):
            missing.append("gVisor RuntimeClass (runsc)")
        if (
            pod.get("metadata", {}).get("deletionTimestamp")
            or pod.get("status", {}).get("phase") != "Running"
        ):
            missing.append("live pod")
        fence = driver.get_manifest(ctx.slug, box.namespace, "NetworkPolicy", "astrolift-agent-fence") or {}
        spec = fence.get("spec", {})
        from constance import config

        from astrolift_dispatch.agent_network_fence import render_agent_fence

        expected = render_agent_fence(
            namespace=box.namespace,
            provider_slug=cluster.provider_plugin.slug,
            allow_cidrs=str(config.AGENT_EGRESS_ALLOW_CIDRS or ""),
            zentinelle_gateway=True,
        )["spec"]

        def rules(values):
            return sorted(json.dumps(rule, sort_keys=True) for rule in values)

        if (
            fence.get("metadata", {}).get("deletionTimestamp")
            or pod.get("metadata", {}).get("labels", {}).get("astrolift.dev/workload-kind") != "agent-box"
            or spec.get("podSelector") != expected["podSelector"]
            or set(spec.get("policyTypes", [])) != {"Ingress", "Egress"}
            or spec.get("ingress") != []
            or rules(spec.get("egress", [])) != rules(expected["egress"])
        ):
            missing.append("agent network fence")
        gateway = driver.get_manifest(ctx.slug, "astrolift-system", "Deployment", "zentinelle-gateway") or {}
        if (
            gateway.get("metadata", {}).get("deletionTimestamp")
            or gateway.get("status", {}).get("availableReplicas", 0) < 1
        ):
            missing.append("ready model gateway")
    except Exception:
        missing.append("verifiable live cluster controls")
    return (
        {"ahp_available": False, "reason": "Missing: " + ", ".join(missing), "fallback": "agent_task.watch"}
        if missing
        else {"ahp_available": True}
    )


def resolve(host, uri, *, hardened=True):
    from astrolift_agents.services.agent_host import HostError

    try:
        rows = boxes(host.tenant.organization_id)
    except PermissionDenied:
        raise HostError("Terminal unavailable or permission denied", -32002) from None
    for box in rows:
        if channel(box, host.user, host.client_id) == uri:
            status = availability(box) if hardened else {}
            if status.get("ahp_available") is False:
                raise HostError("Box live attach unavailable", -32003, status)
            return box
    raise HostError("Terminal unavailable or permission denied", -32002)


def catalog(host):
    from core.permissions import PermissionDenied

    try:
        rows = boxes(host.tenant.organization_id)
    except PermissionDenied:
        return []
    return [
        {
            "resource": channel(box, host.user, host.client_id),
            "title": box.name or box.slug,
            "claim": {"kind": "client", "clientId": host.client_id},
            "lifecycle": {"status": "running" if box.status == "running" else "exited"},
            "_meta": availability(box),
        }
        for box in rows
    ]


@transaction.atomic
def snapshot(host, uri):
    box = resolve(host, uri)
    AgentHostAuthority.objects.get_or_create(organization_id=host.tenant.organization_id)
    authority = AgentHostAuthority.objects.select_for_update().get(
        organization_id=host.tenant.organization_id
    )
    row, _ = AgentHostTerminal.objects.get_or_create(
        authority=authority,
        agent_box=box,
        actor=host.user,
        client_id=host.client_id,
        defaults={
            "channel": uri,
            "state": {
                "title": box.name or box.slug,
                "content": [],
                "claim": {"kind": "client", "clientId": host.client_id},
                "lifecycle": {"status": "running"},
                "isPty": True,
                "supportsCommandDetection": False,
            },
        },
    )
    return {"resource": uri, "state": row.state, "fromSeq": authority.server_sequence}


@transaction.atomic
def acquire(host, uri, owner):
    box = resolve(host, uri)
    row = AgentHostTerminal.objects.select_for_update().get(channel=uri, actor=host.user, agent_box=box)
    if row.lease_owner and row.lease_owner != owner and row.lease_expires_at > timezone.now():
        from astrolift_agents.services.agent_host import HostError

        raise HostError("This client already has an attached terminal; use a distinct clientId")
    row.lease_owner, row.lease_expires_at = owner, timezone.now() + timedelta(seconds=30)
    row.state["lifecycle"] = {"status": "running"}
    row.save()
    AuditEvent.objects.create(
        organization_id=host.tenant.organization_id,
        actor_kind="user",
        actor_id=str(host.user.pk),
        action="agent_host.terminal.attach",
        decision="ALLOW",
        target_kind="agent_box",
        data={"box_id": str(box.guid), "channel": uri},
    )
    return box


def release(host, owner, uri=None):
    rows = AgentHostTerminal.objects.filter(
        actor=host.user, authority__organization_id=host.tenant.organization_id, lease_owner=owner
    )
    if uri is not None:
        rows = rows.filter(channel=uri)
    rows.update(lease_owner=None, lease_expires_at=None)


def renew(host, uri, owner):
    resolve(host, uri)
    return (
        AgentHostTerminal.objects.filter(channel=uri, actor=host.user, lease_owner=owner).update(
            lease_expires_at=timezone.now() + timedelta(seconds=30)
        )
        == 1
    )


@transaction.atomic
def emit(host, uri, owner, action):
    row = AgentHostTerminal.objects.select_for_update().get(channel=uri, actor=host.user, lease_owner=owner)
    authority = AgentHostAuthority.objects.select_for_update().get(pk=row.authority_id)
    if action["type"] == "terminal/data":
        previous = "".join(p["value"] for p in row.state["content"])
        row.state["content"] = [{"type": "unclassified", "value": (previous + action["data"])[-MAX_CONTENT:]}]
    elif action["type"] == "terminal/exited":
        row.state["lifecycle"] = {
            "status": "exited",
            **({"exitCode": action["exitCode"]} if "exitCode" in action else {}),
        }
    row.save()
    event = append_action(authority, None, uri, action)
    event.agent_box = row.agent_box
    event.save(update_fields=["agent_box"])
    authority.save()
    return envelope(event)


@transaction.atomic
def reserve_input(host, params):
    from astrolift_agents.services.agent_host import HostError

    host._module()
    uri, seq, action = params.get("channel"), params.get("clientSeq"), params.get("action")
    box = resolve(host, uri)
    if type(seq) is not int or not 1 <= seq <= 2**53 - 1 or not isinstance(action, dict):
        raise HostError("Terminal dispatch requires an action and positive clientSeq")
    if action.get("type") == "terminal/input":
        if (
            set(action) != {"type", "data"}
            or not isinstance(action["data"], str)
            or len(action["data"]) > 16384
        ):
            raise HostError("Terminal input must be bounded text")
    elif action.get("type") == "terminal/resized":
        if set(action) != {"type", "rows", "cols"} or not all(
            type(action[k]) is int and 1 <= action[k] <= 1000 for k in ("rows", "cols")
        ):
            raise HostError("Terminal dimensions must be integers from 1 to 1000")
    else:
        raise HostError("Only input and resize are supported for a box attachment")
    authority = AgentHostAuthority.objects.select_for_update().get(
        organization_id=host.tenant.organization_id
    )
    previous = AgentHostAction.objects.filter(
        authority=authority, actor=host.user, client_id=host.client_id, client_sequence=seq
    ).first()
    if previous:
        if previous.channel != uri or previous.action != action:
            raise HostError("clientSeq already identifies another action")
        return previous.pk, envelope(previous), False
    row = append_action(
        authority,
        None,
        uri,
        action,
        actor=host.user,
        origin={"clientId": host.client_id, "clientSeq": seq},
        rejection="Input delivery was not confirmed; resubscribe before resending",
    )
    row.agent_box = box
    row.save(update_fields=["agent_box"])
    authority.save()
    return row.pk, envelope(row), True


@transaction.atomic
def finish_input(host, row_id, delivered):
    row = AgentHostAction.objects.select_for_update().get(
        pk=row_id, actor=host.user, authority__organization_id=host.tenant.organization_id
    )
    if delivered:
        row.rejection_reason = ""
        row.save(update_fields=["rejection_reason"])
        if row.action["type"] == "terminal/resized":
            terminal = AgentHostTerminal.objects.select_for_update().get(channel=row.channel, actor=host.user)
            terminal.state.update(rows=row.action["rows"], cols=row.action["cols"])
            terminal.save()
    AuditEvent.objects.create(
        organization_id=host.tenant.organization_id,
        actor_kind="user",
        actor_id=str(host.user.pk),
        action="agent_host.terminal.dispatch",
        decision="ALLOW" if delivered else "DENY",
        target_kind="agent_box",
        data={"channel": row.channel, "action_type": row.action["type"], "reason": row.rejection_reason},
    )
    return envelope(row)
