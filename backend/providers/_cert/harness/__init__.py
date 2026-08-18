"""The lifecycle harness: run a manifest cell through the cycle and grade it.

Spec 41 §2 asked for a scripted runner that executes the full cycle against a
target, asserts each step, and runs an orphan scan after teardown. Nothing
implemented it, so epic #983's AWS defects were all found by hand and step 7,
REPRODUCE, has never run for any cell on any cloud. This is that runner.

    from _cert.campaign import Campaign
    from _cert.harness import Cell, LifecycleRunner
    from _cert.orphans import aws as aws_orphans

    cell = Cell.load("verification/manifests/happy-path/aws.toml")
    runner = LifecycleRunner(
        platform=CliPlatformClient(project_id=..., source_repo=...),
        scan=lambda: aws_orphans.scan(LiveAwsInventory(region="us-west-2"), Campaign("cert2026q3")),
        update_image_ref="docker.io/calliopeai/astrolift-sample-api:main-<sha7>",
    )
    result = runner.run_cell(cell)

``python -m _cert.harness --help`` is the unattended entry point.
"""

from _cert.harness.cell import Binding, Cell, UnrunnableCell
from _cert.harness.fingerprint import Difference, Fingerprint, compare
from _cert.harness.model import (
    CYCLE_STEPS,
    AssertionFailed,
    CellResult,
    CycleResult,
    Grid,
    Outcome,
    Step,
    StepResult,
)
from _cert.harness.platform import (
    AppObservation,
    CliPlatformClient,
    DeploymentObservation,
    ManagedServiceObservation,
    PlatformClient,
    PlatformError,
    ProbeResult,
    WorkloadObservation,
)
from _cert.harness.runner import LifecycleRunner, Poll

__all__ = [
    "CYCLE_STEPS",
    "AppObservation",
    "AssertionFailed",
    "Binding",
    "Cell",
    "CellResult",
    "CliPlatformClient",
    "CycleResult",
    "DeploymentObservation",
    "Difference",
    "Fingerprint",
    "Grid",
    "LifecycleRunner",
    "ManagedServiceObservation",
    "Outcome",
    "PlatformClient",
    "PlatformError",
    "Poll",
    "ProbeResult",
    "Step",
    "StepResult",
    "UnrunnableCell",
    "WorkloadObservation",
    "compare",
]
