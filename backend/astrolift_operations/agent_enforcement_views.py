"""Signed enforcement delivery using the enrolled install credential."""

import hmac
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from astrolift_agents.services.agent_enforcement import AgentEnforcementError, apply_signed
from astrolift_operations.models import ZentinelleConnection
from astrolift_operations.zentinelle_connect import ZentinelleConnectError, _install_credential
from core.permissions import route_auth


@csrf_exempt
@route_auth(
    credential="live enrolled install bearer plus signed expiring nonce envelope",
    scope="install connection and registered tenant; target task or box belongs to that connection's organization",
)
def agent_enforcement(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    if len(request.body) > 16 * 1024:
        return JsonResponse({"error": "Enforcement body is too large"}, status=413)
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return JsonResponse({"error": "Install authentication required"}, status=401)
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict) or not isinstance(body.get("install_id"), str):
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid enforcement JSON"}, status=400)
    presented = authorization[len("Bearer ") :].strip()
    for connection in ZentinelleConnection.objects.filter(
        zentinelle_install_id=body["install_id"], status="connected"
    ):
        try:
            if not hmac.compare_digest(_install_credential(connection), presented):
                continue
            result = apply_signed(connection, body, request.headers.get("X-Zentinelle-Signature", ""))
            return JsonResponse(result)
        except AgentEnforcementError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status)
        except ZentinelleConnectError:
            continue
        except (ValueError, TypeError, OverflowError):
            return JsonResponse({"error": "Invalid enforcement envelope"}, status=400)
    return JsonResponse({"error": "Invalid install credential"}, status=401)
