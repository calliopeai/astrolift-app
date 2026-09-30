"""Tool pre-approval over the organization's authenticated Zentinelle channel."""

from urllib.parse import quote

from astrolift_operations.zentinelle_connect import ZentinelleConnectError, _install_call


class AgentHostPolicyError(ValueError):
    pass


def _connection(task):
    if not task.model_gateway_agent_id and task.model_gateway_connection_id is None:
        return None
    connection = task.model_gateway_connection
    if (
        connection is None
        or connection.organization_id != task.organization_id
        or len(connection.tenant_ids) != 1
        or not task.model_gateway_agent_id
    ):
        raise AgentHostPolicyError("Zentinelle identity is incomplete; the tool remains blocked")
    return connection


def evaluate(task, event, approval_token=None):
    connection = _connection(task)
    if connection is None:
        return {
            "decision": "ask",
            "reason": "Human approval required; this task has no Zentinelle connection",
            "configured": False,
        }
    tool = event.request["tool"]
    context = {
        "harness": str(task.environment_spec.agent_type) if task.environment_spec_id else "astrolift",
        "session_id": f"ahp-session:/{task.guid}",
        "chat_id": f"ahp-session:/{task.guid}/chat",
        "tool_call_id": (event.data or {}).get("call_id") or f"request-{event.sequence}",
        "tool_name": tool["name"],
        "tool_input": tool["input"],
    }
    if approval_token:
        context["approval_token"] = approval_token
    body = {
        "tenant_id": connection.tenant_ids[0],
        "agent_id": task.model_gateway_agent_id,
        "action": "tool_call",
        "user_id": str(task.triggered_by_user_id or task.created_by_id or ""),
        "context": context,
    }
    try:
        reply = _install_call(
            connection, "POST", f"/agents/{quote(task.model_gateway_agent_id, safe='')}/evaluate", body
        )
    except ZentinelleConnectError as exc:
        raise AgentHostPolicyError("Zentinelle evaluation is unavailable; the tool remains blocked") from exc
    verdict = reply.body
    if not reply.ok or verdict.get("decision") not in {"allow", "deny", "ask"}:
        raise AgentHostPolicyError("Zentinelle did not authorize this tool call; the tool remains blocked")
    return {
        "decision": verdict["decision"],
        "reason": str(verdict.get("reason") or ""),
        "configured": True,
        "trace_id": verdict.get("trace_id"),
        "approval": verdict.get("approval"),
        "enforcement": verdict.get("enforcement"),
    }


def guard_approval(task, event, author):
    verdict = evaluate(task, event)
    if not verdict["configured"] or verdict["decision"] == "allow":
        return
    if verdict["decision"] == "deny":
        raise AgentHostPolicyError(f"Zentinelle denied: {verdict['reason'] or 'Policy denied this tool'}")
    if author is None or not getattr(author, "is_authenticated", False):
        raise AgentHostPolicyError("Zentinelle requires an authenticated controller's approval")
    held = verdict.get("approval") or {}
    request_id = held.get("request_id")
    if not isinstance(request_id, str):
        raise AgentHostPolicyError("Zentinelle did not issue an approval request; the tool remains blocked")
    connection = _connection(task)
    try:
        reply = _install_call(
            connection,
            "POST",
            f"/agents/{quote(task.model_gateway_agent_id, safe='')}/approvals/{quote(request_id, safe='')}/decision",
            {
                "tenant_id": connection.tenant_ids[0],
                "decision": "approve",
                "actor_id": str(author.pk),
                "reason": "Approved by the Astrolift controller",
            },
        )
    except ZentinelleConnectError as exc:
        raise AgentHostPolicyError("Zentinelle approval is unavailable; the tool remains blocked") from exc
    token = reply.body.get("approval_token")
    if not reply.ok or not isinstance(token, str):
        raise AgentHostPolicyError("Zentinelle did not confirm the approval; the tool remains blocked")
    final = evaluate(task, event, token)
    if final["decision"] != "allow":
        raise AgentHostPolicyError(
            f"Zentinelle denied after approval: {final['reason'] or 'Policy no longer authorizes the tool'}"
        )
