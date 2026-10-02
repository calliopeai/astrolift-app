"""Platform workflow catalogue seed (spec 40 §4).

The v0.1 starter catalogue (§4.1) is authored as workflow-manifest TOML
files bundled under ``workflows/catalogue/*.toml``. This module parses each
via the #973 serializer (:func:`workflows.manifest.parse_workflow_manifest`)
and upserts a null-org, read-only ``WorkflowDefinition`` + its ordered
``WorkflowStage`` rows by ``(organization=None, slug)``.

Idempotent / ``DEFAULT_SCHEDULES``-shaped: re-runnable on every deploy —
matches existing rows on ``(organization=None, slug)`` (definitions) and
``(definition, order)`` (stages), updates them in place, and prunes stale
trailing stages if a definition shrank. Invoked by both the
``seed_workflow_catalogue`` management command and the catalogue data
migration; the model classes are passed in so the migration can use
historical models.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

CATALOGUE_DIR = Path(__file__).resolve().parent / "catalogue"


@dataclasses.dataclass
class SeedResult:
    definitions_created: int = 0
    definitions_updated: int = 0
    stages_upserted: int = 0
    stages_pruned: int = 0

    def summary(self) -> str:
        return (
            f"{self.definitions_created} definition(s) created, "
            f"{self.definitions_updated} updated; "
            f"{self.stages_upserted} stage(s) upserted, "
            f"{self.stages_pruned} pruned."
        )


def _fan_out_columns(fan_out: int | str) -> tuple[int | None, bool]:
    """Map the manifest tri-state to the model's two columns (#969)."""
    if fan_out == "dynamic":
        return None, True
    if isinstance(fan_out, int) and not isinstance(fan_out, bool) and fan_out > 0:
        return fan_out, False
    return None, False


def seed_workflow_catalogue(WorkflowDefinition, WorkflowStage) -> SeedResult:
    """Upsert the bundled catalogue using the given model classes.

    Pass the real models (management command) or historical models
    (data migration). Returns a :class:`SeedResult` summary.
    """
    # Imported here, not at module load, so migration load-time stays light
    # and avoids importing the serializer before the app registry is ready.
    from workflows.manifest import parse_workflow_manifest

    result = SeedResult()

    for toml_path in sorted(CATALOGUE_DIR.glob("*.toml")):
        parsed = parse_workflow_manifest(toml_path.read_text(encoding="utf-8"))
        defn = parsed.definition

        definition, created = WorkflowDefinition.objects.update_or_create(
            organization=None,
            slug=defn.slug,
            defaults={
                "name": defn.name,
                "description": defn.description,
                "pattern_kind": defn.pattern,
                "model_label": "",
                "is_enabled": True,
            },
        )
        if created:
            result.definitions_created += 1
        else:
            result.definitions_updated += 1

        for stage in parsed.stages:
            fan_out_count, fan_out_dynamic = _fan_out_columns(stage.fan_out)
            WorkflowStage.objects.update_or_create(
                definition=definition,
                order=stage.order,
                defaults={
                    "slug": f"{defn.slug}-{stage.order}",
                    "kind": stage.kind,
                    "role": stage.role or "",
                    # Globals carry no concrete agent — bound at configure-time.
                    "agent_definition": None,
                    "skill_refs": list(stage.skills),
                    "on_failure": stage.on_failure,
                    # Historical models used by the original catalogue migration
                    # predate this field; live seeding preserves the authored cap.
                    **(
                        {"max_attempts": stage.max_attempts}
                        if any(field.name == "max_attempts" for field in WorkflowStage._meta.fields)
                        else {}
                    ),
                    "timeout_seconds": stage.timeout,
                    "fan_out_count": fan_out_count,
                    "fan_out_dynamic": fan_out_dynamic,
                    "prompt": stage.prompt or "",
                    "approvers": list(stage.approvers),
                },
            )
            result.stages_upserted += 1

        # Prune any stage left over from a previous, longer version.
        pruned, _ = (
            WorkflowStage.objects.filter(definition=definition).filter(order__gte=len(parsed.stages)).delete()
        )
        result.stages_pruned += pruned

    return result
