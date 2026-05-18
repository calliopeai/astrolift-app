"""Tests for ``UnsupportedOperationError`` (#614, #618, #619).

This is the exception type that replaces bare ``NotImplementedError``
in the partial-stub spots the audit flagged (SDK observability-method
defaults + the registry/event-stream/queue snapshot-restore stubs).

Resolvers catch this exception to translate to a "not supported on this
cloud" user-facing message; bare ``NotImplementedError`` keeps surfacing
as a 500. The class is a subclass of ``NotImplementedError`` so
``except NotImplementedError`` catches both -- the resolver layer is
opt-in to the narrower type.
"""

from __future__ import annotations

import pytest

from _sdk import UnsupportedOperationError
from _sdk.dns import DnsDriver
from _sdk.identity import WorkloadIdentityDriver
from _sdk.tls import TlsDriver


def test_unsupported_is_subclass_of_not_implemented() -> None:
    """``except NotImplementedError`` still catches it; resolvers that
    want the narrower category can ``except UnsupportedOperationError``."""
    assert issubclass(UnsupportedOperationError, NotImplementedError)


def test_sdk_dns_list_records_for_app_raises_unsupported() -> None:
    class _D(DnsDriver):
        def ensure_record(self, zone, name, type, value, *, ttl=300):  # type: ignore[override]
            raise AssertionError

        def delete_record(self, zone, name, type):  # type: ignore[override]
            raise AssertionError

        def list_records(self, zone):  # type: ignore[override]
            raise AssertionError

    # The protocol default raises UnsupportedOperationError; subclass
    # falls through.
    driver = _D()
    with pytest.raises(UnsupportedOperationError):
        DnsDriver.list_records_for_app(driver, "acme.example")


def test_sdk_tls_list_certificates_raises_unsupported() -> None:
    class _T(TlsDriver):
        def ensure_certificate(
            self,
            domain,
            *,
            sans=None,
            strategy="letsencrypt",
        ):  # type: ignore[override]
            raise AssertionError

        def get_certificate(self, certificate_id):  # type: ignore[override]
            raise AssertionError

        def revoke_certificate(self, certificate_id):  # type: ignore[override]
            raise AssertionError

    with pytest.raises(UnsupportedOperationError):
        TlsDriver.list_certificates(_T(), "host")


def test_sdk_identity_describe_identity_raises_unsupported() -> None:
    class _I(WorkloadIdentityDriver):
        def bind_service_account(
            self,
            cluster,
            namespace,
            sa_name,
            identity_role,
        ):  # type: ignore[override]
            raise AssertionError

        def create_identity_role(self, name, permissions):  # type: ignore[override]
            raise AssertionError

        def attach_policy(self, role, policy):  # type: ignore[override]
            raise AssertionError

        def delete_identity_role(self, role):  # type: ignore[override]
            raise AssertionError

    with pytest.raises(UnsupportedOperationError):
        WorkloadIdentityDriver.describe_identity(_I(), "myapp")


def test_force_delete_repo_raises_unsupported_on_non_aws_registries() -> None:
    """#614 -- the partial NotImplementedError stubs in GCP/Azure/k8s_native
    delete_repo paths now raise UnsupportedOperationError so resolvers can
    translate the cap-gap into a user-facing message."""
    from azure.registry_acr import ACRConfig, ACRDriver
    from gcp.registry_artifact import ArtifactRegistryConfig, ArtifactRegistryDriver
    from k8s_native.registry_oci import OCIRegistryConfig, OCIRegistryDriver

    acr = ACRDriver(
        config=ACRConfig(
            subscription_id="sub",
            resource_group="rg",
            registry_name="acme",
            client=object(),
        )
    )
    with pytest.raises(UnsupportedOperationError):
        acr.delete_repo("foo", archive=False)

    ar = ArtifactRegistryDriver(
        config=ArtifactRegistryConfig(
            project_id="p",
            location="us",
            repository_id="r",
            client=object(),
        )
    )
    with pytest.raises(UnsupportedOperationError):
        ar.delete_repo("foo", archive=False)

    oci = OCIRegistryDriver(
        config=OCIRegistryConfig(registry_url="https://x"),
    )
    with pytest.raises(UnsupportedOperationError):
        oci.delete_repo("foo", archive=False)


def test_force_delete_repo_archive_true_is_no_op() -> None:
    """archive=True (the default) should still be a no-op on every backend."""
    from azure.registry_acr import ACRConfig, ACRDriver
    from gcp.registry_artifact import ArtifactRegistryConfig, ArtifactRegistryDriver
    from k8s_native.registry_oci import OCIRegistryConfig, OCIRegistryDriver

    acr = ACRDriver(
        config=ACRConfig(
            subscription_id="sub",
            resource_group="rg",
            registry_name="acme",
            client=object(),
        )
    )
    acr.delete_repo("foo", archive=True)

    ar = ArtifactRegistryDriver(
        config=ArtifactRegistryConfig(
            project_id="p",
            location="us",
            repository_id="r",
            client=object(),
        )
    )
    ar.delete_repo("foo", archive=True)

    oci = OCIRegistryDriver(
        config=OCIRegistryConfig(registry_url="https://x"),
    )
    oci.delete_repo("foo", archive=True)
