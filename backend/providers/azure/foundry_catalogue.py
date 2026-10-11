"""Bounded, read-only discovery of existing Microsoft Foundry deployments.

This is an internal provider seam, not an authorization or adoption API.
Callers must admit the current actor, organization and enabled provider before
constructing it. ARM read access does not prove model inference permission,
license acceptance or independently revocable application credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote, urlsplit
from uuid import UUID

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential
    from azure.core.paging import PageIterator
    from azure.core.pipeline.transport import HttpTransport

_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z", re.ASCII)
_GROUP = re.compile(r"[^/\\%?#\x00-\x1f]{1,90}\Z")
_REGION = re.compile(r"[a-z0-9]{2,64}\Z", re.ASCII)


class CatalogueState(StrEnum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    INVALID_IDENTITY = "invalid_identity"
    ERROR = "error"


class FoundryCatalogueError(ValueError):
    """Invalid placement or an untrusted native response."""


@dataclass(frozen=True, slots=True)
class FoundryCatalogueConfig:
    subscription_id: str
    resource_group: str
    account_name: str
    region: str

    def __post_init__(self) -> None:
        try:
            parsed = UUID(self.subscription_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise FoundryCatalogueError("A canonical Azure subscription ID is required.") from exc
        if str(parsed) != self.subscription_id:
            raise FoundryCatalogueError("A canonical Azure subscription ID is required.")
        if (
            not isinstance(self.resource_group, str)
            or not _GROUP.fullmatch(self.resource_group)
            or self.resource_group.endswith(".")
            or not isinstance(self.account_name, str)
            or not _NAME.fullmatch(self.account_name)
            or not isinstance(self.region, str)
            or not _REGION.fullmatch(self.region)
        ):
            raise FoundryCatalogueError("Exact Azure account placement is required.")

    @property
    def account_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{self.account_name}"
        )


@dataclass(frozen=True, slots=True)
class FoundryAccountIdentity:
    resource_id: str
    subscription_id: str
    region: str
    kind: str
    local_auth_disabled: bool | None


@dataclass(frozen=True, slots=True)
class FoundryDeploymentSource:
    resource_id: str
    deployment_name: str
    model_format: str
    model_name: str
    model_version: str | None
    sku_name: str | None
    declared_capacity: int | None
    provisioning_state: str | None
    inference_access: str = "unknown"


@dataclass(frozen=True, slots=True)
class FoundryCatalogueResult:
    state: CatalogueState
    identity: FoundryAccountIdentity | None = None
    sources: tuple[FoundryDeploymentSource, ...] = ()
    pages_read: int = 0


def _text(value: Any, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(ord(c) < 32 for c in value):
        raise FoundryCatalogueError("Native model metadata is invalid.")
    return value


def _required_text(value: Any) -> str:
    result = _text(value, required=True)
    if result is None:
        raise FoundryCatalogueError("Native model metadata is missing.")
    return result


def _state(exc: Exception) -> CatalogueState:
    # Never expose SDK diagnostics, response bodies, continuation URLs or keys.
    if isinstance(exc, FoundryCatalogueError):
        return CatalogueState.INVALID_IDENTITY
    code = getattr(exc, "status_code", None)
    if code in (401, 403):
        return CatalogueState.DENIED
    if code == 404:
        return CatalogueState.NOT_FOUND
    return CatalogueState.ERROR


def _guarded_transport(delegate: HttpTransport[Any, Any], allowed_paths: set[str]) -> HttpTransport[Any, Any]:
    from azure.core.pipeline.transport import HttpTransport

    class ReadOnlyAccountTransport(HttpTransport[Any, Any]):
        def open(self) -> None:
            delegate.open()

        def close(self) -> None:
            delegate.close()

        def __enter__(self) -> ReadOnlyAccountTransport:
            self.open()
            return self

        def __exit__(self, *args: Any) -> None:
            self.close()

        def send(self, request: Any, **kwargs: Any) -> Any:
            url = urlsplit(request.url)
            path = unquote(url.path)
            if (
                request.method != "GET"
                or url.scheme != "https"
                or url.netloc != "management.azure.com"
                or url.fragment
                or path.casefold() not in allowed_paths
                or any(part in (".", "..") for part in path.split("/"))
            ):
                raise FoundryCatalogueError("Native discovery attempted an unreviewed destination or write.")
            kwargs["connection_timeout"] = 5
            kwargs["read_timeout"] = 10
            return delegate.send(request, **kwargs)

    return ReadOnlyAccountTransport()


class FoundryCatalogue:
    """Use a current admitted Azure credential to read one exact public-cloud account.

    The client addresses the declared subscription directly. It does not claim
    to verify the principal's Entra tenant or construct a new account login.
    Sovereign clouds need their own admitted endpoint/audience contract.
    """

    def __init__(
        self,
        config: FoundryCatalogueConfig,
        credential: TokenCredential,
        *,
        transport: HttpTransport[Any, Any] | None = None,
    ) -> None:
        from azure.core.pipeline.transport import RequestsTransport
        from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient

        self._config = config
        self._allowed_paths = {config.account_id.casefold(), (config.account_id + "/deployments").casefold()}
        self._client = CognitiveServicesManagementClient(
            credential,
            config.subscription_id,
            base_url="https://management.azure.com",
            transport=_guarded_transport(transport or RequestsTransport(), self._allowed_paths),
            credential_scopes=["https://management.azure.com/.default"],
            retry_total=0,
            permit_redirects=False,
            redirect_max=0,
            connection_timeout=5,
            read_timeout=10,
            logging_enable=False,
        )

    def close(self) -> None:
        self._client.close()

    def _account(self) -> FoundryAccountIdentity:
        account = self._client.accounts.get(self._config.resource_group, self._config.account_name)
        if (
            not isinstance(account.id, str)
            or account.id.casefold() != self._config.account_id.casefold()
            or account.name != self._config.account_name
            or account.kind != "AIServices"
            or account.location != self._config.region
            or account.type != "Microsoft.CognitiveServices/accounts"
        ):
            raise FoundryCatalogueError("Native account placement differs from the reviewed target.")
        disabled = getattr(account.properties, "disable_local_auth", None)
        if disabled is not None and type(disabled) is not bool:
            raise FoundryCatalogueError("Native account authentication metadata is invalid.")
        return FoundryAccountIdentity(
            resource_id=self._config.account_id,
            subscription_id=self._config.subscription_id,
            region=self._config.region,
            kind=account.kind,
            local_auth_disabled=disabled,
        )

    def _source(self, deployment: Any) -> FoundryDeploymentSource:
        name = deployment.name
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise FoundryCatalogueError("Native deployment name is invalid.")
        expected = self._config.account_id + "/deployments/" + name
        if (
            not isinstance(deployment.id, str)
            or deployment.id.casefold() != expected.casefold()
            or deployment.type != "Microsoft.CognitiveServices/accounts/deployments"
        ):
            raise FoundryCatalogueError("Native deployment belongs to another account.")
        properties = deployment.properties
        model = getattr(properties, "model", None)
        sku = deployment.sku
        capacity = getattr(sku, "capacity", None)
        if capacity is not None and (type(capacity) is not int or capacity < 0):
            raise FoundryCatalogueError("Native deployment capacity is invalid.")
        return FoundryDeploymentSource(
            resource_id=expected,
            deployment_name=name,
            model_format=_required_text(getattr(model, "format", None)),
            model_name=_required_text(getattr(model, "name", None)),
            model_version=_text(getattr(model, "version", None)),
            sku_name=_text(getattr(sku, "name", None)),
            declared_capacity=capacity,
            provisioning_state=_text(getattr(properties, "provisioning_state", None)),
        )

    def deployments(self, *, limit: int = 100, max_pages: int = 5) -> FoundryCatalogueResult:
        if type(limit) is not int or not 1 <= limit <= 500 or type(max_pages) is not int or not 1 <= max_pages <= 5:
            raise FoundryCatalogueError("Discovery requires a limit of 1-500 and at most five pages.")
        pages_read = 0
        try:
            identity = self._account()
            # ItemPaged's annotation erases PageIterator's native continuation
            # state. Keep that state private rather than expose tokens to callers.
            pages = cast(
                "PageIterator[Any]",
                self._client.deployments.list(self._config.resource_group, self._config.account_name).by_page(),
            )
            sources: list[FoundryDeploymentSource] = []
            ids: set[str] = set()
            truncated = False
            for _ in range(max_pages):
                try:
                    page = next(pages)
                except StopIteration:
                    break
                pages_read += 1
                for deployment in page:
                    if len(sources) == limit:
                        truncated = True
                        break
                    source = self._source(deployment)
                    if source.resource_id.casefold() in ids:
                        raise FoundryCatalogueError("Native discovery repeated a deployment identity.")
                    ids.add(source.resource_id.casefold())
                    sources.append(source)
                if truncated or len(sources) == limit or not pages.continuation_token:
                    truncated = truncated or bool(pages.continuation_token)
                    break
            else:
                truncated = bool(pages.continuation_token)
            if self._account() != identity:
                raise FoundryCatalogueError("Native account identity changed during discovery.")
            return FoundryCatalogueResult(
                CatalogueState.TRUNCATED if truncated else CatalogueState.COMPLETE,
                identity,
                tuple(sources),
                pages_read,
            )
        except Exception as exc:
            return FoundryCatalogueResult(_state(exc), pages_read=pages_read)

    def deployment(self, name: str) -> FoundryCatalogueResult:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise FoundryCatalogueError("Select an exact deployment name, not a URL or resource path.")
        path = (self._config.account_id + "/deployments/" + name).casefold()
        self._allowed_paths.add(path)
        try:
            identity = self._account()
            source = self._source(
                self._client.deployments.get(self._config.resource_group, self._config.account_name, name)
            )
            if source.deployment_name != name or self._account() != identity:
                raise FoundryCatalogueError("Native deployment identity changed during discovery.")
            return FoundryCatalogueResult(CatalogueState.COMPLETE, identity, (source,))
        except Exception as exc:
            return FoundryCatalogueResult(_state(exc))
        finally:
            self._allowed_paths.discard(path)
