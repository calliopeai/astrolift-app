"""AWS provider plugin manifest.

This plugin will implement drivers for EKS, ALB ingress, Route 53 DNS,
ACM TLS, Secrets Manager, IRSA workload identity, ECR, S3, CloudWatch
logs, and CloudWatch metrics.

Each driver will be implemented in a separate module (e.g. cluster.py,
ingress_alb.py, dns_route53.py) and registered in the PLUGIN manifest
below.
"""

from _sdk.base import ProviderPlugin


class AWSProviderPlugin:
    """AWS provider plugin -- stub.

    Individual driver implementations will be added as separate modules
    and registered in the PLUGIN manifest.
    """

    def __init__(self) -> None:
        raise NotImplementedError("AWS provider plugin is not yet implemented")


# Plugin manifest. Drivers will be populated as they are implemented.
# See specs/23-provider-plugin-aws.md for the full driver catalog.
PLUGIN = ProviderPlugin(
    id="aws",
    display_name="Amazon Web Services",
    drivers={},
    managed_service_drivers={},
    config_schema={},
)
