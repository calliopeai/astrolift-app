"""AWS capability configs, resolved from the tenant cluster's provider_config.

Mirrors the gcp/azure/k8s twins. Added with #1678, whose whole failure was a
config field the driver needed and the resolver never supplied.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.app_deploy import _config_for_capability

BOUNDARY = "arn:aws:iam::123456789012:policy/calliope-agent-boundary"


def _cluster(**provider_overrides: object) -> SimpleNamespace:
    provider_config: dict[str, object] = {
        "account_id": "123456789012",
        "region": "us-west-2",
    }
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="conflictastro",
        region="us-west-2",
        provider_config=provider_config,
        auth_config={"cluster_oidc_issuer": "oidc.eks.us-west-2.amazonaws.com/id/ABC123"},
    )


def test_irsa_config_carries_the_agent_permissions_boundary() -> None:
    """An agent-installed (pull mode) cluster records the boundary its agent
    runs under. The driver has to attach it to every role it creates, or the
    agent's own DenyRoleCreationWithoutThisBoundary refuses the CreateRole."""
    config = _config_for_capability("aws", _cluster(iam_permissions_boundary_arn=BOUNDARY), "identity")

    assert type(config).__name__ == "IRSAConfig"
    assert config.permissions_boundary_arn == BOUNDARY


def test_irsa_config_has_no_boundary_on_an_admin_provisioned_cluster() -> None:
    """Push mode has no boundary, and IAM rejects an empty one, so the resolver
    must yield the empty string the driver knows to omit."""
    config = _config_for_capability("aws", _cluster(), "identity")

    assert config.permissions_boundary_arn == ""


def test_irsa_role_path_stays_root_so_the_astrolift_prefix_matches() -> None:
    """The task role is granted iam:CreateRole on ``role/astrolift-*``. A
    non-root path would put the role at ``role/<path>/astrolift-...``, which
    that prefix does not match, and CreateRole would be denied."""
    config = _config_for_capability("aws", _cluster(), "identity")

    assert config.role_path == "/"


def test_ecr_config_reads_operator_controls() -> None:
    config = _config_for_capability(
        "aws",
        _cluster(image_scanning_enabled=False, image_tag_mutability="MUTABLE"),
        "registry",
    )

    assert type(config).__name__ == "ECRConfig"
    assert config.account_id == "123456789012"
    assert config.image_scanning_enabled is False
    assert config.image_tag_mutability == "MUTABLE"


def test_ecr_config_carries_the_agent_permissions_boundary() -> None:
    """``ensure_ci_push_role`` mints an IAM role too (#1906) — it needs the
    same boundary ``IRSAConfig`` already carries, or the agent's own
    DenyRoleCreationWithoutThisBoundary refuses the CreateRole (installer#313)."""
    config = _config_for_capability("aws", _cluster(iam_permissions_boundary_arn=BOUNDARY), "registry")

    assert type(config).__name__ == "ECRConfig"
    assert config.permissions_boundary_arn == BOUNDARY


def test_ecr_config_has_no_boundary_on_an_admin_provisioned_cluster() -> None:
    """Push mode has no boundary, and IAM rejects an empty one, so the resolver
    must yield the empty string the driver knows to omit."""
    config = _config_for_capability("aws", _cluster(), "registry")

    assert config.permissions_boundary_arn == ""


def test_sql_server_config_carries_the_install_option_group_allowlist(monkeypatch) -> None:
    """The driver refuses an option group outside this list (#2087): one can
    carry the IAM role SQL Server's native backup and restore reads S3 with.
    An install that sets none refuses every config-supplied group."""
    from core.cluster_observability import managed_config_for

    monkeypatch.setattr(
        "aws.managed._networking.ensure_db_networking",
        lambda cluster, **kwargs: ("subnet-group", ["sg-1"]),
    )

    listed = managed_config_for(
        "aws",
        _cluster(mssql_allowed_option_groups=["platform-native-backup"]),
        kind="mssql",
        variant="rds_sqlserver_standard",
    )
    unset = managed_config_for("aws", _cluster(), kind="mssql", variant="rds_sqlserver_standard")

    assert listed.allowed_option_groups == ("platform-native-backup",)
    assert unset.allowed_option_groups == ()
