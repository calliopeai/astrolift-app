"""Building an AWS client for a specific account (#1422).

Exercised against stubs rather than moto because the interesting behaviour is
not what STS returns, it is what the factory does with expiry, with a cache
key, and with a declared account that disagrees with reality. None of those
are observable against a live STS in CI.
"""

from __future__ import annotations

import datetime as dt

import pytest

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from aws.session import (
    REFRESH_SKEW,
    AwsAccountMismatch,
    aws_client,
    clear_credential_cache,
    verify_account,
)

ROLE = "arn:aws:iam::210987654321:role/astrolift-control-plane"
T0 = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_credential_cache()
    yield
    clear_credential_cache()


class FakeSts:
    """Records AssumeRole calls and hands back a scripted session."""

    def __init__(self, *, expires_at: dt.datetime = T0 + dt.timedelta(hours=1), account: str = "210987654321"):
        self.calls: list[dict] = []
        self._expires_at = expires_at
        self._account = account
        self._serial = 0

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        self._serial += 1
        return {
            "Credentials": {
                "AccessKeyId": f"ASIA{self._serial}",
                "SecretAccessKey": f"secret{self._serial}",
                "SessionToken": f"token{self._serial}",
                "Expiration": self._expires_at,
            }
        }

    def get_caller_identity(self):
        return {"Account": self._account}


class RecordingBuild:
    """Stands in for ``boto3.client`` and records how each client was built."""

    def __init__(self, *, account: str = "210987654321"):
        self.calls: list[tuple[str, dict]] = []
        self._account = account

    def __call__(self, service, **kwargs):
        self.calls.append((service, kwargs))
        return FakeSts(account=self._account)


def _assume_role_credential(**overrides) -> CloudCredential:
    fields = {
        "cloud": "aws",
        "mode": CredentialMode.AWS_ASSUME_ROLE,
        "role_arn": ROLE,
        "declared_account": "210987654321",
        "session_name": "astrolift-prod",
    }
    fields.update(overrides)
    return CloudCredential(**fields)


def test_ambient_credential_builds_exactly_what_the_drivers_build_today():
    """Adopting the factory must be a no-op until a cluster declares
    otherwise, or migrating 126 call sites is a behaviour change."""
    build = RecordingBuild()

    aws_client("rds", region="us-west-2", credential=None, build=build)

    assert build.calls == [("rds", {"region_name": "us-west-2"})]


def test_assumed_credential_is_threaded_into_the_client():
    build = RecordingBuild()
    sts = FakeSts()

    aws_client(
        "rds",
        region="us-west-2",
        credential=_assume_role_credential(external_id="nonce-7"),
        build=build,
        sts=sts,
        clock=lambda: T0,
    )

    assert sts.calls == [
        {
            "RoleArn": ROLE,
            "RoleSessionName": "astrolift-prod",
            "DurationSeconds": 3600,
            "ExternalId": "nonce-7",
        }
    ]
    service, kwargs = build.calls[0]
    assert service == "rds"
    assert kwargs["aws_access_key_id"] == "ASIA1"
    assert kwargs["aws_session_token"] == "token1"


def test_external_id_is_omitted_when_unset():
    """An empty ExternalId is not the same as no ExternalId — STS rejects the
    call against a role whose trust policy does not require one."""
    sts = FakeSts()

    aws_client("rds", region="us-west-2", credential=_assume_role_credential(), build=RecordingBuild(), sts=sts)

    assert "ExternalId" not in sts.calls[0]


def test_a_live_session_is_reused_across_clients():
    """Drivers are constructed per operation and build several clients each,
    so an uncached factory would issue an AssumeRole per client."""
    sts = FakeSts()
    credential = _assume_role_credential()

    for service in ("rds", "secretsmanager", "ec2"):
        aws_client(
            service, region="us-west-2", credential=credential, build=RecordingBuild(), sts=sts, clock=lambda: T0
        )

    assert len(sts.calls) == 1


def test_a_session_inside_the_refresh_window_is_re_assumed():
    sts = FakeSts(expires_at=T0 + dt.timedelta(hours=1))
    credential = _assume_role_credential()
    build = RecordingBuild()

    aws_client("rds", region="us-west-2", credential=credential, build=build, sts=sts, clock=lambda: T0)
    later = T0 + dt.timedelta(hours=1) - REFRESH_SKEW
    aws_client("rds", region="us-west-2", credential=credential, build=build, sts=sts, clock=lambda: later)

    assert len(sts.calls) == 2
    assert build.calls[1][1]["aws_access_key_id"] == "ASIA2"


def test_the_cache_never_hands_one_account_the_other_account_session():
    """The failure this guards is the one being fixed, only worse: a cache
    keyed loosely would silently serve account A's session to a caller that
    asked for account B."""
    sts = FakeSts()
    account_a = _assume_role_credential(role_arn="arn:aws:iam::111111111111:role/a")
    account_b = _assume_role_credential(role_arn="arn:aws:iam::222222222222:role/b")

    aws_client("rds", region="us-west-2", credential=account_a, build=RecordingBuild(), sts=sts, clock=lambda: T0)
    aws_client("rds", region="us-west-2", credential=account_b, build=RecordingBuild(), sts=sts, clock=lambda: T0)

    assert [call["RoleArn"] for call in sts.calls] == [account_a.role_arn, account_b.role_arn]


def test_the_cache_is_keyed_by_region_too():
    """Session credentials are global, but a regional STS endpoint issues its
    own; keying without region would pin a session to whichever region asked
    first and hide a regional STS outage as a stale-credential failure."""
    sts = FakeSts()
    credential = _assume_role_credential()

    aws_client("rds", region="us-west-2", credential=credential, build=RecordingBuild(), sts=sts, clock=lambda: T0)
    aws_client("rds", region="eu-west-1", credential=credential, build=RecordingBuild(), sts=sts, clock=lambda: T0)

    assert len(sts.calls) == 2


def test_verify_account_accepts_a_matching_identity():
    sts = FakeSts(account="210987654321")

    assert verify_account(_assume_role_credential(), region="us-west-2", build=RecordingBuild(), sts=sts) == (
        "210987654321"
    )


def test_verify_account_fails_closed_on_a_mismatch():
    """Today nothing checks, and account_id is interpolated into ARNs
    regardless: a mismatch produces resources in one account described by ARNs
    naming another."""
    build = RecordingBuild(account="999999999999")
    credential = _assume_role_credential(declared_account="210987654321")

    with pytest.raises(AwsAccountMismatch) as exc:
        verify_account(credential, region="us-west-2", build=build, sts=FakeSts())

    assert "210987654321" in str(exc.value)
    assert "999999999999" in str(exc.value)


def test_verify_account_passes_an_undeclared_account_through():
    """Cluster rows predate this and account_id is only required at register
    time, so an empty declaration must not brick an existing install."""
    sts = FakeSts(account="999999999999")
    credential = CloudCredential(cloud="aws")

    assert verify_account(credential, region="us-west-2", sts=sts) == "999999999999"
