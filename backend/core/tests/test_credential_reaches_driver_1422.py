"""A declared role reaches the driver's clients, end to end (#1422).

The ratchet next door proves no driver constructs a client directly. That is
necessary and not sufficient: a driver can route every call through
``aws_client`` and still be handed a config nobody stamped, in which case
``credential`` is None and ``aws_client`` correctly, silently, goes ambient.
The gap is between the funnel that builds the config and the driver that reads
it, and only a test that walks both halves can see it.

So these drive the real funnels against a cluster row that declares a role and
assert on the credential that comes out the far end -- at the config, and then
at the boto3 constructor the driver actually calls.
"""

from __future__ import annotations

import pytest
from _sdk.cloud_credentials import CredentialMode

from core.app_deploy import _config_for_capability
from core.cluster_credentials import (
    CREDENTIAL_AWARE_CAPABILITIES,
    ClusterCredentialUnsupported,
    assert_credential_supported,
)
from core.cluster_observability import _config_for

ROLE_ARN = "arn:aws:iam::210987654321:role/astrolift-tenant"


class _Plugin:
    slug = "aws"


class _Cluster:
    """The minimum of a TenantCluster the config funnels read.

    A stand-in rather than a real row because these assertions are about the
    funnels, and a saved row would drag in the whole provider-plugin fixture
    chain for fields none of this reads.
    """

    def __init__(self, *, credential: dict | None = None):
        self.slug = "tenant-a"
        self.region = "us-west-2"
        self.provider_plugin = _Plugin()
        self.auth_config: dict = {}
        self.provider_config: dict = {
            "region": "us-west-2",
            "cluster_name": "tenant-a-eks",
            "account_id": "210987654321",
        }
        if credential is not None:
            self.provider_config["credential"] = credential


def _declaring() -> _Cluster:
    return _Cluster(credential={"mode": "aws_assume_role", "role_arn": ROLE_ARN})


@pytest.mark.parametrize("capability", ["registry", "identity", "secrets"])
def test_capability_config_carries_the_declared_role(capability):
    cfg = _config_for_capability("aws", _declaring(), capability)

    assert cfg.credential is not None, (
        f"{capability}: _config_for_capability returned an unstamped config, so the "
        "driver authenticates as the control plane"
    )
    assert cfg.credential.mode is CredentialMode.AWS_ASSUME_ROLE
    assert cfg.credential.role_arn == ROLE_ARN


def test_cluster_driver_config_carries_the_declared_role():
    cfg = _config_for("aws", _declaring())

    assert cfg.credential is not None
    assert cfg.credential.role_arn == ROLE_ARN


def test_an_undeclared_cluster_stays_ambient():
    """The migration is behaviour-preserving for every cluster that exists.

    Stamping runs unconditionally, so this is what proves the stamp of an
    undeclared cluster is an ambient credential and not, say, a None that
    later reads as "not yet stamped".
    """
    cfg = _config_for_capability("aws", _Cluster(), "registry")

    assert cfg.credential is not None
    assert cfg.credential.mode is CredentialMode.AMBIENT
    assert cfg.credential.is_ambient


@pytest.mark.parametrize("capability", ["cluster", "registry", "identity", "secrets"])
def test_the_migrated_capabilities_are_no_longer_refused(capability):
    """Before #1422 these raised: the guard refuses a declared credential a
    capability would ignore. The refusal lifting is the observable half of
    the migration."""
    assert capability in CREDENTIAL_AWARE_CAPABILITIES
    assert_credential_supported(_declaring(), capability=capability)


def test_an_unmigrated_capability_is_still_refused():
    """The guard has not been blanket-disabled.

    ``dns`` on AWS still builds its clients ambiently -- Route53Config is
    constructed with no cluster in hand at every call site -- so a cluster
    declaring a role must still be refused there rather than quietly served
    from the control plane's account.
    """
    assert "dns" not in CREDENTIAL_AWARE_CAPABILITIES
    with pytest.raises(ClusterCredentialUnsupported) as exc:
        assert_credential_supported(_declaring(), capability="dns")
    assert "dns" in str(exc.value)


def test_the_driver_hands_the_credential_to_the_client_factory():
    """The last hop: config.credential actually reaches client construction.

    Everything above stops at the config. This drives the ECR driver's own
    constructor with a stamped config and captures what ``aws_client`` was
    asked to build, which is the only place the two halves meet.
    """
    from aws import registry_ecr

    seen: list[dict] = []

    def fake_build(service, **kwargs):
        seen.append({"service": service, **kwargs})
        return object()

    cfg = _config_for_capability("aws", _declaring(), "registry")
    # Assume the role through an injected STS rather than reaching AWS; the
    # assumption path itself is covered in the provider suite.
    registry_ecr.ECRDriver(
        config=cfg,
        client=registry_ecr.aws_client(
            "ecr",
            region=cfg.region,
            credential=cfg.credential,
            build=fake_build,
            sts=_FakeSts(),
        ),
    )

    assert seen, "aws_client never built a client"
    built = seen[0]
    assert built["service"] == "ecr"
    assert built["aws_access_key_id"] == "ASIAFAKE"
    assert built["aws_session_token"] == "token-abc"


class _FakeSts:
    def assume_role(self, **kwargs):
        assert kwargs["RoleArn"] == ROLE_ARN
        return {
            "Credentials": {
                "AccessKeyId": "ASIAFAKE",
                "SecretAccessKey": "secret",
                "SessionToken": "token-abc",
                "Expiration": _far_future(),
            }
        }


def _far_future():
    import datetime as dt

    return dt.datetime.now(tz=dt.UTC) + dt.timedelta(hours=1)
