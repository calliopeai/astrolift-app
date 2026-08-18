"""A platform that behaves, and can be told to misbehave at one point.

The runner's whole contract is "when the platform does the wrong thing, say
which step, which cell and which assertion". Testing that needs a platform whose
wrongness is precise: one fault at a time, in the place a real defect would
appear, with everything else still working. A mock that raises from every call
would prove only that the runner catches exceptions.

The faults here are the defect shapes the AWS campaign actually produced or
would have: a managed service stuck short of ACTIVE, a binding that never
reached the pod, a rollback that lands on the wrong image, an old revision that
never retired, a deregister that never converges, residue in the cloud, and a
second run that provisions under a different name because the first teardown
released the resource but not its name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from _cert.campaign import Campaign
from _cert.harness.cell import Cell
from _cert.harness.platform import (
    AppObservation,
    DeploymentObservation,
    ManagedServiceObservation,
    PlatformError,
    ProbeResult,
    WorkloadObservation,
)
from _cert.orphans import CloudResource
from _cert.orphans import aws as aws_orphans

CAMPAIGN = Campaign("cert2026q3")

MANIFEST: dict[str, Any] = {
    "name": "cert2026q3-happy-aws",
    "campaign": {"slug": "cert2026q3", "cell": "happy-path/aws", "cloud": "aws"},
    "workloads": [
        {
            "name": "web",
            "is_public": True,
            "replicas": 2,
            "containers": [
                {
                    "name": "app",
                    "is_primary": True,
                    "image_ref": "docker.io/calliopeai/astrolift-sample-api:main-df5ffa3",
                    "healthcheck": {"kind": "http", "value": "/health"},
                }
            ],
        }
    ],
    "managed_services": [
        {"kind": "postgres", "name": "records", "variant": "rds"},
        {"kind": "object_store", "name": "archive", "variant": "s3"},
    ],
}

BASE_IMAGE = "docker.io/calliopeai/astrolift-sample-api:main-df5ffa3"
UPDATE_IMAGE = "docker.io/calliopeai/astrolift-sample-api:main-abc1234"


def happy_cell() -> Cell:
    return Cell.from_toml(MANIFEST)


@dataclass
class FakeInventory:
    """Cloud state, as the orphan scanner would see it."""

    rds: list[CloudResource] = field(default_factory=list)
    s3: list[CloudResource] = field(default_factory=list)

    def rds_instances(self):
        return list(self.rds)

    def s3_buckets(self):
        return list(self.s3)

    def rds_clusters(self):
        return []

    def elasticache_clusters(self):
        return []

    def elasticache_replication_groups(self):
        return []

    def elasticache_serverless_caches(self):
        return []

    def sqs_queues(self):
        return []

    def sns_topics(self):
        return []

    def iam_roles(self):
        return []

    def iam_policies(self):
        return []


@dataclass
class FakePlatform:
    """A control plane that works, unless ``fault`` says otherwise.

    Cloud resources are created and destroyed alongside the platform-side rows,
    so the injected orphan scan sees a coherent world rather than a canned
    answer.
    """

    fault: str = ""
    inventory: FakeInventory = field(default_factory=FakeInventory)

    app: AppObservation | None = None
    services: list[ManagedServiceObservation] = field(default_factory=list)
    running_image: str = ""
    deployments: list[DeploymentObservation] = field(default_factory=list)
    runs: int = 0
    """How many times BUILDOUT has been entered. Lets a fault differ between the
    first and second pass, which is what REPRODUCE is for."""

    # -- helpers -------------------------------------------------------------

    def _suffix(self) -> str:
        """The second run's resource-name suffix under the leaked-name fault.

        This is the non-idempotent-teardown signature: the resource was deleted
        but its name was not released, so the driver derives a new one.
        """
        return "-2" if self.fault == "leaked_name" and self.runs > 1 else ""

    def scan(self):
        return aws_orphans.scan(self.inventory, CAMPAIGN)

    # -- PlatformClient ------------------------------------------------------

    def register_app(self, cell: Cell) -> None:
        self.runs += 1
        if self.fault == "register_fails":
            raise PlatformError("registerApp refused: NOT_FOUND project not found")
        status = "failed" if self.fault == "app_never_ready" else "ready"
        self.app = AppObservation(slug=cell.app_name, provisioning_status=status, url="https://app.example.test")

    def get_app(self, slug: str) -> AppObservation | None:
        return self.app if self.app and self.app.slug == slug else None

    def provision_managed_service(self, slug: str, environment: str, kind: str, name: str, variant: str) -> None:
        status = "provisioning" if self.fault == "service_stuck" else "active"
        self.services.append(ManagedServiceObservation(name=name, kind=kind, variant=variant, status=status))
        identifier = f"astrolift-conflict-{slug}-{name}{self._suffix()}"
        resource = CloudResource(identifier=identifier, location="us-west-2", tags={"astrolift.io/app": slug})
        (self.inventory.rds if kind == "postgres" else self.inventory.s3).append(resource)

    def list_managed_services(self, slug: str, environment: str):
        if self.app is None:
            raise PlatformError(f"app {slug!r} not found")
        if self.fault == "service_stuck":
            # Terminal but not ACTIVE: the shape a driver leaves when provision
            # half-succeeds, which reads as "done" to a poller checking only
            # terminality.
            return [
                ManagedServiceObservation(s.name, s.kind, s.variant, "failed", "quota exceeded") for s in self.services
            ]
        return list(self.services)

    def list_workloads(self, slug: str):
        if self.fault == "old_revision_lingers" and self.running_image == UPDATE_IMAGE:
            images = (BASE_IMAGE, UPDATE_IMAGE)
        else:
            images = (self.running_image,) if self.running_image else ()
        ready = 0 if self.fault == "pods_not_ready" else 2
        return [WorkloadObservation(name="web", desired_replicas=2, ready_replicas=ready, images=images)]

    def deploy(self, slug: str, environment: str, image_tag: str) -> DeploymentObservation:
        succeeded = not (self.fault == "update_fails" and image_tag != BASE_IMAGE.rpartition(":")[2])
        status = "succeeded" if succeeded else "failed"
        if succeeded:
            self.running_image = BASE_IMAGE if image_tag == BASE_IMAGE.rpartition(":")[2] else UPDATE_IMAGE
        deployment = DeploymentObservation(
            id=f"dep-{len(self.deployments) + 1}",
            status=status,
            image_tag=image_tag,
            environment_name=environment,
        )
        self.deployments.append(deployment)
        if self.app is not None:
            self.app = AppObservation(
                slug=self.app.slug,
                provisioning_status=self.app.provisioning_status,
                url=self.app.url,
                latest_deployment=deployment,
            )
        return deployment

    def rollback(self, slug: str, environment: str) -> DeploymentObservation:
        # Under the fault the rollback reports success but lands on the image it
        # was already running -- a rollback that no-ops is worse than one that
        # errors, because nothing surfaces it.
        tag = UPDATE_IMAGE.rpartition(":")[2] if self.fault == "rollback_noop" else BASE_IMAGE.rpartition(":")[2]
        self.running_image = UPDATE_IMAGE if self.fault == "rollback_noop" else BASE_IMAGE
        return DeploymentObservation(id="dep-rollback", status="succeeded", image_tag=tag)

    def deregister(self, slug: str) -> None:
        if self.fault == "teardown_never_converges":
            return
        self.app = None
        self.services = []
        self.running_image = ""
        self.inventory.rds = []
        if self.fault != "orphan_left_behind":
            # Under that fault the bucket survives the app that owned it, which
            # is the residue VERIFY-CLEAN exists to find. Under `leaked_name`
            # everything is deleted and only the *name* is never released --
            # invisible until the next run has to pick one.
            self.inventory.s3 = []

    def probe(self, url: str, path: str) -> ProbeResult:
        if self.fault == "binding_missing" and path == "/debug":
            return ProbeResult(status_code=200, body='{"bindings": ["records"]}', tls_verified=True)
        if self.fault == "selftest_fails" and path == "/selftest":
            return ProbeResult(status_code=500, body="postgres: connection refused", tls_verified=True)
        if self.fault == "no_tls":
            return ProbeResult(status_code=200, body="ok", tls_verified=False)
        body = '{"bindings": ["records", "archive"]}' if path == "/debug" else "ok"
        return ProbeResult(status_code=200, body=body, tls_verified=True)
