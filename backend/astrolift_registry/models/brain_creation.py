"""One request to create a semantic layer (#2424): ``createBrain`` and the
``BrainCreateWorkflow`` steps that carry it out.

The row is the progress a client polls (``astroliftBrainCreation``) and the
memory that makes each step idempotent: the repository it found or made, the
scaffold commit, the registered app and the first deployment. At most one
creation per organization and slug is in flight; a retry joins it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class BrainCreation(BaseCoreModel):
    class State(models.TextChoices):
        PENDING = "pending"
        REPO_READY = "repo_ready"
        SCAFFOLDED = "scaffolded"
        REGISTERED = "registered"
        DEPLOYING = "deploying"
        READY = "ready"
        FAILED = "failed"

    IN_FLIGHT = (
        State.PENDING,
        State.REPO_READY,
        State.SCAFFOLDED,
        State.REGISTERED,
        State.DEPLOYING,
    )

    organization = models.ForeignKey(
        "astrolift_identity.Organization", related_name="brain_creations", on_delete=models.CASCADE
    )
    project = models.ForeignKey("astrolift_identity.Project", related_name="+", on_delete=models.CASCADE)
    team = models.ForeignKey(
        "astrolift_identity.Team", related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    source_connection = models.ForeignKey(
        "astrolift_scm.SourceConnection", related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    slug = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    repo = models.CharField(max_length=255)
    visibility = models.CharField(max_length=16, default="private")
    template_ref = models.CharField(max_length=200)
    environment_name = models.CharField(max_length=64, default="production")
    deploy = models.BooleanField(default=True)
    # The Mimwell cell and persistence asked for (brain_substrate.Selection.as_dict).
    substrate = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp", related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    branch = models.CharField(max_length=128, blank=True, default="")
    commit_sha = models.CharField(max_length=64, blank=True, default="")
    deployment = models.ForeignKey(
        "astrolift_lifecycle.Deployment", related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    workflow_id = models.CharField(max_length=255, blank=True, default="")
    error_code = models.CharField(max_length=32, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(
                    deleted_at__isnull=True,
                    state__in=["pending", "repo_ready", "scaffolded", "registered", "deploying"],
                ),
                name="brain_creation_one_in_flight_per_slug",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return f"BrainCreation({self.slug} {self.state})"


class BrainSubstrate(BaseCoreModel):
    """The Mimwell substrate selection Astrolift last applied to a brain
    (#2424): its cell (authority, blobs, vectors, graph) and what the host
    attaches (``deploy.persistence``, ``deploy.replicas``, the image). The
    brain's own ``contract.json`` says what it has adopted; this says what
    was asked for, so status can tell the two apart."""

    organization = models.ForeignKey(
        "astrolift_identity.Organization", related_name="+", on_delete=models.CASCADE
    )
    brain = models.ForeignKey(
        "astrolift_registry.RegisteredApp", related_name="brain_substrates", on_delete=models.CASCADE
    )
    authority = models.CharField(max_length=32, default="files")
    blobs = models.CharField(max_length=32, default="fs")
    vectors = models.CharField(max_length=32, default="local")
    graph = models.CharField(max_length=32, default="edges-table")
    persistence = models.JSONField(default=list, blank=True)
    replicas = models.PositiveSmallIntegerField(default=1)
    image = models.CharField(max_length=512, blank=True, default="")
    port = models.PositiveIntegerField(default=8080)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["brain"],
                condition=models.Q(deleted_at__isnull=True),
                name="brain_substrate_one_per_brain",
            ),
        ]

    def selection(self):
        from astrolift_registry.brain_substrate import Selection

        return Selection(
            authority=self.authority,
            blobs=self.blobs,
            vectors=self.vectors,
            graph=self.graph,
            persistence=tuple(self.persistence or ()),
            replicas=self.replicas,
            image=self.image,
            port=self.port,
        )

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return f"BrainSubstrate({self.brain_id} {self.authority}/{self.blobs}/{self.vectors}/{self.graph})"
