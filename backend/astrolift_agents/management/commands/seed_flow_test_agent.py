"""
``manage.py seed_flow_test_agent`` — seed everything needed to dispatch a
minimal "flow-test" agent as a Kubernetes Job on an already-registered
tenant cluster, via the server-side spawn path.

This wires up, end-to-end, the records that
``astrolift_workflows.activities.workflow_stage_activities.dispatch_agent_for_stage``
reads when ``runWorkflowDefinition`` runs an agent ``WorkflowDefinition``:

* a :class:`~astrolift_registry.models.RegisteredApp` (under the org, via a
  Team + Project — ``RegisteredApp.team`` is non-null) to host the agent;
* an agent-kind :class:`~astrolift_registry.models.Workload` plus its
  primary :class:`~astrolift_registry.models.Container` carrying the
  ``image_ref`` the ``K8sJobSpawner`` renders the Job from;
* a single-pattern :class:`~workflows.models.WorkflowDefinition`
  (``is_enabled=True``) whose one ordered
  :class:`~workflows.models.WorkflowStage` is ``kind=agent_dispatch`` and
  points ``agent_definition`` at the Workload;
* an ACTIVE :class:`~astrolift_agents.models.DispatcherInstance`
  (``backend=k8s_job``) bound to the tenant cluster — this is what
  ``_resolve_dispatcher_sync`` picks (oldest ACTIVE for the org) and what
  ``get_spawner(dispatcher.backend, cluster=dispatcher.tenant_cluster)``
  spawns through.

The Organization and TenantCluster must already exist (resolved by slug);
everything else is created or updated in place. Idempotent: every record is
keyed on its slug (the dispatcher / workflow / app / workload / container)
so repeat invocations are cheap and never pile up duplicates. Wrapped in a
single transaction.

Note: when no ACTIVE dispatcher exists for the org,
``dispatch_agent_for_stage`` logs a warning and leaves the AgentRun
``PENDING`` rather than failing the run (registration may be in flight).
Seeding the DispatcherInstance ACTIVE here is what makes the spawn actually
fire.

Example (run on a live container)::

    manage.py seed_flow_test_agent \\
        --org-slug steadymd \\
        --cluster-slug astrolift-eks \\
        --image 464386617157.dkr.ecr.us-west-2.amazonaws.com/astrolift/agent-claude:latest

Then dispatch the seeded workflow::

    runWorkflowDefinition(workflowSlug="flow-test-run")
"""

from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from astrolift_agents.models import DispatcherInstance
from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import Container, RegisteredApp, Workload
from workflows.models import WorkflowDefinition, WorkflowStage

# A valid minimal state machine. Agent stage workflows don't drive the
# ``states`` array (the stage executor walks ``WorkflowStage`` rows), but
# ``WorkflowDefinition.validate_definition`` still requires exactly one
# initial state — so we mirror what the createWorkflowDefinition mutation
# accepts and the stage-executor tests use.
_MINIMAL_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
]
# WorkflowDefinition's uniqueness is (slug, model_label). The value is
# free-form; matching the run mutation's WorkflowInstance content type keeps
# the row self-consistent.
_MODEL_LABEL = "workflows.workflowdefinition"

# Default container command for the flow-test agent. The point of the
# flow-test is to prove the dispatch chain end-to-end (workflow → stage
# executor → dispatcher → spawned Job → terminal status), NOT to run a
# real agent. This command exits 0 immediately with a marker line so the
# Job reaches ``Complete`` without needing an API key, a Brief, or any
# agent runtime wiring. The agent-claude base image ships ``/bin/sh``, so
# this runs on the published runtime as well as on a bespoke image.
_DEFAULT_COMMAND = [
    "/bin/sh",
    "-c",
    "echo 'flow-test reached the agent runtime'; exit 0",
]


def _resolve_org(value: str) -> Organization:
    """Resolve an Organization by slug, falling back to guid."""
    org = Organization.objects.filter(slug=value, deleted_at__isnull=True).first()
    if org is None:
        try:
            org = Organization.objects.filter(guid=value, deleted_at__isnull=True).first()
        except (ValidationError, ValueError):
            org = None
    if org is None:
        raise CommandError(f"organization not found: {value!r}")
    return org


def _resolve_cluster(value: str) -> TenantCluster:
    """Resolve a TenantCluster by slug, falling back to guid."""
    cluster = TenantCluster.objects.filter(slug=value, deleted_at__isnull=True).first()
    if cluster is None:
        try:
            cluster = TenantCluster.objects.filter(guid=value, deleted_at__isnull=True).first()
        except (ValidationError, ValueError):
            cluster = None
    if cluster is None:
        raise CommandError(f"tenant cluster not found: {value!r}")
    return cluster


class Command(BaseCommand):
    help = (
        "Seed a minimal flow-test agent (app, workload, workflow + agent_dispatch "
        "stage, and an ACTIVE k8s_job dispatcher) for the server-side spawn path. "
        "Idempotent on slugs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--org-slug", default="steadymd", help="Organization slug or guid.")
        parser.add_argument(
            "--cluster-slug",
            default="astrolift-eks",
            help="Registered TenantCluster slug or guid to dispatch onto.",
        )
        parser.add_argument(
            "--image",
            required=True,
            help="Agent container image ref (the Job's primary container image).",
        )
        parser.add_argument(
            "--command",
            default=json.dumps(_DEFAULT_COMMAND),
            help=(
                "Primary container command as a JSON list string (parsed with "
                "json.loads), set as Container.command so the spawned Job runs "
                "it instead of the image entrypoint. Defaults to a shell command "
                "that prints a marker line and exits 0, so the flow-test Job "
                "completes cleanly without an API key or Brief. Pass '[]' to fall "
                "back to the image entrypoint."
            ),
        )
        parser.add_argument("--app-slug", default="flow-test", help="RegisteredApp slug.")
        parser.add_argument(
            "--workflow-slug",
            default="flow-test-run",
            help="WorkflowDefinition slug (the runWorkflowDefinition target).",
        )
        parser.add_argument(
            "--dispatcher-slug",
            default="k8s-astrolift-eks",
            help="DispatcherInstance slug (unique).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        org = _resolve_org(options["org_slug"])
        cluster = _resolve_cluster(options["cluster_slug"])

        image = options["image"].strip()
        if not image:
            raise CommandError("--image must not be empty")

        # Parse --command as a JSON list. A non-list (e.g. a bare string or
        # an object) is a usage error — the container command must be argv.
        try:
            command = json.loads(options["command"])
        except json.JSONDecodeError as exc:
            raise CommandError(f"--command is not valid JSON: {exc}") from exc
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise CommandError("--command must be a JSON list of strings, e.g. '[\"/bin/sh\",\"-c\",\"...\"]'")

        app_slug = options["app_slug"]
        workflow_slug = options["workflow_slug"]
        dispatcher_slug = options["dispatcher_slug"]

        # --- Team + Project ------------------------------------------------
        # RegisteredApp.team is a non-null FK, so the app needs a Team. The
        # Project FK is nullable, but we seed one so the app slots into the
        # nav tree exactly like a real registration.
        team, _ = Team.objects.get_or_create(
            organization=org,
            slug=f"{app_slug}-team",
            defaults={"name": "Flow Test"},
        )
        project, _ = Project.objects.get_or_create(
            team=team,
            slug=f"{app_slug}-project",
            defaults={"organization": org, "name": "Flow Test"},
        )

        # --- RegisteredApp -------------------------------------------------
        # DIRECT_UPLOAD: there is no source repo for a flow-test agent — the
        # image is supplied directly. ``all_objects`` so an accidentally
        # soft-deleted row is restored rather than tripping the unique
        # (organization, slug) active constraint.
        app, app_created = RegisteredApp.all_objects.update_or_create(
            organization=org,
            slug=app_slug,
            defaults={
                "name": "Flow Test",
                "team": team,
                "project": project,
                "source_kind": RegisteredApp.SourceKind.DIRECT_UPLOAD,
                "build_mode": RegisteredApp.BuildMode.NONE,
                "default_tenant_cluster": cluster,
                "provisioning_status": RegisteredApp.ProvisioningStatus.READY,
                "is_active": True,
                "deleted_at": None,
                "deleted_by": None,
            },
        )

        # --- Workload (agent) ----------------------------------------------
        workload, workload_created = Workload.all_objects.update_or_create(
            registered_app=app,
            slug=f"{app_slug}-agent",
            defaults={
                "name": "Flow Test Agent",
                "kind": Workload.Kind.AGENT,
                "deleted_at": None,
                "deleted_by": None,
            },
        )

        # --- Primary Container ---------------------------------------------
        # The K8sJobSpawner renders the Job from the workload's primary
        # container's ``image_ref``. Exactly one primary container per
        # workload (DB constraint), keyed here on (workload, name).
        container, container_created = Container.all_objects.update_or_create(
            workload=workload,
            name="agent",
            defaults={
                "is_primary": True,
                "image_ref": image,
                # The spawner sets the Job container's ``command`` only when
                # this is non-empty (else the image entrypoint runs). The
                # default exits 0 cleanly so the flow-test Job completes.
                "command": command,
                "deleted_at": None,
                "deleted_by": None,
            },
        )

        # --- WorkflowDefinition (single, enabled) --------------------------
        # Keyed on (slug, model_label) to match the unique constraint.
        workflow, workflow_created = WorkflowDefinition.objects.update_or_create(
            slug=workflow_slug,
            model_label=_MODEL_LABEL,
            defaults={
                "name": "Flow Test Run",
                "description": "Minimal single-stage agent dispatch for flow testing.",
                "pattern_kind": WorkflowDefinition.PatternKind.SINGLE,
                "states": _MINIMAL_STATES,
                "transitions": [],
                "is_enabled": True,
            },
        )

        # --- WorkflowStage (agent_dispatch, order 0) -----------------------
        # Explicit slug: WorkflowStage extends a slugged base whose blank
        # slug defaults to the literal "none", which collides on the second
        # unslugged row. Keyed on (definition, order) to match the unique
        # constraint and stay idempotent.
        stage, stage_created = WorkflowStage.objects.update_or_create(
            definition=workflow,
            order=0,
            defaults={
                "slug": f"{workflow_slug}-s0",
                "kind": WorkflowStage.StageKind.AGENT_DISPATCH,
                "agent_definition": workload,
                "on_failure": WorkflowStage.OnFailure.FAIL,
                "timeout_seconds": 600,
                "skill_refs": [],
            },
        )

        # --- DispatcherInstance (ACTIVE k8s_job) ---------------------------
        # _resolve_dispatcher_sync picks the oldest ACTIVE dispatcher for the
        # org; the spawner is selected by ``backend`` and dispatches onto
        # ``tenant_cluster``. ``cloud=aws`` because astrolift-eks is EKS; the
        # endpoint is informational metadata for this server-side path (the
        # spawner talks to the cluster apiserver, not this URL).
        dispatcher, dispatcher_created = DispatcherInstance.all_objects.update_or_create(
            slug=dispatcher_slug,
            defaults={
                "organization": org,
                "name": "Flow Test k8s_job Dispatcher",
                "endpoint": f"https://dispatch.{cluster.slug}.internal",
                "cloud": DispatcherInstance.Cloud.AWS,
                "region": cluster.region or "",
                "backend": DispatcherInstance.Backend.K8S_JOB,
                "status": DispatcherInstance.Status.ACTIVE,
                "tenant_cluster": cluster,
                "deleted_at": None,
                "deleted_by": None,
            },
        )

        # --- Summary -------------------------------------------------------
        def _verb(created: bool) -> str:
            return "created" if created else "updated"

        self.stdout.write(self.style.SUCCESS("Seeded flow-test agent dispatch chain:"))
        self.stdout.write(f"  organization      {org.slug} (pk={org.pk}) [resolved]")
        self.stdout.write(f"  tenant_cluster    {cluster.slug} (pk={cluster.pk}) [resolved]")
        self.stdout.write(f"  team              {team.slug} (pk={team.pk})")
        self.stdout.write(f"  project           {project.slug} (pk={project.pk})")
        self.stdout.write(
            f"  registered_app    {app.slug} (pk={app.pk}) [{_verb(app_created)}]"
        )
        self.stdout.write(
            f"  workload          {workload.slug} (pk={workload.pk}, "
            f"kind={workload.kind}) [{_verb(workload_created)}]"
        )
        self.stdout.write(
            f"  container         {container.name} (pk={container.pk}, "
            f"is_primary={container.is_primary}, image_ref={container.image_ref}, "
            f"command={container.command}) [{_verb(container_created)}]"
        )
        self.stdout.write(
            f"  workflow_def      {workflow.slug} (pk={workflow.pk}, "
            f"pattern={workflow.pattern_kind}, is_enabled={workflow.is_enabled}) "
            f"[{_verb(workflow_created)}]"
        )
        self.stdout.write(
            f"  workflow_stage    {stage.slug} (pk={stage.pk}, order={stage.order}, "
            f"kind={stage.kind}, on_failure={stage.on_failure}, "
            f"timeout_seconds={stage.timeout_seconds}) [{_verb(stage_created)}]"
        )
        self.stdout.write(
            f"  dispatcher        {dispatcher.slug} (pk={dispatcher.pk}, "
            f"backend={dispatcher.backend}, status={dispatcher.status}, "
            f"cluster={cluster.slug}) [{_verb(dispatcher_created)}]"
        )
        self.stdout.write("")
        self.stdout.write(f'RUN: runWorkflowDefinition(workflowSlug="{workflow_slug}")')
