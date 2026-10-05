"""Private bounded AWS credential/client chain for one admitted mail operation.

This adapter does not certify the AWS account or authorize a send. The caller
must compare actual STS identity and retain fresh source/operation admission in
checkpoint. SDK exceptions remain private to the driver; never expose/log them.
"""

from __future__ import annotations

import contextvars
import importlib
import logging
import re
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from _sdk.cloud_credentials import CredentialMode
from aws.session import aws_client

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import TracebackType

    from _sdk.cloud_credentials import CloudCredential

_PRIVATE = contextvars.ContextVar("email_native_clients_private", default=False)
_LOG_LOCK = threading.Lock()
_LOG_INSTALLED = False


class EmailNativeClientError(ValueError):
    """Fixed transport/lifetime reason, without credential or provider payloads."""


def _install_private_logging() -> None:
    global _LOG_INSTALLED
    with _LOG_LOCK:
        if _LOG_INSTALLED:
            return
        previous = logging.getLogRecordFactory()

        def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            if _PRIVATE.get() and record.name.startswith(("botocore", "boto3", "urllib3")):
                record.msg = "Private AWS email client operation."
                record.args = ()
                record.exc_info = None
                record.exc_text = None
                record.stack_info = None
            return record

        logging.setLogRecordFactory(record_factory)
        _LOG_INSTALLED = True


@contextmanager
def _private_io() -> Iterator[None]:
    _install_private_logging()
    marker = _PRIVATE.set(True)
    try:
        try:
            context = importlib.import_module("opentelemetry.context")
        except ImportError:
            yield
        else:
            # Use the installed API's instrumentation keys even when the
            # optional instrumentation utility package is absent. Existing
            # instrumentors consult these keys; restore the caller context.
            current = context.get_current()
            for key in (
                context._SUPPRESS_INSTRUMENTATION_KEY,
                context._SUPPRESS_HTTP_INSTRUMENTATION_KEY,
                "suppress_instrumentation",
                "suppress_http_instrumentation",
            ):
                current = context.set_value(key, True, current)
            token = context.attach(current)
            try:
                yield
            finally:
                context.detach(token)
    finally:
        _PRIVATE.reset(marker)


class _GuardedCredentials:
    def __init__(self, original: Any, owner: EmailNativeClients) -> None:
        self.original, self.owner = original, owner

    def get_frozen_credentials(self) -> Any:
        with _private_io():
            self.owner._gate()
            result = self.original.get_frozen_credentials()
            self.owner._gate()
            return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)


class EmailNativeClients:
    """Owns clients/refresh transports; no shared SDK session is modified.

    IRSA and container/instance identities use botocore's actual credential
    chain. Explicit registered AssumeRole uses the same guarded ambient chain.
    External credential-process and interactive login providers are refused
    when selected: the SDK runs unbounded processes for those providers.
    Returned real clients must stay inside this owner's lifetime.
    """

    def __init__(self, credential: CloudCredential, region: str, checkpoint: Callable[[], None]) -> None:
        import boto3
        from botocore.config import Config
        from botocore.session import Session

        if (
            credential.cloud != "aws"
            or credential.mode not in (CredentialMode.AMBIENT, CredentialMode.AWS_ASSUME_ROLE)
            or not isinstance(region, str)
            or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region)
        ):
            raise EmailNativeClientError("EMAIL_CLIENT_SOURCE_UNSUPPORTED")
        self.credential, self.region, self.checkpoint = credential, region, checkpoint
        self._closed = False
        self._all_clients: list[Any] = []
        self._metadata_transports: list[Any] = []
        self._clients: dict[str, Any] = {}
        self._ambient_sts: Any = None
        self._creation_lock = threading.RLock()
        self.config = Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
            ignore_configured_endpoint_urls=True,
        )
        try:
            with _private_io():
                self._gate()
                native = Session()
                native.set_default_client_config(self.config)
                native.set_config_variable("region", region)
                native.set_config_variable("metadata_service_timeout", 3)
                native.set_config_variable("metadata_service_num_attempts", 1)
                # Session events are copied into internal profile/WebIdentity/
                # SSO clients as well as the clients returned to the caller.
                native.register("before-send.*.*", lambda **_kwargs: self._gate())
                native.create_client = self._guard_factory(native.create_client)
                self._gate()
                resolver = native.get_component("credential_provider")
                for provider in resolver.providers:
                    self._guard_provider(provider)
                self.session = boto3.Session(botocore_session=native, region_name=region)
                self._gate()
        except BaseException:
            self.close()
            raise

    def _gate(self) -> None:
        if self._closed:
            raise EmailNativeClientError("EMAIL_CLIENTS_CLOSED")
        self.checkpoint()

    def _guard_provider(self, provider: Any) -> None:
        if getattr(provider, "_email_private_guarded", False):
            return
        provider._email_private_guarded = True
        builder = getattr(provider, "_profile_provider_builder", None)
        if builder is not None:
            original_providers = builder.providers

            def providers(*args: Any, **kwargs: Any) -> Any:
                self._gate()
                rows = original_providers(*args, **kwargs)
                for row in rows:
                    self._guard_provider(row)
                return rows

            builder.providers = providers
        original_load = provider.load

        def load() -> Any:
            with _private_io():
                self._gate()
                if provider.METHOD in {"custom-process", "login"}:
                    # Preserve provider ordering: no fallthrough to another
                    # identity when the configured unsupported source wins.
                    if provider.METHOD == "custom-process" and provider._credential_process is None:
                        return None
                    if provider.METHOD == "login" and not provider._load_config().get("profiles", {}).get(
                        provider._profile_name, {}
                    ).get("login_session"):
                        return None
                    raise EmailNativeClientError("EMAIL_CREDENTIAL_PROVIDER_UNSUPPORTED")
                result = original_load()
                self._gate()
                return result

        provider.load = load
        fetcher = getattr(provider, "_fetcher", None) or getattr(provider, "_role_fetcher", None)
        if fetcher is None:
            return
        transport = getattr(fetcher, "_session", None)
        if transport is None:
            return
        if transport not in self._metadata_transports:
            self._metadata_transports.append(transport)
        if provider.METHOD == "container-role":
            fetcher.RETRY_ATTEMPTS = 1
        original_send = transport.send

        def send(request: Any) -> Any:
            with _private_io():
                self._gate()
                response = original_send(request)
                self._gate()
                return response

        transport.send = send

    def _guard_factory(self, factory: Callable[..., Any]) -> Callable[..., Any]:
        def create(*args: Any, **kwargs: Any) -> Any:
            with self._creation_lock, _private_io():
                self._gate()
                client = factory(*args, **kwargs)
                self._all_clients.append(client)
                credentials = client._request_signer._credentials
                if credentials is not None and not isinstance(credentials, _GuardedCredentials):
                    client._request_signer._credentials = _GuardedCredentials(credentials, self)
                original_api_call = client._make_api_call

                def api_call(operation: str, params: Any) -> Any:
                    with _private_io():
                        self._gate()
                        # No post-call authority gate here: the driver may
                        # need to retain an acknowledged original send reply.
                        # Every new transport/signature still checks current.
                        return original_api_call(operation, params)

                client._make_api_call = api_call
                self._gate()
                return client

        return create

    def _build(self, name: str, **kwargs: Any) -> Any:
        self._gate()
        kwargs.setdefault("config", self.config)
        return self.session.client(name, **kwargs)

    def client(self, service: str) -> Any:
        if service not in {"sts", "sesv2"}:
            raise EmailNativeClientError("EMAIL_CLIENT_SERVICE_UNSUPPORTED")
        with self._creation_lock, _private_io():
            self._gate()
            if service not in self._clients:
                if self.credential.mode is CredentialMode.AWS_ASSUME_ROLE and self._ambient_sts is None:
                    self._ambient_sts = self._build("sts", region_name=self.region)
                self._clients[service] = aws_client(
                    service,
                    region=self.region,
                    credential=self.credential,
                    build=self._build,
                    sts=self._ambient_sts,
                    config=self.config,
                )
            self._gate()
            return self._clients[service]

    def close(self) -> None:
        with self._creation_lock, _private_io():
            if self._closed:
                return
            self._closed = True
            for client in reversed(self._all_clients):
                client.close()
            for transport in reversed(self._metadata_transports):
                transport.close()
            self._metadata_transports.clear()
            self._all_clients.clear()
            self._clients.clear()

    def __enter__(self) -> EmailNativeClients:
        self._gate()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()
