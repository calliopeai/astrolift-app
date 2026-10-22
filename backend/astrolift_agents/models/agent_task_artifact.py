"""Evidence a run attaches to its result (#2488).

A JUnit report, coverage, a conformance report or another named text file,
reported inline with the run's terminal callback and kept with its task. The
callback validates the whole list before anything is written; the limits are
in ``astrolift_agents.services.agent_task_artifacts``.
"""

from django.db import models

from core.models.base import BaseCoreModel


class AgentTaskArtifact(BaseCoreModel):
    class Kind(models.TextChoices):
        JUNIT = "junit"
        COVERAGE = "coverage"
        CONFORMANCE_JSON = "conformance-json"
        OTHER = "other"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask", related_name="artifacts", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=128)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    media_type = models.CharField(max_length=255)
    content = models.TextField()
    # Both describe the UTF-8 bytes of ``content``; the server computes them.
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent_task", "name"], name="agent_artifact_task_name_uniq"),
        ]
