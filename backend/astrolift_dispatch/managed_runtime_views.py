import json

from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from astrolift_agents.services.managed_runtime_authority import (
    RuntimeAuthorityError,
    authenticated_runtime,
    validate_runtime_authority,
)
from core.permissions import route_auth


def _error(code, message, status):
    return JsonResponse({"ok": False, "errors": [{"code": code, "message": message}]}, status=status)


@csrf_exempt
@require_POST
@route_auth(
    credential="box-scoped managed runtime bearer token, hashed at rest and expiring",
    scope="the exact live box, owner epoch, pod incarnation and persistent claim",
)
def validate_managed_runtime(request, box_id):
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    runtime = authenticated_runtime(box_id, token)
    if runtime is None:
        return _error("unauthorized", "Managed runtime authentication required.", 401)
    try:
        if request.content_type != "application/json" or len(request.body) > 4096:
            return _error("validation", "Invalid managed runtime request.", 400)
        payload = json.loads(request.body)
        value = validate_runtime_authority(runtime.pk, token, payload)
    except (ValueError, RequestDataTooBig) as error:
        if isinstance(error, RuntimeAuthorityError):
            return _error("conflict", str(error), 409)
        return _error("validation", "Invalid managed runtime request.", 400)
    except Exception:
        return _error("unavailable", "Managed runtime authority could not be verified.", 503)
    return JsonResponse({"ok": True, "errors": [], "data": value})
