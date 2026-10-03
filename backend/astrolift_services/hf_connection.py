"""Fixed-origin bounded credential and repository-access reads; never download weights."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener

from astrolift_services import hf_catalogue
from astrolift_services.hf_catalogue import (
    MAX_BODY_BYTES,
    ORIGIN,
    TIMEOUT_SECONDS,
    CatalogueState,
    CatalogueUnavailable,
    _model,
    _NoRedirect,
    valid_repo_id,
)
from core.secrets import EncryptedSecret, decrypt


class HuggingFaceUnavailable(ValueError):
    """Safe diagnostic with no upstream response, credential or HTTP exception."""


def credential(connection):
    try:
        return validate_token(
            decrypt(
                EncryptedSecret(
                    backend_kind=connection.secret_backend_kind,
                    backend_ref=bytes(connection.secret_ciphertext),
                )
            ).decode("utf-8")
        )
    except Exception:
        raise HuggingFaceUnavailable(
            "The stored Hugging Face credential is unavailable. Reconnect."
        ) from None


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(r"hf_[A-Za-z0-9]{8,1024}", token):
        raise HuggingFaceUnavailable("Enter a valid Hugging Face read or fine-grained access token.")
    return token


def _read(path, token=None, *, status_only=False):
    headers = {"Accept": "application/json", "User-Agent": "Astrolift-model-access/1"}
    if token is not None:
        headers["Authorization"] = f"Bearer {validate_token(token)}"
    try:
        with build_opener(_NoRedirect()).open(
            Request(f"{ORIGIN}{path}", headers=headers),
            timeout=TIMEOUT_SECONDS,
        ) as response:
            if response.status != 200:
                raise HuggingFaceUnavailable("Hugging Face access could not be verified. Retry.")
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise HuggingFaceUnavailable("Hugging Face access could not be verified. Retry.")
            return None if status_only else json.loads(body) if body else {}
    except HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise HuggingFaceUnavailable(
                "Hugging Face denied access. Check the token's repository scope and request model access on Hugging Face."
            ) from None
        raise HuggingFaceUnavailable("Hugging Face access could not be verified. Retry.") from None
    except (URLError, OSError, ValueError) as exc:
        if isinstance(exc, HuggingFaceUnavailable):
            raise
        raise HuggingFaceUnavailable("Hugging Face access could not be verified. Retry.") from None


def verify_account(token):
    account = _read("/api/whoami-v2", validate_token(token))
    name = account.get("name") if isinstance(account, dict) else None
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", name):
        raise HuggingFaceUnavailable("Hugging Face account could not be verified. Retry.")
    return name


def verified_model(repo_id, revision_sha, *, token=None, checkpoint=None):
    if (
        not valid_repo_id(repo_id)
        or not isinstance(revision_sha, str)
        or not re.fullmatch(r"[a-f0-9]{40}", revision_sha)
    ):
        raise HuggingFaceUnavailable("Select an immutable Hugging Face repository revision.")
    repo = quote(repo_id, safe="/")
    if token is None:
        observed = hf_catalogue.model_detail(repo_id, revision_sha)
        item = observed.model if observed.state is CatalogueState.AVAILABLE else None
    else:
        row = _read(f"/api/models/{repo}/revision/{revision_sha}", token)
        try:
            item = _model(row, allow_private=True)
        except CatalogueUnavailable:
            raise HuggingFaceUnavailable(
                "The requested Hugging Face revision could not be verified."
            ) from None
    if item is None or item.repo_id != repo_id or item.revision_sha != revision_sha:
        raise HuggingFaceUnavailable("The requested Hugging Face revision could not be verified.")
    if checkpoint is not None:
        checkpoint()
    # Metadata can be readable while weights are gated. The Hub auth-check is
    # the separate authority check; it does not accept terms on a user's behalf.
    _read(f"/api/models/{repo}/auth-check", token, status_only=True)
    return item


def materialize_model_token(service, secrets_backend):
    """Copy only the admitted credential into this service's existing secret owner."""
    from astrolift_services.models import HuggingFaceConnection

    if service.model_hf_connection_id is None:
        return
    connection = (
        HuggingFaceConnection.objects.select_for_update()
        .filter(
            pk=service.model_hf_connection_id,
            organization_id=service.organization_id,
            organization__deleted_at__isnull=True,
        )
        .first()
    )
    path = f"services/{service.organization.guid}/{service.guid}/huggingface"
    if (
        connection is None
        or connection.version != service.model_hf_connection_version
        or service.config.get("hf_token_secret_ref") != f"{path}#token"
    ):
        raise HuggingFaceUnavailable("Shared model Hugging Face connection changed or is unavailable.")
    try:
        secrets_backend.upsert(path, {"token": credential(connection)})
    except Exception:
        raise HuggingFaceUnavailable("Hugging Face credential delivery is unavailable. Retry.") from None


def delete_model_token(service, secrets_backend):
    """Remove only this model's copied credential after confirmed deprovision."""
    if service.model_hf_connection_id is None:
        return
    path = f"services/{service.organization.guid}/{service.guid}/huggingface"
    if service.config.get("hf_token_secret_ref") != f"{path}#token":
        raise HuggingFaceUnavailable("Shared model Hugging Face credential owner is unavailable.")
    try:
        secrets_backend.delete(path)
    except Exception:
        raise HuggingFaceUnavailable("Hugging Face credential cleanup is unavailable. Retry.") from None
