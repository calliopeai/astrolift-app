"""Named OCI registry variants (#60).

Quay, DockerHub, GHCR all speak OCI Distribution Spec v1, so they
share the generic OCIRegistryDriver — they differ only in the host
URL and the credential-source label. These factories pin the host
+ label so the plugin manifest can register them as distinct
variants without copy-pasting driver code.
"""

from __future__ import annotations

from k8s_native.registry_oci import OCIRegistryConfig, OCIRegistryDriver


def quay_driver(
    *,
    organization: str,
    username: str = "",
    password: str = "",
    http_client: object | None = None,
) -> OCIRegistryDriver:
    """Build an OCIRegistryDriver pointed at quay.io. `organization`
    is the Quay organization namespace (e.g. 'redhat')."""
    return OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url=f"https://quay.io/{organization}",
            username=username,
            password=password,
            http_client=http_client,
        ),
    )


def dockerhub_driver(
    *,
    namespace: str,
    username: str = "",
    password: str = "",
    http_client: object | None = None,
) -> OCIRegistryDriver:
    """Build an OCIRegistryDriver pointed at Docker Hub. `namespace`
    is the user / org name (the segment before the repo)."""
    return OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url=f"https://docker.io/{namespace}",
            username=username,
            password=password,
            http_client=http_client,
        ),
    )


def ghcr_driver(
    *,
    organization: str,
    pat: str = "",
    username: str = "",
    http_client: object | None = None,
) -> OCIRegistryDriver:
    """Build an OCIRegistryDriver pointed at GitHub Container
    Registry. Auth is a Personal Access Token with read:packages
    + write:packages."""
    return OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url=f"https://ghcr.io/{organization}",
            username=username or "ghcr",
            password=pat,
            http_client=http_client,
        ),
    )


def harbor_driver(
    *,
    base_url: str,
    project: str,
    robot_username: str = "",
    robot_password: str = "",
    http_client: object | None = None,
) -> OCIRegistryDriver:
    """Build an OCIRegistryDriver pointed at a self-hosted Harbor.
    Project is the Harbor project namespace; robot_username/password
    are a robot-account credential for least-privilege push/pull."""
    return OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url=f"{base_url.rstrip('/')}/{project}",
            username=robot_username,
            password=robot_password,
            http_client=http_client,
        ),
    )
