"""Bearer-only two-step source-build then fresh-person brain hosting."""

import functools

from django.core import signing
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from astrolift_lifecycle import builder_brain_source as source
from astrolift_lifecycle import builder_views
from astrolift_lifecycle.models import BuilderBrainBuild, DevEnvironment
from astrolift_registry.expiry import ExpiryError
from core.permissions import Permission, PermissionDenied, route_auth


def errors(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except source.SourceError as exc:
            return JsonResponse({"code": exc.code, "detail": exc.detail}, status=exc.status)
        except PermissionDenied:
            return JsonResponse(
                {"code": "PERMISSION_DENIED", "detail": "current scoped permission is required"}, status=403
            )
        except (ExpiryError, ValueError, TypeError, ValidationError):
            return JsonResponse(
                {"code": "VALIDATION", "detail": "invalid source, runtime, destination or expiry request"},
                status=400,
            )

    return wrapped


def auth(request):
    token = getattr(request, "_api_token", None)
    if token is None:
        raise source.SourceError("UNAUTHENTICATED", "a user API token is required", 401)
    org, err = builder_views._resolve_org(token)
    if err is not None:
        raise source.SourceError("PERMISSION_DENIED", "a current organization is required", 403)
    if builder_views._module_gate(org) is not None:
        raise source.SourceError("PERMISSION_DENIED", "the Chat Studio integration is unavailable", 403)
    return token, org


@require_GET
@route_auth(
    credential="api-token",
    permissions=(Permission.APP_CREATE,),
    scope="source live TEAM/ORG plus creator or administrator; bearer write:apps",
)
@errors
def descriptor(request, guid):
    token, org = auth(request)
    dev = DevEnvironment.objects.select_related("team").filter(guid=guid, organization_id=org.pk).first()
    if dev is None:
        raise source.SourceError("NOT_FOUND", "source environment not found", 404)
    source.source_owner(token, org, dev)
    data, _ = source.source_descriptor(dev)
    return JsonResponse({"protocol": source.PROTOCOL, **data})


def enqueue(row):
    from astrolift_lifecycle.builder_brain_dispatch import dispatch

    return dispatch(row.pk, "build")


@require_POST
@route_auth(
    credential="api-token",
    permissions=(Permission.APP_CREATE, Permission.APP_DEPLOY),
    scope="source live owner plus exact live project and readable brain; bearer write:apps",
)
@errors
def create(request, guid):
    token, org = auth(request)
    body, err = builder_views._load_json(request)
    if err is not None:
        return err
    row, _ = source.admit(request, token, org, guid, body)
    try:
        row = enqueue(row)
    except source.SourceError:
        raise
    except Exception:
        raise source.SourceError(
            "BUILD_DISPATCH_PENDING",
            "the durable intent exists; retry the identical request to dispatch it",
            503,
        ) from None
    return JsonResponse(source.response(row), status=202)


@require_GET
@route_auth(
    credential="api-token",
    permissions=(Permission.APP_CREATE, Permission.APP_DEPLOY),
    scope="original current bearer and source owner; exact live project and readable brain",
)
@errors
def status(request, intent_guid):
    token, org = auth(request)
    row = (
        BuilderBrainBuild.objects.select_related(
            "organization", "project", "brain", "hosted_app", "deployment__app_environment"
        )
        .filter(
            guid=intent_guid,
            organization_id=org.pk,
            actor_id=token.user_id,
            credential_id=token.pk,
        )
        .first()
    )
    if row is None:
        raise source.SourceError("NOT_FOUND", "source build not found", 404)
    source.validate(row)
    from astrolift_lifecycle.builder_brain_dispatch import dispatch

    if row.build_first_submission_at is not None:
        row = dispatch(row.pk, "build", recovery_only=True)
    if row.deployment_first_submission_at is not None:
        row = dispatch(row.pk, "deployment", recovery_only=True)
    return JsonResponse(source.response(row))


@require_POST
@route_auth(
    credential="api-token",
    permissions=(Permission.APP_CREATE, Permission.APP_DEPLOY),
    scope="fresh original person request; source owner; exact project app.create/deploy and readable brain; native origin preserved",
)
@errors
def host(request, intent_guid):
    token, org = auth(request)
    body, err = builder_views._load_json(request)
    if err is not None:
        return err
    if set(body) != {"expected_image_digest"} or not isinstance(body["expected_image_digest"], str):
        raise source.SourceError("VALIDATION", "only expected_image_digest is accepted", 400)
    row = source.host(request, token, org, intent_guid, body["expected_image_digest"])
    row.refresh_from_db()
    return JsonResponse(source.response(row), status=202)


@require_GET
@route_auth(
    credential="builder-build-archive",
    scope="bounded immutable archive grant for the current original build authority only",
)
def archive(request, intent_guid):
    from django.utils import timezone

    header = request.headers.get("Authorization", "")
    if not header.startswith("BuilderBuildArchive "):
        return HttpResponse(status=403)
    try:
        claim = signing.loads(
            header[len("BuilderBuildArchive ") :], salt=source.ARCHIVE_SALT, max_age=source.GRANT_SECONDS
        )
        if claim["intent"] != str(intent_guid):
            return HttpResponse(status=403)
        row = (
            BuilderBrainBuild.objects.select_related("organization", "project", "brain")
            .filter(guid=intent_guid, organization_id=claim["org"])
            .first()
        )
        if (
            row is None
            or row.status != "building"
            or row.build_deadline <= timezone.now()
            or claim["sha256"] != row.archive_sha256
        ):
            return HttpResponse(status=403)
        source.validate(row)
        result = FileResponse(
            source.builder_artifacts.storage().open(row.archive_key, "rb"), content_type="application/zip"
        )
        result["Cache-Control"] = "private, no-store"
        result["X-Content-Type-Options"] = "nosniff"
        return result
    except (
        signing.BadSignature,
        source.SourceError,
        PermissionDenied,
        ValueError,
        KeyError,
        TypeError,
        FileNotFoundError,
    ):
        return HttpResponse(status=403)
