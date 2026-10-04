"""Bounded Bedrock metadata reads; neither registration nor invoke authority (#2269)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, cast

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from aws.session import aws_session

MAX_ITEMS = 500
MAX_PAGES = 5
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.:-]{0,139}\Z")
_REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d+\Z")
_ARN = re.compile(r"arn:(aws(?:-[a-z-]+)?):bedrock:([a-z0-9-]+):(\d{12}|):([^/]+)/(.+)\Z")


class CatalogueState(StrEnum):
    METADATA = "metadata"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    ERROR = "error"
    REFUSED = "refused"


class BedrockSourceKind(StrEnum):
    FOUNDATION_MODEL = "foundation_model"
    INFERENCE_PROFILE = "inference_profile"


class BedrockCatalogueError(ValueError):
    """Fixed reason only; never provider response bodies or credential material."""


@dataclass(frozen=True)
class BedrockCatalogueConfig:
    region: str
    credential: CloudCredential = field(repr=False)


@dataclass(frozen=True)
class BedrockCatalogueIdentity:
    account_id: str
    region: str
    partition: str


@dataclass(frozen=True)
class BedrockAvailability:
    """Native access prerequisites, never an IAM/inference/consumer-access proof."""

    state: CatalogueState
    authorization: str | None = None
    entitlement: str | None = None
    region: str | None = None
    agreement: str | None = None
    invoke_access: str = "unknown"


@dataclass(frozen=True)
class BedrockCatalogueSource:
    kind: BedrockSourceKind
    identifier: str
    arn: str
    name: str
    provider: str | None = None
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    inference_types: tuple[str, ...] = ()
    streaming: bool | None = None
    lifecycle: str | None = None
    profile_type: str | None = None
    destination_model_arns: tuple[str, ...] = ()
    availability: BedrockAvailability = field(default_factory=lambda: BedrockAvailability(CatalogueState.METADATA))


@dataclass(frozen=True)
class BedrockCataloguePage:
    state: CatalogueState
    identity: BedrockCatalogueIdentity | None = None
    items: tuple[BedrockCatalogueSource, ...] = ()
    truncated: bool = False
    partial: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class BedrockCatalogueDetail:
    state: CatalogueState
    identity: BedrockCatalogueIdentity | None = None
    source: BedrockCatalogueSource | None = None
    reason: str | None = None


class BedrockCatalogue:
    """Caller must admit organization/provider/credential source before constructing.

    An injected session is caller-owned and must already represent the configured
    credential. It exists for native SDK testing; production constructs a private
    registered session. No endpoints or continuation tokens are caller inputs.
    """

    def __init__(self, config: BedrockCatalogueConfig, *, session: Any = None) -> None:
        credential = config.credential
        if (
            not _REGION.fullmatch(config.region)
            or credential.cloud != "aws"
            or not re.fullmatch(r"\d{12}", credential.declared_account)
            or credential.mode not in (CredentialMode.AMBIENT, CredentialMode.AWS_ASSUME_ROLE)
        ):
            raise BedrockCatalogueError("INVALID_CONFIGURATION")
        if credential.mode == CredentialMode.AWS_ASSUME_ROLE:
            parts = credential.role_arn.split(":", 5)
            if (
                len(parts) != 6
                or parts[2] != "iam"
                or parts[4] != credential.declared_account
                or not parts[5].startswith("role/")
            ):
                raise BedrockCatalogueError("INVALID_CONFIGURATION")
        self._partition = _partition(config.region)
        if credential.mode == CredentialMode.AWS_ASSUME_ROLE and (
            parts[0] != "arn" or parts[1] != self._partition or parts[3] or not parts[5][5:]
        ):
            raise BedrockCatalogueError("INVALID_CONFIGURATION")
        self.config = config
        self._session = session
        self._bedrock: Any = None
        self._sts: Any = None
        self._principal: tuple[str, str] | None = None
        self._identity: BedrockCatalogueIdentity | None = None

    def foundation_models(self, *, limit: int = 100) -> BedrockCataloguePage:
        self._limits(limit)
        try:
            response = self._read("list_foundation_models")
            rows = response.get("modelSummaries")
            if not isinstance(rows, list) or len(rows) > 5000:
                raise BedrockCatalogueError("INVALID_RESPONSE")
            # Validate all returned identities, including rows beyond the output cap.
            sources = tuple(self._foundation(row) for row in rows)
            if len({row.arn for row in sources}) != len(sources):
                raise BedrockCatalogueError("INVALID_RESPONSE")
            return BedrockCataloguePage(
                CatalogueState.METADATA, self._identity, sources[:limit], truncated=len(sources) > limit
            )
        except Exception as exc:
            state, reason = self._failure(exc)
            return BedrockCataloguePage(state, reason=reason)

    def inference_profiles(self, *, limit: int = 100, max_pages: int = MAX_PAGES) -> BedrockCataloguePage:
        self._limits(limit, max_pages)
        items: list[BedrockCatalogueSource] = []
        tokens: set[str] = set()
        token = ""
        try:
            for _ in range(max_pages):
                params: dict[str, Any] = {"maxResults": min(100, limit - len(items))}
                if token:
                    params["nextToken"] = token
                response = self._read("list_inference_profiles", **params)
                rows = response.get("inferenceProfileSummaries")
                if not isinstance(rows, list) or len(rows) > params["maxResults"]:
                    raise BedrockCatalogueError("INVALID_RESPONSE")
                items.extend(self._profile(row) for row in rows)
                if len({row.arn for row in items}) != len(items):
                    raise BedrockCatalogueError("INVALID_RESPONSE")
                token = response.get("nextToken", "")
                if not isinstance(token, str) or len(token) > 2048 or (token and token in tokens):
                    raise BedrockCatalogueError("INVALID_RESPONSE")
                if not token:
                    break
                tokens.add(token)
                if len(items) >= limit:
                    break
            return BedrockCataloguePage(CatalogueState.METADATA, self._identity, tuple(items), truncated=bool(token))
        except Exception as exc:
            state, reason = self._failure(exc)
            if state == CatalogueState.REFUSED:
                return BedrockCataloguePage(state, reason=reason)
            return BedrockCataloguePage(
                state, self._identity, tuple(items), truncated=bool(items), partial=bool(items), reason=reason
            )

    def detail(
        self, kind: BedrockSourceKind, identifier: str, *, check_availability: bool = False
    ) -> BedrockCatalogueDetail:
        self._selector(kind, identifier)
        try:
            if kind == BedrockSourceKind.FOUNDATION_MODEL:
                source = self._foundation(
                    self._read("get_foundation_model", modelIdentifier=identifier)["modelDetails"]
                )
            else:
                source = self._profile(self._read("get_inference_profile", inferenceProfileIdentifier=identifier))
            if identifier not in (source.identifier, source.arn):
                raise BedrockCatalogueError("IDENTITY_MISMATCH")
            if check_availability and kind == BedrockSourceKind.FOUNDATION_MODEL:
                source = replace(source, availability=self._availability(source.identifier))
            return BedrockCatalogueDetail(CatalogueState.METADATA, self._identity, source)
        except Exception as exc:
            state, reason = self._failure(exc)
            return BedrockCatalogueDetail(state, reason=reason)

    def _clients(self) -> None:
        if self._sts is not None:
            return
        import boto3
        from botocore.config import Config

        bounds = Config(connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 1})
        if self._session is None:
            # The initial AssumeRole client is private and bounded as well.
            ambient = boto3.Session(region_name=self.config.region)
            ambient._session.set_default_client_config(bounds)
            self._session = aws_session(
                region=self.config.region,
                credential=self.config.credential,
                sts=ambient.client("sts", config=bounds),
            )
        self._session._session.set_default_client_config(bounds)
        self._sts = self._session.client("sts", region_name=self.config.region, config=bounds)
        self._bedrock = self._session.client("bedrock", region_name=self.config.region, config=bounds)

    def _verify(self) -> None:
        self._clients()
        try:
            result = self._sts.get_caller_identity()
        except Exception:
            raise BedrockCatalogueError("IDENTITY_UNVERIFIED") from None
        account, arn, user_id = result.get("Account"), result.get("Arn"), result.get("UserId")
        parts = arn.split(":", 5) if isinstance(arn, str) else []
        if (
            account != self.config.credential.declared_account
            or len(parts) != 6
            or parts[0] != "arn"
            or parts[2] not in ("sts", "iam")
            or parts[4] != account
            or not isinstance(user_id, str)
            or not user_id
            or parts[1] != self._partition
            or parts[1] != self._bedrock.meta.partition
            or self._bedrock.meta.region_name != self.config.region
        ):
            raise BedrockCatalogueError("IDENTITY_MISMATCH")
        credential = self.config.credential
        if credential.mode == CredentialMode.AWS_ASSUME_ROLE:
            role_name = credential.role_arn.rsplit("/", 1)[-1]
            expected = f"arn:{self._partition}:sts::{account}:assumed-role/{role_name}/{credential.session_name}"
            if arn != expected:
                raise BedrockCatalogueError("IDENTITY_MISMATCH")
        principal = (arn, user_id)
        if self._principal is not None and principal != self._principal:
            raise BedrockCatalogueError("IDENTITY_MISMATCH")
        self._principal = principal
        self._identity = BedrockCatalogueIdentity(account, self.config.region, parts[1])

    def _read(self, operation: str, **params: Any) -> dict[str, Any]:
        self._verify()
        try:
            result = getattr(self._bedrock, operation)(**params)
        finally:
            self._verify()
        if not isinstance(result, dict):
            raise BedrockCatalogueError("INVALID_RESPONSE")
        return cast("dict[str, Any]", result)

    def _arn(self, arn: Any, kind: str, *, destination: bool = False) -> str:
        match = _ARN.fullmatch(arn) if isinstance(arn, str) and len(arn) <= 2048 else None
        if not match or self._identity is None:
            raise BedrockCatalogueError("IDENTITY_MISMATCH")
        partition, region, account, resource, identifier = match.groups()
        if (
            partition != self._identity.partition
            or (not destination and region != self.config.region)
            or not _REGION.fullmatch(region)
            or resource != kind
            or account != ("" if kind == "foundation-model" else self._identity.account_id)
            or not _ID.fullmatch(identifier)
        ):
            raise BedrockCatalogueError("IDENTITY_MISMATCH")
        return identifier

    def _foundation(self, row: dict[str, Any]) -> BedrockCatalogueSource:
        identifier = self._arn(row.get("modelArn"), "foundation-model")
        if identifier != row.get("modelId"):
            raise BedrockCatalogueError("IDENTITY_MISMATCH")
        return BedrockCatalogueSource(
            kind=BedrockSourceKind.FOUNDATION_MODEL,
            identifier=identifier,
            arn=row["modelArn"],
            name=row.get("modelName") or identifier,
            provider=row.get("providerName"),
            input_modalities=tuple(row.get("inputModalities", ())),
            output_modalities=tuple(row.get("outputModalities", ())),
            inference_types=tuple(row.get("inferenceTypesSupported", ())),
            streaming=row.get("responseStreamingSupported"),
            lifecycle=(row.get("modelLifecycle") or {}).get("status"),
        )

    def _profile(self, row: dict[str, Any]) -> BedrockCatalogueSource:
        profile_type = row.get("type")
        if profile_type not in ("SYSTEM_DEFINED", "APPLICATION"):
            raise BedrockCatalogueError("INVALID_RESPONSE")
        identifier = self._arn(
            row.get("inferenceProfileArn"),
            "inference-profile" if profile_type == "SYSTEM_DEFINED" else "application-inference-profile",
        )
        if identifier != row.get("inferenceProfileId"):
            raise BedrockCatalogueError("IDENTITY_MISMATCH")
        models = row.get("models")
        if not isinstance(models, list) or not 1 <= len(models) <= 5:
            raise BedrockCatalogueError("INVALID_RESPONSE")
        destinations = tuple(model.get("modelArn") for model in models)
        for arn in destinations:
            self._arn(arn, "foundation-model", destination=True)
        if len(set(destinations)) != len(destinations):
            raise BedrockCatalogueError("INVALID_RESPONSE")
        return BedrockCatalogueSource(
            BedrockSourceKind.INFERENCE_PROFILE,
            identifier,
            row["inferenceProfileArn"],
            row.get("inferenceProfileName") or identifier,
            lifecycle=row.get("status"),
            profile_type=profile_type,
            destination_model_arns=destinations,
        )

    def _availability(self, identifier: str) -> BedrockAvailability:
        try:
            row = self._read("get_foundation_model_availability", modelId=identifier)
            if row.get("modelId") != identifier:
                raise BedrockCatalogueError("IDENTITY_MISMATCH")
            authorization = row.get("authorizationStatus")
            entitlement = row.get("entitlementAvailability")
            region = row.get("regionAvailability")
            agreement = (row.get("agreementAvailability") or {}).get("status")
            if (
                authorization not in ("AUTHORIZED", "NOT_AUTHORIZED")
                or entitlement not in ("AVAILABLE", "NOT_AVAILABLE")
                or region not in ("AVAILABLE", "NOT_AVAILABLE")
                or agreement not in ("AVAILABLE", "NOT_AVAILABLE", "PENDING", "ERROR")
            ):
                return BedrockAvailability(CatalogueState.ERROR)
            return BedrockAvailability(CatalogueState.METADATA, authorization, entitlement, region, agreement)
        except BedrockCatalogueError:
            raise
        except Exception as exc:
            state, _ = self._failure(exc)
            return BedrockAvailability(state)

    def _selector(self, kind: BedrockSourceKind, identifier: str) -> None:
        if kind not in (BedrockSourceKind.FOUNDATION_MODEL, BedrockSourceKind.INFERENCE_PROFILE):
            raise BedrockCatalogueError("INVALID_REQUEST")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 2048:
            raise BedrockCatalogueError("INVALID_REQUEST")
        if identifier.startswith("arn:"):
            match = _ARN.fullmatch(identifier)
            if not match:
                raise BedrockCatalogueError("INVALID_REQUEST")
            partition, region, account, resource, model_id = match.groups()
            expected_resources = (
                ("foundation-model",)
                if kind == BedrockSourceKind.FOUNDATION_MODEL
                else ("inference-profile", "application-inference-profile")
            )
            if (
                region != self.config.region
                or account
                != ("" if kind == BedrockSourceKind.FOUNDATION_MODEL else self.config.credential.declared_account)
                or resource not in expected_resources
                or not _ID.fullmatch(model_id)
                or partition != self._partition
            ):
                raise BedrockCatalogueError("INVALID_REQUEST")
        elif not _ID.fullmatch(identifier):
            raise BedrockCatalogueError("INVALID_REQUEST")

    @staticmethod
    def _limits(limit: int, max_pages: int = 1) -> None:
        if (
            type(limit) is not int
            or not 1 <= limit <= MAX_ITEMS
            or type(max_pages) is not int
            or not 1 <= max_pages <= MAX_PAGES
        ):
            raise BedrockCatalogueError("INVALID_REQUEST")

    @staticmethod
    def _failure(exc: Exception) -> tuple[CatalogueState, str]:
        if isinstance(exc, BedrockCatalogueError):
            return CatalogueState.REFUSED, str(exc)
        response = getattr(exc, "response", {})
        code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedException"):
            return CatalogueState.DENIED, "METADATA_DENIED"
        if code in ("ResourceNotFoundException", "ResourceNotFound"):
            return CatalogueState.NOT_FOUND, "METADATA_NOT_FOUND"
        return CatalogueState.ERROR, "METADATA_UNAVAILABLE"


def _partition(region: str) -> str:
    from botocore.session import get_session

    return str(get_session().get_partition_for_region(region))
