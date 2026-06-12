"""Add SCM routing fields to WorkflowWebhook (#863).

Adds three nullable/blank columns:
  organization  — FK to astrolift_identity.Organization; scopes the
                  trigger so push-handler lookups stay O(rows per org).
  scm_repo      — "owner/repo" exact match filter; blank = any repo.
  branch_pattern — fnmatch glob against pushed branch; blank = any branch.

Also adds a (organization, enabled) composite index to keep the per-org
lookup cheap when the org has many webhooks.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_agents", "0005_agent_environment_spec_and_extensions"),
        ("astrolift_identity", "0017_userpreferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowwebhook",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workflow_webhooks",
                to="astrolift_identity.organization",
                help_text=(
                    "Org that owns this trigger. Required for SCM routing; "
                    "rows without an org are not matched by the SCM handler."
                ),
            ),
        ),
        migrations.AddField(
            model_name="workflowwebhook",
            name="scm_repo",
            field=models.CharField(
                blank=True,
                default="",
                max_length=512,
                help_text=(
                    "SCM repo full name (owner/repo) this trigger matches. "
                    "Blank matches any repo delivered to the org's webhook."
                ),
            ),
        ),
        migrations.AddField(
            model_name="workflowwebhook",
            name="branch_pattern",
            field=models.CharField(
                blank=True,
                default="",
                max_length=256,
                help_text=(
                    "Glob pattern matched against the pushed branch name (fnmatch). "
                    "Blank matches any branch. Examples: 'main', 'release/*', '*'."
                ),
            ),
        ),
        migrations.AddIndex(
            model_name="workflowwebhook",
            index=models.Index(
                fields=["organization", "enabled"],
                name="wfwebhook_org_enabled_idx",
            ),
        ),
    ]
