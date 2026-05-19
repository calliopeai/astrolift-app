"""Add manual-preview support to ``PreviewEnvironment`` (#751).

Adds two model-level pieces so operators can spin up a preview from a
branch without a PR:

* ``is_manual`` BooleanField (default False) — distinguishes manual
  previews from PR-webhook-triggered ones so downstream callers (GC,
  PR comment renderer, audit) can branch on intent.
* ``pr_number`` becomes nullable — manual previews have no PR and we
  refuse to fabricate one.  Postgres treats NULLs as distinct in
  unique constraints, so existing auto previews continue to enforce
  ``(registered_app, pr_number)`` uniqueness on non-NULL pr_numbers.

The original ``preview_pr_unique_active_per_app`` constraint is
swapped for a pair: one keyed on ``(registered_app, pr_number)`` for
non-manual previews (matches the prior behavior — no migration of
existing rows needed), and a new one on ``(registered_app, branch)``
for manual previews so re-running ``createPreviewEnvironment`` on the
same branch collides on the index rather than racing two rows in.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0020_deployment_rendered_manifest_snapshot"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="previewenvironment",
            name="preview_pr_unique_active_per_app",
        ),
        migrations.AlterField(
            model_name="previewenvironment",
            name="pr_number",
            field=models.PositiveIntegerField(
                null=True,
                blank=True,
                help_text=(
                    "PR number that opened this preview. Null when the "
                    "preview was created via ``createPreviewEnvironment`` "
                    "(manual branch spin-up) rather than auto-triggered "
                    "from a PR webhook."
                ),
            ),
        ),
        migrations.AddField(
            model_name="previewenvironment",
            name="is_manual",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True when this preview was created via the "
                    "``createPreviewEnvironment`` mutation rather than "
                    "auto-triggered from a PR webhook. Manual previews "
                    "have a null ``pr_number`` and are keyed for "
                    "uniqueness on ``branch`` instead."
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name="previewenvironment",
            constraint=models.UniqueConstraint(
                fields=["registered_app", "pr_number"],
                condition=models.Q(deleted_at__isnull=True) & models.Q(is_manual=False),
                name="preview_pr_unique_active_per_app",
            ),
        ),
        migrations.AddConstraint(
            model_name="previewenvironment",
            constraint=models.UniqueConstraint(
                fields=["registered_app", "branch"],
                condition=models.Q(deleted_at__isnull=True) & models.Q(is_manual=True),
                name="preview_manual_branch_unique_active_per_app",
            ),
        ),
    ]
