# Environment specs and agent boxes gained an owning team/project in 0036
# (#1866), where null means org-shared for a spec and org-level for a box.
# Every existing row is null, so derive an owner only where one is provable:
#
# * A spec whose slug is the slug of exactly one live agent workload in its
#   org is that agent's canonical spec (manifest sync and agent import both
#   create it under the agent's slug), so it takes that agent app's project
#   and team. A slug two apps' agents share stays org-shared: both agents
#   run with it today, and giving it to either would stop the other.
# * A box ensured from an agent already belongs to the agent's app through
#   ``agent_definition`` and records nothing. A box ensured from a spec
#   alone takes the spec's owner, as ensure now stamps it.
#
# Owners are taken only when they are live and in the row's own org; a
# soft-deleted or foreign parent never becomes the owner.

from django.db import migrations


def _live_in_org(row, organization_id) -> bool:
    return row is not None and row.deleted_at is None and row.organization_id == organization_id


def backfill_owners(apps, schema_editor):
    AgentBox = apps.get_model("astrolift_agents", "AgentBox")
    AgentEnvironmentSpec = apps.get_model("astrolift_agents", "AgentEnvironmentSpec")
    Workload = apps.get_model("astrolift_registry", "Workload")

    specs = list(AgentEnvironmentSpec.objects.filter(team_id__isnull=True, project_id__isnull=True))
    agents_by_slug: dict[tuple[int, str], set[int]] = {}
    apps_by_pk = {}
    for workload in Workload.objects.filter(
        kind="agent",
        deleted_at__isnull=True,
        registered_app__deleted_at__isnull=True,
        slug__in={spec.slug for spec in specs},
    ).select_related("registered_app__team", "registered_app__project"):
        app = workload.registered_app
        agents_by_slug.setdefault((app.organization_id, workload.slug), set()).add(app.pk)
        apps_by_pk[app.pk] = app

    owned = []
    for spec in specs:
        app_ids = agents_by_slug.get((spec.organization_id, spec.slug), set())
        if len(app_ids) != 1:
            continue
        app = apps_by_pk[next(iter(app_ids))]
        team = app.team if _live_in_org(app.team, spec.organization_id) else None
        project = app.project if _live_in_org(app.project, spec.organization_id) else None
        if project is not None and project.team_id != getattr(team, "pk", None):
            project = None
        if team is None:
            continue
        spec.team_id = team.pk
        spec.project_id = getattr(project, "pk", None)
        owned.append(spec)
    if owned:
        AgentEnvironmentSpec.objects.bulk_update(owned, ["team_id", "project_id"])

    boxes = []
    for box in AgentBox.objects.filter(
        agent_definition_id__isnull=True,
        environment_spec__isnull=False,
        team_id__isnull=True,
        project_id__isnull=True,
    ).select_related("environment_spec"):
        spec = box.environment_spec
        if spec.organization_id != box.organization_id or spec.team_id is None:
            continue
        box.team_id = spec.team_id
        box.project_id = spec.project_id
        boxes.append(box)
    if boxes:
        AgentBox.objects.bulk_update(boxes, ["team_id", "project_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0038_agent_spec_and_box_owner"),
        ("astrolift_registry", "0041_populate_hostname_claims"),
    ]

    operations = [
        migrations.RunPython(backfill_owners, migrations.RunPython.noop),
    ]
