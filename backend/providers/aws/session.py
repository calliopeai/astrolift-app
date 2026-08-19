"""Build a boto3 client for a specific AWS account (#1422).

Every AWS driver in this tree constructs its clients the same way::

    boto3.client("rds", region_name=config.region)

which authenticates as whatever identity the control-plane process carries.
The region is per-cluster; the account is per-process. This module is the one
place that can be told otherwise, and it exists because AWS is the only cloud
here where the account is not addressable in the request: it is a property of
the credential, so reaching a second account means changing the credential and
nothing else will do.

Three things belong together and are why this is a module rather than four
lines inlined at each call site:

* **Assumption.** ``sts:AssumeRole`` on a role ARN read off the cluster row.
  The control plane's own identity authenticates that call, so the tenant's
  trust policy stays the authority over whether this install may reach them.
* **Caching with expiry.** Assumed credentials last an hour and drivers are
  constructed per operation, so an uncached factory would issue an AssumeRole
  per client — several per provision. The cache is keyed by the full identity
  tuple, never by region alone, because a cache that can return account A's
  credentials to a caller that asked for account B is a worse bug than the one
  being fixed.
* **Verification.** :func:`verify_account` fails closed when the identity in
  use is not the account the cluster row declares. Today nothing checks, and
  ``provider_config.account_id`` is interpolated into ARN strings regardless,
  so a mismatch produces resources in one account described by ARNs naming
  another.

``clock``, ``sts`` and ``build`` are injectable on every entry point. That is
not test scaffolding for its own sake: expiry handling and mismatch refusal
are the parts most worth having tests for, and neither can be exercised
against a real STS in CI.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk.cloud_credentials import CloudCredential, CloudCredentialError, CredentialMode

if TYPE_CHECKING:
    from collections.abc import Callable

#: Re-assume this far before the credential actually expires. An hour-long
#: session that is handed out with four minutes left will expire mid-operation
#: on a slow provision; five minutes is the margin botocore itself uses.
REFRESH_SKEW = dt.timedelta(minutes=5)

#: Requested session duration. One hour is the AWS default and the maximum a
#: role accepts unless its ``MaxSessionDuration`` was raised, so asking for
#: more fails on most roles rather than getting more.
SESSION_DURATION_SECONDS = 3600


class AwsAccountMismatch(RuntimeError):
    """The identity in use is not in the account the cluster row declares.

    A distinct type because the operator response is specific: either the row
    is wrong or the credential is, and both are configuration rather than an
    AWS-side failure worth retrying.
    """


@dataclass(frozen=True, slots=True)
class _AssumedCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expires_at: dt.datetime


_CACHE: dict[tuple[str, str, str, str], _AssumedCredentials] = {}
_CACHE_LOCK = threading.Lock()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def clear_credential_cache() -> None:
    """Drop every cached assumed credential. For tests, and for an operator
    path that has just changed a role ARN."""
    with _CACHE_LOCK:
        _CACHE.clear()


def aws_client(
    service: str,
    *,
    region: str,
    credential: CloudCredential | None = None,
    build: Callable[..., Any] | None = None,
    sts: Any | None = None,
    clock: Callable[[], dt.datetime] = _utcnow,
    **kwargs: Any,
) -> Any:
    """Return a boto3 client for ``service`` authenticated as ``credential``.

    ``credential`` of None (or an ambient one) reproduces exactly what the
    drivers do today, so adopting this factory is behaviour-preserving until a
    cluster declares otherwise.
    """
    factory = build or _default_build
    if credential is None or credential.mode is CredentialMode.AMBIENT:
        return factory(service, region_name=region, **kwargs)
    if credential.mode is not CredentialMode.AWS_ASSUME_ROLE:
        # Fail closed on an unhandled mode. The alternative — falling through
        # to the ambient branch — is silently the bug this module exists for.
        raise CloudCredentialError(
            f"aws: no client construction defined for credential mode {credential.mode.value!r}",
        )

    creds = _assume(credential, region=region, sts=sts, clock=clock)
    return factory(
        service,
        region_name=region,
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
        aws_session_token=creds.session_token,
        **kwargs,
    )


def caller_account(
    credential: CloudCredential,
    *,
    region: str,
    sts: Any | None = None,
    build: Callable[..., Any] | None = None,
    clock: Callable[[], dt.datetime] = _utcnow,
) -> str:
    """The 12-digit account id the given credential actually resolves to.

    ``sts`` is the *ambient* client used to perform the AssumeRole. It is also
    the client the identity call is made on when the credential is ambient,
    since then there is nothing to assume and the two are the same client.
    """
    if credential.is_ambient and sts is not None:
        client = sts
    else:
        client = aws_client("sts", region=region, credential=credential, build=build, sts=sts, clock=clock)
    return str(client.get_caller_identity()["Account"])


def verify_account(
    credential: CloudCredential,
    *,
    region: str,
    sts: Any | None = None,
    build: Callable[..., Any] | None = None,
    clock: Callable[[], dt.datetime] = _utcnow,
) -> str:
    """Fail closed unless the effective identity is in the declared account.

    Returns the verified account id. A credential with no declared account is
    returned unverified rather than refused: most cluster rows predate this
    and ``account_id`` is only required by the plugin's config schema at
    register time, not backfilled.
    """
    actual = caller_account(credential, region=region, sts=sts, build=build, clock=clock)
    declared = credential.declared_account
    if declared and actual != declared:
        raise AwsAccountMismatch(
            f"cluster declares AWS account {declared} but the credential in use resolves to "
            f"{actual}. Resources would be created in {actual} while every ARN the drivers "
            f"build would name {declared}.",
        )
    return actual


def _assume(
    credential: CloudCredential,
    *,
    region: str,
    sts: Any | None,
    clock: Callable[[], dt.datetime],
) -> _AssumedCredentials:
    key = (credential.role_arn, credential.external_id, credential.session_name, region)
    now = clock()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached.expires_at - now > REFRESH_SKEW:
            return cached

    fresh = _assume_uncached(credential, region=region, sts=sts, clock=clock)
    with _CACHE_LOCK:
        _CACHE[key] = fresh
    return fresh


def _assume_uncached(
    credential: CloudCredential,
    *,
    region: str,
    sts: Any | None,
    clock: Callable[[], dt.datetime],
) -> _AssumedCredentials:
    client = sts if sts is not None else _default_build("sts", region_name=region)
    params: dict[str, Any] = {
        "RoleArn": credential.role_arn,
        "RoleSessionName": credential.session_name,
        "DurationSeconds": SESSION_DURATION_SECONDS,
    }
    if credential.external_id:
        params["ExternalId"] = credential.external_id
    payload = client.assume_role(**params)["Credentials"]
    return _AssumedCredentials(
        access_key_id=str(payload["AccessKeyId"]),
        secret_access_key=str(payload["SecretAccessKey"]),
        session_token=str(payload["SessionToken"]),
        # STS always returns Expiration; defaulting keeps a stubbed response
        # from silently caching forever if it does not.
        expires_at=_as_aware(payload.get("Expiration")) or clock(),
    )


def _as_aware(value: Any) -> dt.datetime | None:
    if not isinstance(value, dt.datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def _default_build(service: str, **kwargs: Any) -> Any:
    import boto3

    return boto3.client(service, **kwargs)
