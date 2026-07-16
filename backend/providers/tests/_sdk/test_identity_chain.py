"""Tests for cross-account workload identity chains (#64)."""

from __future__ import annotations

from _sdk.identity_chain import (
    IdentityChain,
    IdentityHop,
    build_simple_chain,
    validate_chain,
)


def test_simple_chain_passes() -> None:
    chain = build_simple_chain(
        namespace="acme-api",
        sa_name="api",
        target_plugin_id="aws",
        target_account_id="123456789012",
        target_identity_name="astrolift-api-role",
    )
    result = validate_chain(chain)
    assert result.ok is True


def test_empty_chain_fails() -> None:
    chain = IdentityChain(
        hops=(),
        namespace="ns",
        sa_name="sa",
    )
    result = validate_chain(chain)
    assert result.ok is False
    assert result.failures[0].code == "empty_chain"


def test_cycle_detected() -> None:
    chain = IdentityChain(
        namespace="ns",
        sa_name="sa",
        hops=(
            IdentityHop(
                plugin_id="k8s_native",
                account_id="local-cluster",
                identity_name="ns/sa",
            ),
            IdentityHop(
                plugin_id="aws",
                account_id="111",
                identity_name="role-A",
                audience="sts.amazonaws.com",
                trust_principal="oidc:ns:sa",
            ),
            IdentityHop(
                plugin_id="aws",
                account_id="111",
                identity_name="role-A",  # repeat!
            ),
        ),
    )
    result = validate_chain(chain)
    assert result.ok is False
    assert any(f.code == "cycle" for f in result.failures)


def test_cross_cloud_aws_to_gcp_supported() -> None:
    chain = IdentityChain(
        namespace="ns",
        sa_name="sa",
        hops=(
            IdentityHop(
                plugin_id="k8s_native",
                account_id="local-cluster",
                identity_name="ns/sa",
            ),
            IdentityHop(
                plugin_id="aws",
                account_id="111",
                identity_name="hub-role",
                audience="sts.amazonaws.com",
                trust_principal="oidc:ns:sa",
            ),
            IdentityHop(
                plugin_id="gcp",
                account_id="acme-prod",
                identity_name="data-reader@acme-prod.iam",
                audience="https://oidc.example.com/aws-hub",
                trust_principal="aws:sts:role/hub-role",
            ),
        ),
    )
    result = validate_chain(chain)
    assert result.ok is True


def test_unsupported_cross_plugin_pair_fails() -> None:
    """k8s_native → k8s_native (different cluster) isn't a defined
    cross-plugin transition."""
    chain = IdentityChain(
        namespace="ns",
        sa_name="sa",
        hops=(
            IdentityHop(
                plugin_id="aws",
                account_id="111",
                identity_name="role-A",
            ),
            IdentityHop(
                plugin_id="other_cloud",
                account_id="222",
                identity_name="role-B",
                audience="sts",
            ),
        ),
    )
    result = validate_chain(chain)
    assert result.ok is False
    codes = {f.code for f in result.failures}
    assert "unsupported_cross_plugin" in codes


def test_cross_plugin_hop_without_audience_fails() -> None:
    """OIDC trust requires the next hop to claim a specific
    audience — missing it is a misconfiguration."""
    chain = IdentityChain(
        namespace="ns",
        sa_name="sa",
        hops=(
            IdentityHop(
                plugin_id="k8s_native",
                account_id="local-cluster",
                identity_name="ns/sa",
            ),
            IdentityHop(
                plugin_id="aws",
                account_id="111",
                identity_name="role",
                # audience missing!
            ),
        ),
    )
    result = validate_chain(chain)
    assert result.ok is False
    assert any(f.code == "missing_audience" for f in result.failures)


def test_terminal_hop_accessor() -> None:
    chain = build_simple_chain(
        namespace="ns",
        sa_name="sa",
        target_plugin_id="aws",
        target_account_id="111",
        target_identity_name="role",
    )
    assert chain.terminal().identity_name == "role"


def test_intra_plugin_aws_chain() -> None:
    """AWS-to-AWS account chaining (cross-account AssumeRole)."""
    chain = IdentityChain(
        namespace="ns",
        sa_name="sa",
        hops=(
            IdentityHop(
                plugin_id="k8s_native",
                account_id="local-cluster",
                identity_name="ns/sa",
            ),
            IdentityHop(
                plugin_id="aws",
                account_id="111",
                identity_name="hub-role",
                audience="sts.amazonaws.com",
                trust_principal="oidc:ns:sa",
            ),
            IdentityHop(
                plugin_id="aws",
                account_id="222",
                identity_name="spoke-role",
                trust_principal="aws:iam::111:role/hub-role",
            ),
        ),
    )
    result = validate_chain(chain)
    assert result.ok is True
