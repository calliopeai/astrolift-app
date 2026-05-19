"""
SourceTriggerToken — per-project trigger credential for non-GitHub hosts.

GitLab pipeline trigger tokens, Bitbucket pipeline API keys, and Gitea
workflow-dispatch tokens are all project-scoped and distinct from the
user OAuth token stored on SourceConnection. This model holds the
encrypted credential so the workflow-dispatch service (#532) can call
the right host endpoint without prompting the operator every time.

Rotation (``rotate_source_trigger_token`` in services/trigger_tokens.py)
soft-deletes the old row and creates a fresh one so the audit trail
survives. The dispatcher always picks the newest active row for a given
(connection, repo) pair.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class SourceTriggerToken(BaseCoreModel):
    class Kind(models.TextChoices):
        GITLAB_PIPELINE_TRIGGER = "gitlab_pipeline_trigger"
        BITBUCKET_PIPELINE_API_KEY = "bitbucket_pipeline_api_key"
        GITEA_WORKFLOW_DISPATCH = "gitea_workflow_dispatch"

    source_connection = models.ForeignKey(
        "astrolift_scm.SourceConnection",
        related_name="trigger_tokens",
        on_delete=models.CASCADE,
    )
    repo_full_name = models.CharField(max_length=255)
    kind = models.CharField(max_length=48, choices=Kind.choices)

    # Encrypted credential. Mirrors the pattern on SourceConnection:
    # backend_kind identifies which backend produced the ciphertext so
    # the decrypt path selects the right key/envelope without a config
    # lookup at call time.
    secret_backend_kind = models.CharField(max_length=32, default="local_fernet")
    secret_ciphertext = models.BinaryField(blank=True, default=b"")

    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["source_connection", "repo_full_name", "-created_at"],
                name="trigger_token_conn_repo_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("source_connection", "repo_full_name", "kind"),
                name="trigger_token_unique_active_per_conn_repo_kind",
            )
        ]
