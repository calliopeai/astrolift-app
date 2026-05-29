"""Tests for storage encryption policy + local-dev handling (#28)."""

from __future__ import annotations

from _sdk.storage_encryption import (
    POLICIES,
    check_encryption,
    policy_for,
)


def test_every_data_variant_has_policy() -> None:
    """Every (plugin, kind, variant) that stores tenant data must
    have an encryption policy. New variants without a policy fail
    the encryption preflight by default."""
    expected_keys = {
        ("aws", "object_store", "s3"),
        ("aws", "queue", "sqs"),
        ("gcp", "object_store", "gcs"),
        ("gcp", "queue", "pubsub"),
        ("azure", "object_store", "blob"),
        ("azure", "queue", "servicebus"),
        ("k8s_native", "postgres", "cnpg"),
        ("k8s_native", "redis", "operator"),
        ("k8s_native", "mysql", "operator"),
        ("k8s_native", "document_db", "mongodb_operator"),
        ("k8s_native", "event_stream", "kafka_strimzi"),
        ("k8s_native", "event_stream", "nats"),
        ("k8s_native", "queue", "rabbitmq_operator"),
        ("k8s_native", "filesystem", "nfs_csi"),
    }
    assert expected_keys <= set(POLICIES.keys())


def test_cloud_variants_default_to_kms() -> None:
    s3 = policy_for(plugin_id="aws", kind="object_store", variant="s3")
    assert s3 is not None
    assert s3.mode == "cloud_kms"
    assert s3.cmek_supported is True


def test_k8s_variants_default_to_pv_luks() -> None:
    cnpg = policy_for(
        plugin_id="k8s_native", kind="postgres", variant="cnpg",
    )
    assert cnpg is not None
    assert cnpg.mode == "pv_luks"


def test_check_with_cmek_passes() -> None:
    result = check_encryption(
        plugin_id="aws", kind="object_store", variant="s3",
        cmek_key="arn:aws:kms:us-east-1:123:key/abc",
    )
    assert result.ok is True
    assert result.cmek_used is True
    assert result.failures == []


def test_pci_compliance_without_cmek_fails() -> None:
    """Compliance framework requires CMEK but bind didn't supply
    a key — fail bind."""
    result = check_encryption(
        plugin_id="aws", kind="object_store", variant="s3",
        compliance_frameworks=["pci"],
    )
    assert result.ok is False
    assert any("pci" in f.lower() for f in result.failures)


def test_pci_compliance_in_local_dev_warns_not_fails() -> None:
    """Local-dev allows iteration without full KMS plumbing —
    surface the warning explicitly so promotion fails."""
    result = check_encryption(
        plugin_id="aws", kind="object_store", variant="s3",
        compliance_frameworks=["pci"],
        is_local_dev=True,
    )
    assert result.ok is True
    assert any(
        "local-dev" in w for w in result.warnings
    )


def test_unknown_variant_fails_closed() -> None:
    result = check_encryption(
        plugin_id="aws", kind="postgres", variant="not_yet_added",
    )
    assert result.ok is False
    assert any("no encryption policy" in f for f in result.failures)


def test_cmek_on_unsupported_variant_warns() -> None:
    """Supplying a CMEK to a variant that doesn't support it —
    operator probably misconfigured. Warn + accept."""
    result = check_encryption(
        plugin_id="k8s_native", kind="postgres", variant="cnpg",
        cmek_key="arn:aws:kms:...",
    )
    assert result.ok is True
    assert result.cmek_used is False
    assert any("doesn't support CMEK" in w for w in result.warnings)


def test_in_transit_default_on() -> None:
    """All policies have in_transit_default=True by default."""
    for policy in POLICIES.values():
        assert policy.in_transit_default is True


def test_compliance_required_only_for_payment_or_health() -> None:
    """CMEK-required-for-compliance shouldn't be set for variants
    where the cloud doesn't even support CMEK."""
    for key, policy in POLICIES.items():
        if policy.cmek_required_for_compliance:
            assert policy.cmek_supported, (
                f"{key} declares CMEK-required compliance but "
                f"cmek_supported=False — contradiction"
            )
