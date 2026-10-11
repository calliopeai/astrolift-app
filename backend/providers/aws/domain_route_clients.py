"""Finite, no-retry native clients for reviewed domain routing operations."""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from botocore.config import Config

from _sdk.cloud_credentials import CloudCredential
from aws.domain_route import DomainRouteError
from aws.session import aws_client


class _RouteSigningCredentials:
    def __init__(self, original: Any, checkpoint: Callable[[], None]) -> None:
        self.original = original
        self.checkpoint = checkpoint

    def get_frozen_credentials(self) -> Any:
        self.checkpoint()
        frozen = self.original.get_frozen_credentials()
        self.checkpoint()
        return frozen

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)


@contextmanager
def _clients(
    *,
    region: str,
    credential: CloudCredential | None,
    checkpoint: Callable[[], None],
    expected_account_id: str = "",
    services: tuple[str, ...] = ("sts", "route53", "acm", "elbv2"),
) -> Iterator[tuple[dict[str, Any], str]]:
    # Explicit public endpoints reject ambient endpoint/proxy overrides.
    if not isinstance(region, str) or not re.fullmatch(r"(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-[1-9]", region):
        raise DomainRouteError("NATIVE_REGION_UNSUPPORTED")
    checkpoint()
    config = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}, proxies={})
    endpoints = {
        "sts": f"https://sts.{region}.amazonaws.com",
        "route53": "https://route53.amazonaws.com",
        "acm": f"https://acm.{region}.amazonaws.com",
        "elbv2": f"https://elasticloadbalancing.{region}.amazonaws.com",
        "eks": f"https://eks.{region}.amazonaws.com",
        "cognito-idp": f"https://cognito-idp.{region}.amazonaws.com",
        "apigatewayv2": f"https://apigateway.{region}.amazonaws.com",
        "lambda": f"https://lambda.{region}.amazonaws.com",
    }
    if (
        not isinstance(services, tuple)
        or "sts" not in services
        or len(set(services)) != len(services)
        or set(services) - set(endpoints)
    ):
        raise DomainRouteError("INVALID_NATIVE_CLIENT_SET")
    built = []
    try:
        ambient_sts = aws_client("sts", region=region, config=config, endpoint_url=endpoints["sts"])
        built.append(ambient_sts)
        checkpoint()
        result: dict[str, Any] = {}
        # A single signing credential object avoids separate SDK refresh
        # identities within this explicitly admitted native read/write stage.
        shared = None
        for name in ("sts", *(s for s in services if s != "sts")):
            checkpoint()
            result[name] = aws_client(
                name,
                region=region,
                credential=credential,
                sts=ambient_sts,
                config=config,
                endpoint_url=endpoints[name],
            )
            built.append(result[name])
            if shared is None:
                original = result[name]._request_signer._credentials
                if original is None:
                    raise DomainRouteError("NATIVE_SIGNING_IDENTITY_UNAVAILABLE")
                shared = _RouteSigningCredentials(original, checkpoint)
            result[name]._request_signer._credentials = shared
            checkpoint()
        try:
            identity = result["sts"].get_caller_identity()
        except Exception:
            raise DomainRouteError("NATIVE_ACCOUNT_UNAVAILABLE") from None
        checkpoint()
        account = identity.get("Account") if isinstance(identity, dict) else None
        if (
            not isinstance(account, str)
            or len(account) != 12
            or not account.isascii()
            or not account.isdigit()
            or (expected_account_id and account != expected_account_id)
            or (credential and credential.declared_account and account != credential.declared_account)
        ):
            raise DomainRouteError("NATIVE_ACCOUNT_CHANGED")
        result["_ambient_sts"] = ambient_sts
        yield result, account
    finally:
        for client in reversed(built):
            client.close()


@contextmanager
def clients(
    *,
    region: str,
    credential: CloudCredential | None,
    checkpoint: Callable[[], None],
    expected_account_id: str = "",
    services: tuple[str, ...] = ("sts", "route53", "acm", "elbv2"),
) -> Iterator[tuple[dict[str, Any], str]]:
    from aws.email_delivery_clients import _private_io

    with (
        _private_io(),
        _clients(
            region=region,
            credential=credential,
            checkpoint=checkpoint,
            expected_account_id=expected_account_id,
            services=services,
        ) as observed,
    ):
        yield observed
