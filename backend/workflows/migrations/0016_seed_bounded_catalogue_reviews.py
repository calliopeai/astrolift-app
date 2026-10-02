"""Enable explicit finite review returns only on the two platform templates.

Organization copies and historical runs remain unchanged. Authors review their
own return targets and budgets before starting a new run.
"""

from django.db import migrations


def seed_review_edges(apps, schema_editor):
    Definition = apps.get_model("workflows", "WorkflowDefinition")
    Stage = apps.get_model("workflows", "WorkflowStage")
    for definition in Definition.objects.filter(
        organization__isnull=True, slug__in=("rasd", "moderate")
    ):
        producer = Stage.objects.filter(
            definition=definition, order=0, deleted_at__isnull=True
        ).first()
        gate = Stage.objects.filter(
            definition=definition, order=2, kind="human_gate", deleted_at__isnull=True
        ).first()
        if producer is None or gate is None or gate.back_edge:
            continue
        if not producer.output_key:
            producer.output_key = "stage_0"
            producer.save(update_fields=["output_key"])
        if not gate.output_key:
            gate.output_key = "review"
        gate.back_edge = {
            "to": producer.output_key,
            "when": "gate_rejected",
            "max_rounds": 3,
            "on_exhausted": "escalate",
        }
        gate.save(update_fields=["output_key", "back_edge"])


class Migration(migrations.Migration):
    dependencies = [("workflows", "0015_historicalworkflowstage_back_edge_and_more")]
    operations = [migrations.RunPython(seed_review_edges, migrations.RunPython.noop)]
