"""BuildDriver protocol (#56) -- build container images from source.

Variants: kaniko (in-cluster), buildpacks (cloud-native), buildkit
(daemonless), docker_build (legacy), gcp_cloud_build, aws_codebuild,
azure_pipelines.

The driver is provider-agnostic at the SDK level — the cluster-bound
plugin picks the variant. Output is always an image reference push-able
to any ImageRegistryDriver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class BuildSpec:
    source_uri: str
    """git+https://... | tar+s3://... | local path."""

    dockerfile_path: str = "Dockerfile"
    context_path: str = "."
    target_platform: str = "linux/amd64"
    build_args: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    """Build-time secrets exposed via --mount=type=secret. Values
    are references resolved by the cluster's SecretsBackend."""

    cache_from: list[str] = field(default_factory=list)
    """Image refs to use as build cache."""


@dataclass(frozen=True)
class BuildResult:
    success: bool
    image_uri: str
    """Pushed image ref, e.g. registry/repo:tag."""

    digest: str
    """sha256:... — content-addressable."""

    duration_seconds: float
    log_url: str | None = None
    errors: list[str] = field(default_factory=list)


class BuildDriver(Protocol):
    """Protocol for building images.

    Variants: kaniko, buildpacks, buildkit, docker_build,
    gcp_cloud_build, aws_codebuild, azure_pipelines.
    """

    def build(
        self,
        spec: BuildSpec,
        repo: str,
        tag: str,
    ) -> BuildResult: ...

    def cancel(self, build_id: str) -> None: ...
