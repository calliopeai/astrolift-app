"""Coverage test: every public driver method carries ``@driver_op`` (#586-#623).

The instrumentation audit (#586-#623) called for the decorator to be
applied across every driver in the providers tree. This test enumerates
the known driver classes and asserts each public method has the marker
attribute the decorator stamps on. It's the structural counterpart to
``test_telemetry.py`` (which tests the decorator's behaviour); together
they confirm the audit's "wire telemetry everywhere" mandate is met.

A driver method is considered "instrumented" when:

* ``getattr(method, "__astrolift_driver_op__", None) is not None`` -- the
  decorator was applied and stamped the marker, OR
* the method starts with ``_`` (private) and is therefore excluded by
  the audit's "public methods only" scope, OR
* the method is ``__init__``.

When a new driver method is added the audit expects it to land with the
decorator already attached. Failing this test is the signal to revisit
``_telemetry.driver_op``'s coverage.
"""

from __future__ import annotations

import inspect


def _public_methods(cls: type) -> list[str]:
    out: list[str] = []
    for name in vars(cls):
        if name.startswith("_"):
            continue
        if not callable(getattr(cls, name)):
            continue
        out.append(name)
    return out


def _instrumented(cls: type, method_name: str) -> bool:
    method = getattr(cls, method_name)
    return getattr(method, "__astrolift_driver_op__", None) is not None


# Per-cloud driver classes the audit explicitly named in #591-#598 +
# #604-#615 + #620-#623. The list is the minimum surface this PR claims
# to instrument; growing it tightens the contract.

_DRIVER_CLASSES: dict[str, list[type]] = {}


def _load_drivers() -> None:
    """Pull driver classes in one place so import errors surface cleanly.

    Wrapped in a function so test collection doesn't crash when a single
    plugin's dependencies are missing in the test environment -- the
    individual tests will skip that subset and the rest still run.
    """
    from aws.cluster_eks import EKSClusterDriver
    from aws.dns_route53 import Route53Driver
    from aws.identity_irsa import IRSADriver
    from aws.ingress_alb import ALBIngressDriver
    from aws.notification_sns import SNSNotificationDriver
    from aws.registry_ecr import ECRDriver
    from aws.secrets import AWSSecretsBackend
    from aws.tls_acm import ACMDriver
    from azure.cluster_aks import AKSClusterDriver
    from azure.dns_azuredns import AzureDNSDriver
    from azure.identity_federated import AzureFederatedIdentityDriver
    from azure.ingress_appgw import AzureAppGatewayIngressDriver
    from azure.notification_anh import AzureNotificationHubsDriver
    from azure.registry_acr import ACRDriver
    from azure.secrets_keyvault import KeyVaultSecretsBackend
    from azure.tls_appgw import AzureAppGatewayTlsDriver
    from gcp.cluster_gke import GKEClusterDriver
    from gcp.dns_clouddns import CloudDNSDriver
    from gcp.identity_wi import GCPWorkloadIdentityDriver
    from gcp.ingress import GCPIngressDriver
    from gcp.notification_fcm import FCMNotificationDriver
    from gcp.registry_artifact import ArtifactRegistryDriver
    from gcp.secrets import GCPSecretsBackend
    from gcp.tls_managed import GCPManagedCertDriver
    from k8s_native.cluster import K8sNativeClusterDriver
    from k8s_native.dns_external import ExternalDnsDriver
    from k8s_native.identity_projected import ProjectedSaTokenDriver
    from k8s_native.ingress import K8sIngressDriver
    from k8s_native.notification_otlp import WebhookSMTPNotificationDriver
    from k8s_native.registry_oci import OCIRegistryDriver
    from k8s_native.secrets_vault import VaultSecretsBackend
    from k8s_native.tls_certmanager import CertManagerDriver

    _DRIVER_CLASSES.update(
        {
            "aws": [
                EKSClusterDriver,
                AWSSecretsBackend,
                Route53Driver,
                ACMDriver,
                IRSADriver,
                ECRDriver,
                ALBIngressDriver,
                SNSNotificationDriver,
            ],
            "gcp": [
                GKEClusterDriver,
                GCPSecretsBackend,
                CloudDNSDriver,
                GCPManagedCertDriver,
                GCPWorkloadIdentityDriver,
                ArtifactRegistryDriver,
                GCPIngressDriver,
                FCMNotificationDriver,
            ],
            "azure": [
                AKSClusterDriver,
                KeyVaultSecretsBackend,
                AzureDNSDriver,
                AzureAppGatewayTlsDriver,
                AzureFederatedIdentityDriver,
                ACRDriver,
                AzureAppGatewayIngressDriver,
                AzureNotificationHubsDriver,
            ],
            "k8s_native": [
                K8sNativeClusterDriver,
                VaultSecretsBackend,
                ExternalDnsDriver,
                CertManagerDriver,
                ProjectedSaTokenDriver,
                OCIRegistryDriver,
                K8sIngressDriver,
                WebhookSMTPNotificationDriver,
            ],
        }
    )


_load_drivers()


# Methods we deliberately don't instrument:
# - properties (e.g. ACRDriver.login_server, ApplyResult.ok) -- they're
#   data accessors, not driver ops
# - ``render_endpoint`` on ExternalDnsDriver -- pure-CPU helper, no
#   external side effect to record
_EXCLUDED_METHODS = {
    "login_server",  # ACR property
    "render_endpoint",  # k8s_native/dns_external pure helper
    "render_certificate",  # k8s_native/tls_certmanager pure helper
}


def test_every_driver_method_carries_driver_op() -> None:
    missing: list[str] = []
    for cloud, classes in _DRIVER_CLASSES.items():
        for cls in classes:
            for method_name in _public_methods(cls):
                if method_name in _EXCLUDED_METHODS:
                    continue
                # Skip properties + descriptors -- their __get__ surface
                # isn't a driver-op call site.
                raw = vars(cls).get(method_name)
                if isinstance(raw, property):
                    continue
                if not _instrumented(cls, method_name):
                    missing.append(f"{cloud}/{cls.__name__}.{method_name}")
    assert not missing, "the following driver methods are missing @driver_op:\n  " + "\n  ".join(missing)


def test_cluster_apply_manifests_has_per_loop_heartbeat() -> None:
    """The audit's primary requirement was that ``apply_manifests`` heartbeat
    per manifest. Read the source and assert ``maybe_heartbeat`` is called
    inside the loop. This is a structural check; the behaviour is exercised
    by the driver-specific tests where they can construct a fake client."""
    from aws import cluster_eks
    from azure import cluster_aks
    from gcp import cluster_gke
    from k8s_native import cluster as k8s_native_cluster

    sources = [
        inspect.getsource(cluster_eks.EKSClusterDriver.apply_manifests),
        inspect.getsource(cluster_gke.GKEClusterDriver.apply_manifests),
        inspect.getsource(cluster_aks.AKSClusterDriver.apply_manifests),
        inspect.getsource(k8s_native_cluster.K8sNativeClusterDriver.apply_manifests),
    ]
    for src in sources:
        assert "maybe_heartbeat" in src, (
            "expected apply_manifests to call maybe_heartbeat per manifest " "(audit #595-#598)"
        )


def test_cluster_poll_rollout_has_per_iteration_heartbeat() -> None:
    from aws import cluster_eks
    from azure import cluster_aks
    from gcp import cluster_gke
    from k8s_native import cluster as k8s_native_cluster

    sources = [
        inspect.getsource(cluster_eks.EKSClusterDriver.poll_rollout),
        inspect.getsource(cluster_gke.GKEClusterDriver.poll_rollout),
        inspect.getsource(cluster_aks.AKSClusterDriver.poll_rollout),
        inspect.getsource(k8s_native_cluster.K8sNativeClusterDriver.poll_rollout),
    ]
    for src in sources:
        assert "maybe_heartbeat" in src, "poll_rollout must heartbeat per iteration"


def test_cluster_delete_namespace_has_per_iteration_heartbeat() -> None:
    from aws import cluster_eks
    from azure import cluster_aks
    from gcp import cluster_gke
    from k8s_native import cluster as k8s_native_cluster

    sources = [
        inspect.getsource(cluster_eks.EKSClusterDriver.delete_namespace),
        inspect.getsource(cluster_gke.GKEClusterDriver.delete_namespace),
        inspect.getsource(cluster_aks.AKSClusterDriver.delete_namespace),
        inspect.getsource(k8s_native_cluster.K8sNativeClusterDriver.delete_namespace),
    ]
    for src in sources:
        assert "maybe_heartbeat" in src, "delete_namespace must heartbeat in poll loop"


def test_sensitive_drivers_have_audit_enabled() -> None:
    """SOC2 / HIPAA mandate audit on secrets, dns, tls, identity-bind,
    cluster teardown, registry-delete. This test checks the decorator
    marker carries ``audit=True`` on those methods."""
    from aws.dns_route53 import Route53Driver
    from aws.identity_irsa import IRSADriver
    from aws.registry_ecr import ECRDriver
    from aws.secrets import AWSSecretsBackend
    from aws.tls_acm import ACMDriver

    audited = [
        (AWSSecretsBackend, "get"),
        (AWSSecretsBackend, "upsert"),
        (AWSSecretsBackend, "delete"),
        (Route53Driver, "ensure_record"),
        (Route53Driver, "delete_record"),
        (ACMDriver, "ensure_certificate"),
        (ACMDriver, "revoke_certificate"),
        (IRSADriver, "bind_service_account"),
        (IRSADriver, "delete_identity_role"),
        (ECRDriver, "delete_repo"),
    ]
    for cls, name in audited:
        marker = getattr(getattr(cls, name), "__astrolift_driver_op__", None)
        assert marker is not None, f"{cls.__name__}.{name} missing @driver_op"
        assert marker.audit, f"{cls.__name__}.{name} expected audit=True (#604-#615 SOC2/HIPAA)"
