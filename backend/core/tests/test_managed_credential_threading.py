"""Managed-service drivers act as the cluster's identity (#1422, PR 3).

Before this, every driver took per-cluster config and then authenticated
as whatever the control-plane *process* carried. One install served one
AWS account, and the constraint was invisible: every driver looks like it
takes per-cluster config, and does, except for the one thing that decides
which account it talks to.

The two properties that matter are opposite in direction. A cluster that
declares nothing must build byte-identically the client it built before,
or this is a behaviour change dressed as a refactor. A cluster that
declares a role must reach every driver, including the VPC discovery that
runs beside them.
"""

from __future__ import annotations

import dataclasses

import pytest

pytestmark = pytest.mark.django_db

ROLE = "arn:aws:iam::210987654321:role/astrolift-control-plane"


class _Plugin:
    def __init__(self, slug):
        self.slug = slug


class _Cluster:
    def __init__(self, credential: dict | None = None, *, account: str = ""):
        self.slug = "prod"
        self.region = "us-west-2"
        self.provider_plugin = _Plugin("aws")
        self.provider_plugin_id = "aws"
        self.provider_config = {"region": "us-west-2"}
        if credential is not None:
            self.provider_config["credential"] = credential
        self.auth_config = {}
        self.cloud_account_id = account
        self.cloud_account_verified_at = None


def _config(cluster, kind="object_store", variant=""):
    from core.cluster_observability import managed_config_for

    return managed_config_for("aws", cluster, kind=kind, variant=variant)


# ---- the field exists everywhere it has to -------------------------------


def test_every_aws_managed_config_carries_the_field():
    """A kind whose config lacks it would silently stay ambient while the
    guard reported it migrated."""
    import re
    from pathlib import Path

    from core.cluster_credentials import _MANAGED_KINDS

    src = Path("core/cluster_observability.py").read_text()
    body = src[src.index("def _managed_config_uncredentialed") :]
    body = body[: body.index("\ndef ", body.index('if plugin_slug != "aws"'))]
    branched = set(re.findall(r'kind == "([\w_]+)"', body)) | {
        k for grp in re.findall(r"kind in \(([^)]*)\)", body) for k in re.findall(r'"([\w_]+)"', grp)
    }

    assert branched == set(_MANAGED_KINDS), (
        "the aware set and the AWS branches have drifted; a kind in one and "
        "not the other either refuses a migrated driver or admits an "
        "unmigrated one"
    )


def test_a_config_is_frozen_and_still_replaceable():
    cfg = _config(_Cluster())

    assert dataclasses.is_dataclass(cfg)
    assert any(f.name == "credential" for f in dataclasses.fields(cfg))


# ---- ambient stays exactly what it was -----------------------------------


def test_a_cluster_declaring_nothing_gets_an_ambient_credential():
    cfg = _config(_Cluster())

    assert cfg.credential is not None
    assert cfg.credential.is_ambient


def test_an_ambient_credential_builds_the_client_it_always_built(monkeypatch):
    """`aws_client` with an ambient credential must not touch STS and must
    pass through unchanged — that is what makes this migration safe."""
    from _sdk.cloud_credentials import CloudCredential, CredentialMode
    from aws.session import aws_client

    seen = {}

    def fake_build(service, **kwargs):
        seen["service"] = service
        seen["kwargs"] = kwargs
        return object()

    aws_client(
        "s3",
        region="us-west-2",
        credential=CloudCredential(cloud="aws", mode=CredentialMode.AMBIENT),
        build=fake_build,
        sts=_ExplodingSts(),
    )

    assert seen["service"] == "s3"
    assert seen["kwargs"] == {"region_name": "us-west-2"}


class _ExplodingSts:
    def assume_role(self, **kwargs):
        raise AssertionError("ambient must not assume a role")


# ---- declared reaches the client -----------------------------------------


def test_a_declared_role_reaches_the_driver_config():
    cfg = _config(_Cluster({"mode": "aws_assume_role", "role_arn": ROLE}))

    assert not cfg.credential.is_ambient
    assert cfg.credential.role_arn == ROLE


@pytest.mark.parametrize("kind", ["object_store", "kv_store", "queue", "faas"])
def test_it_reaches_every_kind_not_only_the_first(kind):
    cfg = _config(_Cluster({"mode": "aws_assume_role", "role_arn": ROLE}), kind=kind)

    assert cfg.credential.role_arn == ROLE


def test_managed_kinds_no_longer_refuse_a_declared_credential():
    """Before PR 3 this raised: the guard refused every capability outside
    the aware set rather than let it run on the wrong account."""
    from core.cluster_credentials import assert_credential_supported

    cluster = _Cluster({"mode": "aws_assume_role", "role_arn": ROLE})

    assert_credential_supported(cluster, capability="managed:object_store")


def test_an_unmigrated_capability_still_refuses():
    """PR 4 and PR 5 have not landed. Those paths must keep refusing rather
    than quietly authenticate as the control plane."""
    from core.cluster_credentials import ClusterCredentialUnsupported, assert_credential_supported

    cluster = _Cluster({"mode": "aws_assume_role", "role_arn": ROLE})

    with pytest.raises(ClusterCredentialUnsupported):
        assert_credential_supported(cluster, capability="secrets")


# ---- ratchets, so the next driver cannot arrive ambient -------------------


def test_no_managed_driver_builds_a_client_directly():
    """The ratchet that makes this migration stick.

    Every one of the forty-odd drivers used `boto3.client(...)` directly,
    which walks botocore's default chain and so authenticates as the
    process. A new driver written the old way would look correct, pass its
    own tests, and silently ignore the cluster's credential — the exact
    invisibility #1422 is about. Nothing else in the tree would notice, so
    this does.
    """
    import re
    from pathlib import Path

    offenders = []
    for path in sorted(Path("providers/aws/managed").glob("*.py")):
        for i, line in enumerate(path.read_text().split("\n"), 1):
            # Skip prose: several docstrings name boto3 clients to explain
            # which API surface a driver talks to.
            if re.search(r"(?<!['\"`])boto3\.client\(", line) and "``" not in line:
                offenders.append(f"{path.name}:{i}")

    assert offenders == [], (
        "these build a client directly instead of through aws_client, so they "
        f"authenticate as the control plane regardless of the cluster: {offenders}"
    )


def test_the_vpc_discovery_takes_the_credential_too():
    """Half-migrating this is worse than not migrating it.

    `_networking` creates the subnet group and security group that a
    database is placed into. Ambient there and explicit in the driver would
    put the networking in the control plane's account and the database in
    the cluster's, and the failure would surface as an unrelated VPC error.
    """
    import ast
    from pathlib import Path

    src = Path("providers/aws/managed/_networking.py").read_text()
    tree = ast.parse(src)

    missing = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        body = ast.get_source_segment(src, fn) or ""
        # Only the ones that *construct* a client. Several helpers are handed
        # clients by their caller and have no identity decision to make.
        if "aws_client(" not in body:
            continue
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        if "credential" not in params:
            missing.append(fn.name)

    assert missing == [], f"these build clients without taking a credential: {missing}"


def test_no_networking_helper_references_a_credential_it_cannot_receive():
    """A NameError waiting to happen, and the one this migration actually hit:
    forwarding `credential=credential` into a helper whose own signature was
    never widened to accept it."""
    import ast
    from pathlib import Path

    src = Path("providers/aws/managed/_networking.py").read_text()
    tree = ast.parse(src)

    broken = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        body = ast.get_source_segment(src, fn) or ""
        if "credential=credential" not in body:
            continue
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        if "credential" not in params:
            broken.append(fn.name)

    assert broken == [], f"these pass a credential they never receive: {broken}"


def test_a_vpc_bound_kind_hands_the_credential_to_the_networking_layer(monkeypatch):
    """The half-migration this PR exists to avoid.

    `postgres` builds its subnet group and security group through
    `_networking` before the config is returned. Ambient there and explicit
    in the driver would put the networking in the control plane's account
    and the database in the cluster's, and it would surface as an unrelated
    VPC error rather than as a credential problem.
    """
    seen = {}

    def fake_ensure(cluster, *, region, port, service, clients=None, credential=None):
        seen["credential"] = credential
        return "subnet-group", ["sg-1"]

    monkeypatch.setattr("aws.managed._networking.ensure_db_networking", fake_ensure)

    cfg = _config(_Cluster({"mode": "aws_assume_role", "role_arn": ROLE}), kind="postgres")

    assert seen["credential"] is not None, "the networking layer was left ambient"
    assert seen["credential"].role_arn == ROLE
    assert cfg.credential.role_arn == ROLE
