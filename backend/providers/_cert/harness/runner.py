"""The lifecycle runner: seven steps, asserted, over one manifest cell.

Spec 41 §2 described this and nothing implemented it, so epic #983 closed on AWS
with its defects found by hand. This is the runner that campaign asked for.

Three properties are load-bearing and each shows up in the control flow rather
than in a comment.

**It drives the platform, not the database or the cloud.** Everything goes
through :class:`~_cert.harness.platform.PlatformClient`, which is the CLI and
the GraphQL API. VERIFY-CLEAN is the sole exception, because "nothing was left
behind" is a claim about the cloud and only the cloud can answer it; that
arrives as an injected scan handle so the runner never imports a cloud SDK.

**Teardown runs even when the cycle has already failed.** A cell that fails at
BUILDOUT has usually created something first, and abandoning it on a metered
account is a worse outcome than the original failure. So steps 1-4 short-circuit
on the first red, and steps 5-6 always run. This is the difference between a
harness you can leave unattended and one you cannot.

**A failure is a sentence, not a traceback.** Every check raises
:class:`~_cert.harness.model.AssertionFailed` with the thing that was supposed
to be true and the thing that was observed instead. Unexpected exceptions are
caught at the step boundary and reported the same shape, because a cell that
dies on an unhandled error still has to say which step it died in.

The runner is idempotent in the sense the campaign needs: every invocation
performs the same sequence and asserts the same things, and BUILDOUT refuses to
start on top of a previous run's residue rather than quietly adopting it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from _cert.harness.fingerprint import Fingerprint, compare
from _cert.harness.model import AssertionFailed as Failed
from _cert.harness.model import (
    CellResult,
    CycleResult,
    Outcome,
    Step,
    StepResult,
)
from _cert.harness.platform import PlatformError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from _cert.harness.cell import Cell
    from _cert.harness.platform import ManagedServiceObservation, PlatformClient
    from _cert.orphans.model import ScanReport

TERMINAL_APP_STATUSES = frozenset({"ready", "failed", "deregistered"})
TORN_DOWN_APP_STATUSES = frozenset({"deregistered"})


@dataclass
class Poll:
    """Wait for a condition, or fail with what was last seen.

    ``sleep`` and ``now`` are injected so the runner's waiting can be exercised
    offline without the tests taking as long as a real provision.
    """

    timeout_s: float
    interval_s: float = 10.0
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic

    def until(self, observe: Callable[[], Any], done: Callable[[Any], bool], *, assertion: str) -> Any:
        deadline = self.now() + self.timeout_s
        observation = observe()
        while not done(observation):
            if self.now() >= deadline:
                raise Failed(assertion, f"still {_describe(observation)} after {self.timeout_s:.0f}s")
            self.sleep(self.interval_s)
            observation = observe()
        return observation


@dataclass
class LifecycleRunner:
    """Runs the cycle for one cell and reports a grid row.

    ``scan`` is the cloud-side census: a zero-argument callable returning a
    :class:`~_cert.orphans.model.ScanReport` for the cell's cloud, normally
    ``lambda: aws_orphans.scan(inventory, CAMPAIGN)``. It is optional only so a
    caller can construct the runner before wiring one; a cycle without it cannot
    pass VERIFY-CLEAN, and says so.
    """

    platform: PlatformClient
    scan: Callable[[], ScanReport] | None = None
    update_image_ref: str = ""
    """The second pinned image the UPDATE step rolls to. No default on purpose:
    campaign blocker B3 was a deploy that failed because a tag moved, and a
    default that does not exist in the registry would turn a fixture problem
    into a platform defect on the report."""

    include_rollback: bool = True
    """UPDATE asserts "rolls cleanly (+ rollback works)". Verifying the rollback
    costs two extra deploys per cell -- forward, back, forward again -- so it is
    a switch, but it defaults on: a rollback path nobody exercises is one of the
    things a certification campaign is for."""

    debug_path: str = "/debug"
    selftest_path: str = "/selftest"
    """The spec 42 fixture's own endpoints. ``/debug`` reports which bindings
    reached the pod; ``/selftest`` opens a real client against each one."""

    provision_poll: Poll = field(default_factory=lambda: Poll(timeout_s=2700, interval_s=15))
    teardown_poll: Poll = field(default_factory=lambda: Poll(timeout_s=2700, interval_s=15))
    """Teardown's floor is not the workflow: ``deregisterAstroliftApp`` waits out
    a roughly five-minute grace window before its first destructive activity, so
    a timeout in minutes reports a platform failure that is really the harness
    leaving early."""

    # -- public -------------------------------------------------------------

    def run_cell(self, cell: Cell, *, reproduce: bool = True) -> CellResult:
        """One cell, both passes, and the comparison between them."""
        first, first_print = self.run_cycle(cell)
        if not reproduce:
            return CellResult(cell=cell.identifier, cloud=cell.cloud, first=first)
        if not first.is_green:
            red = first.first_red
            return CellResult(
                cell=cell.identifier,
                cloud=cell.cloud,
                first=first,
                reproduce=StepResult(
                    step=Step.REPRODUCE,
                    outcome=Outcome.SKIPPED,
                    assertion="the first pass must be green before it is worth reproducing",
                    detail=f"{red.step} was red" if red else "the first pass did not complete",
                ),
            )

        second, second_print = self.run_cycle(cell)
        differences = compare(first_print, second_print)
        if not second.is_green:
            red = second.first_red
            result = StepResult(
                step=Step.REPRODUCE,
                outcome=Outcome.RED,
                assertion="an unattended second run must go green at every step",
                detail=(f"the second pass was red at {red.step}: {red.assertion}" if red else "the second pass failed"),
            )
        elif differences:
            result = StepResult(
                step=Step.REPRODUCE,
                outcome=Outcome.RED,
                assertion="both runs must leave the platform and the cloud in the same state",
                detail="; ".join(str(difference) for difference in differences),
            )
        else:
            result = StepResult(step=Step.REPRODUCE, outcome=Outcome.GREEN)

        return CellResult(
            cell=cell.identifier,
            cloud=cell.cloud,
            first=first,
            second=second,
            reproduce=result,
            differences=tuple(str(difference) for difference in differences),
        )

    def run_cycle(self, cell: Cell) -> tuple[CycleResult, Fingerprint]:
        """Steps 1-6 once, plus the fingerprint the comparison needs."""
        state: dict[str, Any] = {}
        results: list[StepResult] = []

        failed = False
        for step, body in (
            (Step.BUILDOUT, self._buildout),
            (Step.VERIFY_UP, self._verify_up),
            (Step.UPDATE, self._update),
            (Step.VERIFY_UPD, self._verify_upd),
        ):
            if failed:
                results.append(
                    StepResult(step=step, outcome=Outcome.SKIPPED, assertion="an earlier step was red"),
                )
                continue
            result = self._run_step(step, body, cell, state)
            results.append(result)
            failed = not result.is_green

        # Always, even after a red: a half-built cell abandoned on a metered
        # account costs more than the defect that stranded it.
        results.append(self._run_step(Step.TEARDOWN, self._teardown, cell, state))
        results.append(self._run_step(Step.VERIFY_CLEAN, self._verify_clean, cell, state))

        fingerprint = Fingerprint(
            **Fingerprint.up_projection(
                state.get("workloads", []),
                state.get("services", []),
                state.get("up_report"),
            ),
            **Fingerprint.clean_projection(
                bool(state.get("clean_app_visible", True)),
                state.get("clean_report"),
            ),
        )
        return CycleResult(cell=cell.identifier, steps=tuple(results)), fingerprint

    # -- step plumbing -------------------------------------------------------

    def _run_step(
        self,
        step: Step,
        body: Callable[[Cell, dict[str, Any]], Outcome | None],
        cell: Cell,
        state: dict[str, Any],
    ) -> StepResult:
        started = time.monotonic()
        try:
            outcome = body(cell, state) or Outcome.GREEN
        except Failed as failure:
            return StepResult(
                step=step,
                outcome=Outcome.RED,
                assertion=failure.assertion,
                detail=failure.detail,
                duration_s=time.monotonic() - started,
            )
        except PlatformError as exc:
            return StepResult(
                step=step,
                outcome=Outcome.RED,
                assertion=f"the {step} step must complete against the platform API",
                detail=str(exc),
                duration_s=time.monotonic() - started,
            )
        except Exception as exc:
            return StepResult(
                step=step,
                outcome=Outcome.RED,
                assertion=f"the {step} step must not raise",
                detail=f"{type(exc).__name__}: {exc}",
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            step=step,
            outcome=outcome,
            assertion="" if outcome is Outcome.GREEN else "there was nothing for this step to do",
            duration_s=time.monotonic() - started,
        )

    # -- 1. BUILDOUT ---------------------------------------------------------

    def _buildout(self, cell: Cell, state: dict[str, Any]) -> None:
        slug = cell.app_name
        if self.platform.get_app(slug) is not None:
            raise Failed(
                f"no app named {slug!r} may exist before BUILDOUT",
                "one does. A previous run's TEARDOWN did not free the slug, and building on top of "
                "its residue would certify a lifecycle that only works once",
            )

        self.platform.register_app(cell)
        app = self.provision_poll.until(
            lambda: self.platform.get_app(slug),
            lambda observed: observed is not None and observed.provisioning_status.lower() in TERMINAL_APP_STATUSES,
            assertion=f"registering {slug!r} must reach a terminal provisioning status",
        )
        if app.provisioning_status.lower() != "ready":
            raise Failed(
                f"app {slug!r} must be ready after registration",
                f"provisioning status is {app.provisioning_status!r}",
            )

        for binding in cell.bindings:
            self.platform.provision_managed_service(slug, cell.environment, binding.kind, binding.name, binding.variant)

        if cell.bindings:
            services = self.provision_poll.until(
                lambda: list(self.platform.list_managed_services(slug, cell.environment)),
                lambda observed: len(observed) >= len(cell.bindings) and all(s.is_terminal for s in observed),
                assertion="every managed service the manifest declares must reach a terminal status",
            )
            _assert_services_active(services, cell)

        deployment = self.platform.deploy(slug, cell.environment, cell.image_tag)
        if not deployment.succeeded:
            raise Failed(
                f"the first deploy of {slug!r} must succeed",
                f"deployment {deployment.id or '<unknown>'} ended {deployment.status!r}",
            )

    # -- 2. VERIFY-UP --------------------------------------------------------

    def _verify_up(self, cell: Cell, state: dict[str, Any]) -> None:
        slug = cell.app_name
        app = self.platform.get_app(slug)
        if app is None:
            raise Failed(f"app {slug!r} must be visible after BUILDOUT", "the API does not return it")
        if not app.url:
            raise Failed(
                f"app {slug!r} must have a reachable environment URL",
                "no environment reports one, so there is no ingress to verify",
            )

        workloads = list(self.platform.list_workloads(slug))
        state["workloads"] = workloads
        by_name = {workload.name: workload for workload in workloads}
        for declared in cell.workloads:
            observed = by_name.get(declared)
            if observed is None:
                raise Failed(
                    f"workload {declared!r} must be running",
                    f"the platform reports only {sorted(by_name) or 'no workloads'}",
                )
            if observed.ready_replicas < 1:
                raise Failed(
                    f"workload {declared!r} must have at least one ready replica",
                    f"{observed.ready_replicas} of {observed.desired_replicas} are ready",
                )

        health = self.platform.probe(app.url, cell.health_path)
        if not health.ok:
            raise Failed(
                f"the health endpoint {cell.health_path} must answer 2xx over the app's ingress",
                _describe_probe(health),
            )
        if not health.tls_verified:
            raise Failed(
                "the app's ingress must serve verified TLS",
                f"{app.url} was reached without a verified certificate",
            )

        debug = self.platform.probe(app.url, self.debug_path)
        if not debug.ok:
            raise Failed(
                f"the fixture's {self.debug_path} must answer, so bindings in the pod can be checked",
                _describe_probe(debug),
            )
        # Presence, not correctness: /debug says which bindings reached the pod
        # and /selftest below says whether they work. Both matter -- a binding
        # that never arrived and one that arrived broken are different defects
        # in different code.
        missing = [b.name for b in cell.bindings if b.name.lower() not in debug.body.lower()]
        if missing:
            raise Failed(
                "every declared binding must be present in the pod's environment",
                f"{', '.join(missing)} absent from {self.debug_path}",
            )

        services = list(self.platform.list_managed_services(slug, cell.environment))
        state["services"] = services
        _assert_services_active(services, cell)

        selftest = self.platform.probe(app.url, self.selftest_path)
        if not selftest.ok:
            raise Failed(
                "every managed service must be connectable from the pod, not merely ACTIVE",
                _describe_probe(selftest),
            )

        if self.scan is not None:
            # A census, not a defect list: at the cycle's high-water mark this
            # is what the campaign owns, and it is the only place the harness
            # can see cloud-side resource names -- no platform API exposes them.
            state["up_report"] = self.scan()

    # -- 3. UPDATE -----------------------------------------------------------

    def _update(self, cell: Cell, state: dict[str, Any]) -> None:
        if not self.update_image_ref:
            raise Failed(
                "UPDATE needs a second published image to roll to",
                "update_image_ref is unset. There is no default because a tag that is not in the "
                "registry fails the deploy and reports a fixture problem as a platform defect",
            )
        slug = cell.app_name
        update_tag = self.update_image_ref.rpartition(":")[2]
        if update_tag == cell.image_tag:
            raise Failed(
                "the UPDATE image must differ from the one BUILDOUT deployed",
                f"both are {update_tag!r}, so nothing would roll and VERIFY-UPD would pass vacuously",
            )

        rolled = self.platform.deploy(slug, cell.environment, update_tag)
        if not rolled.succeeded:
            raise Failed(
                "redeploying a new image must roll cleanly",
                f"deployment {rolled.id or '<unknown>'} ended {rolled.status!r}",
            )

        if self.include_rollback:
            back = self.platform.rollback(slug, cell.environment)
            if not back.succeeded or back.image_tag != cell.image_tag:
                raise Failed(
                    f"rollback must return the app to {cell.image_tag!r}",
                    f"it ended {back.status!r} on image {back.image_tag!r}",
                )
            forward = self.platform.deploy(slug, cell.environment, update_tag)
            if not forward.succeeded:
                raise Failed(
                    "rolling forward again after a rollback must succeed",
                    f"deployment {forward.id or '<unknown>'} ended {forward.status!r}",
                )

        state["update_tag"] = update_tag

    # -- 4. VERIFY-UPD -------------------------------------------------------

    def _verify_upd(self, cell: Cell, state: dict[str, Any]) -> None:
        slug = cell.app_name
        update_tag = state.get("update_tag", "")
        app = self.platform.get_app(slug)
        if app is None or app.latest_deployment is None:
            raise Failed(f"app {slug!r} must report the deployment UPDATE made", "no deployment record is visible")
        latest = app.latest_deployment
        if latest.image_tag != update_tag or not latest.succeeded:
            raise Failed(
                f"the live revision must be {update_tag!r} and succeeded",
                f"the latest deployment is {latest.image_tag!r} in state {latest.status!r}",
            )

        workloads = list(self.platform.list_workloads(slug))
        state["workloads"] = workloads
        stale = sorted(
            {image for workload in workloads for image in workload.images if not image.endswith(f":{update_tag}")}
        )
        if stale:
            raise Failed(
                "the previous revision must be retired once the new one is live",
                f"still running {', '.join(stale)}",
            )

        health = self.platform.probe(app.url, cell.health_path)
        if not health.ok:
            raise Failed("the app must still be healthy on the new revision", _describe_probe(health))
        selftest = self.platform.probe(app.url, self.selftest_path)
        if not selftest.ok:
            raise Failed(
                "the managed services must still be connectable on the new revision",
                _describe_probe(selftest),
            )

    # -- 5. TEARDOWN ---------------------------------------------------------

    def _teardown(self, cell: Cell, state: dict[str, Any]) -> Outcome | None:
        slug = cell.app_name
        if self.platform.get_app(slug) is None:
            # Nothing was built, or an earlier failure already unwound it.
            # SKIPPED rather than GREEN: the grid is the certification evidence,
            # and a green TEARDOWN on a cycle whose deregister never ran is the
            # grid claiming coverage the run does not have.
            state["clean_app_visible"] = False
            return Outcome.SKIPPED

        self.platform.deregister(slug)
        app = self.teardown_poll.until(
            lambda: self.platform.get_app(slug),
            lambda observed: observed is None or observed.provisioning_status.lower() in TORN_DOWN_APP_STATUSES,
            assertion=f"deregistering {slug!r} must finish",
        )
        state["clean_app_visible"] = app is not None

        remaining = _services_after_teardown(self.platform, slug, cell.environment)
        if remaining:
            raise Failed(
                "no managed service may survive the app that owned it",
                f"still bound: {', '.join(f'{s.name} ({s.status})' for s in remaining)}",
            )

    # -- 6. VERIFY-CLEAN -----------------------------------------------------

    def _verify_clean(self, cell: Cell, state: dict[str, Any]) -> None:
        if self.scan is None:
            raise Failed(
                "VERIFY-CLEAN requires an orphan scanner for the cell's cloud",
                "none was wired, and a cycle with no scan cannot claim the teardown left nothing behind",
            )
        report = self.scan()
        state["clean_report"] = report
        try:
            report.raise_if_dirty()
        except AssertionError as leftovers:
            raise Failed(
                "teardown must leave nothing behind that belongs to the campaign",
                str(leftovers),
            ) from leftovers


# ---- shared assertions --------------------------------------------------------


def _assert_services_active(services: Sequence[ManagedServiceObservation], cell: Cell) -> None:
    by_name = {service.name: service for service in services}
    for binding in cell.bindings:
        observed = by_name.get(binding.name)
        if observed is None:
            raise Failed(
                f"managed service {binding.name!r} ({binding.kind}:{binding.variant}) must exist",
                f"the platform reports only {sorted(by_name) or 'none'}",
            )
        if not observed.is_active:
            raise Failed(
                f"managed service {binding.name!r} must be ACTIVE",
                f"status is {observed.status!r}{f': {observed.status_error}' if observed.status_error else ''}",
            )


def _services_after_teardown(platform: PlatformClient, slug: str, environment: str) -> list[ManagedServiceObservation]:
    """Bindings the API still shows for a torn-down app.

    A refusal here means the app is gone, which is the answer the step wanted;
    anything the API still lists is residue on the platform side of the fence,
    ahead of the cloud-side scan in VERIFY-CLEAN.
    """
    try:
        return list(platform.list_managed_services(slug, environment))
    except PlatformError:
        return []


def _describe(observation: Any) -> str:
    if observation is None:
        return "absent"
    status = getattr(observation, "provisioning_status", None) or getattr(observation, "status", None)
    if status:
        return str(status)
    if isinstance(observation, list):
        return ", ".join(f"{getattr(item, 'name', item)}={getattr(item, 'status', '?')}" for item in observation)
    return str(observation)


def _describe_probe(probe: Any) -> str:
    if probe.error:
        return probe.error
    body = probe.body.strip().replace("\n", " ")
    return f"HTTP {probe.status_code}{f': {body[:200]}' if body else ''}"
