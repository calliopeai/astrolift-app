"""Private, immutable builder bundles; Kubernetes carries only a download grant."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.http import FileResponse, HttpResponse
from django.views.decorators.http import require_GET

from core.permissions import route_auth

SALT = "astrolift.builder.artifact.v1"


def storage():
    return storages["builder_artifacts"]


def pack(dev):
    from .builder_views import _path_error, _tree_error

    files = dev.files or {}
    if _tree_error(files):
        raise ValueError("invalid builder file tree")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(files.items()):
            if _path_error(name):
                raise ValueError("invalid builder asset path")
            raw = (
                base64.b64decode(value["content"], validate=True)
                if isinstance(value, dict)
                else str(value).encode()
            )
            archive.writestr(zipfile.ZipInfo("app/" + name), raw, compress_type=zipfile.ZIP_DEFLATED)
        if dev.data_file_path:
            if _path_error(dev.data_file_path):
                raise ValueError("invalid builder data path")
            archive.writestr(
                zipfile.ZipInfo("data/" + dev.data_file_path),
                bytes(dev.data_file or b""),
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return output.getvalue()


def reference(dev):
    raw = pack(dev)
    digest = hashlib.sha256(raw).hexdigest()
    prefix = f"builder/{dev.organization_id}/{dev.guid}/"
    key = prefix + digest + ".zip"
    store = storage()
    if not store.exists(key):
        key = store.save(key, ContentFile(raw))
    token = signing.dumps(
        {"dev": str(dev.guid), "org": dev.organization_id, "key": key, "sha256": digest}, salt=SALT
    )
    base = settings.BUILDER_ARTIFACT_BASE_URL.rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("BUILDER_ARTIFACT_BASE_URL must be an operator-configured HTTP(S) origin")
    return {
        "url": base + f"/api/builder/v1/dev-environments/{dev.guid}/artifact/",
        "token": token,
        "sha256": digest,
        "bytes": len(raw),
        "expanded_bytes": sum(e.file_size for e in zipfile.ZipFile(io.BytesIO(raw)).infolist()),
    }


@require_GET
@route_auth(
    credential="builder-artifact",
    scope="signed immutable artifact for one live organization and dev environment",
)
def download(request, guid):
    from .models import DevEnvironment

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("BuilderArtifact "):
        return HttpResponse(status=403)
    try:
        claim = signing.loads(authorization[len("BuilderArtifact ") :], salt=SALT)
        if claim["dev"] != str(guid):
            raise ValueError("wrong environment")
        prefix = f"builder/{claim['org']}/{guid}/"
        key = claim["key"]
        if not key.startswith(prefix) or "/" in key[len(prefix) :] or ".." in key:
            raise ValueError("wrong artifact")
        dev = (
            DevEnvironment.all_objects.select_related("promoted_app")
            .filter(guid=guid, organization_id=claim["org"], organization__deleted_at__isnull=True)
            .first()
        )
        if dev is None:
            return HttpResponse(status=404)
        if (dev.deleted_at or dev.status == DevEnvironment.Status.TORN_DOWN) and not (
            dev.promoted_app_id and dev.promoted_app.deleted_at is None
        ):
            return HttpResponse(status=404)
        response = FileResponse(storage().open(key, "rb"), content_type="application/zip")
    except (signing.BadSignature, ValueError, KeyError, TypeError, FileNotFoundError):
        return HttpResponse(status=403)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def init_script():
    from . import builder_unpack

    return Path(builder_unpack.__file__).read_text()
