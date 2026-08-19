"""The auditable record of an explicitly authorized resource adoption (#1365).

Adoption is the one operation that writes the platform's identity onto a cloud
resource the platform cannot already prove it owns. #1365 requires it to leave
a record, and requires that record to say *what the resource's prior ownership
markers were* -- because "it carried no Astrolift tag" and "it carried another
managed service's tag" are very different things for somebody to have approved,
and after the envelope is merged the evidence is gone from the cloud.

So the markers are snapshotted before the write and stored here verbatim. The
row is the answer to "who took this over, when, from what, and why", and it is
written whether the cloud call succeeds or fails: an attempted adoption that
errored halfway is exactly the state an operator later needs to see.

``core.events`` also gets an event for the activity feed and webhook fan-out.
That is the notification; this is the record. The event log is retention-bound
and shaped for streaming, while an adoption has to stay queryable next to the
service it affected for as long as that service exists.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ManagedResourceAdoption(BaseCoreModel):
    """One authorized takeover of an existing cloud resource."""

    class Classification(models.TextChoices):
        """What the resource looked like before adoption touched it.

        Mirrors ``_sdk.azure_adoption.AzureAdoptionClassification``; a
        contract test pins the two together so a new provider-side case
        cannot land as an unrenderable empty string here.
        """

        UNMANAGED = "unmanaged", "No Astrolift marker"
        UNSTAMPED = "unstamped", "Platform-created, no managed-service id"
        FOREIGN_OWNER = "foreign_owner", "Belonged to another managed service"
        ALREADY_OWNED = "already_owned", "Already carried this identity"

    class Status(models.TextChoices):
        ADOPTED = "adopted"
        REFUSED = "refused"
        FAILED = "failed"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="managed_resource_adoptions",
        on_delete=models.CASCADE,
    )
    # PROTECT, not CASCADE: the record has to outlive routine churn on the
    # service row. A hard delete of the service would take the evidence of the
    # takeover with it, and the service is soft-deleted in normal operation
    # anyway, which leaves this row addressable.
    managed_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        related_name="resource_adoptions",
        on_delete=models.PROTECT,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="managed_resource_adoptions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Denormalized so the record still names a person after the account is
    # deleted and the FK nulls out. An audit row that says "somebody" is not
    # an audit row.
    actor_display = models.CharField(max_length=254, blank=True, default="")

    cloud = models.CharField(max_length=32, default="azure")
    resource_id = models.TextField()
    """Fully-qualified cloud resource identifier -- on Azure the ARM id. Free
    text rather than a parsed set of columns because the next cloud's
    identifier is not shaped like this one."""

    surface = models.CharField(max_length=64, blank=True, default="")
    """Where the envelope was written: ARM tags, blob-container metadata, or
    file-share metadata. Two resources of the same kind can differ here, and
    an adoption that stamped the wrong surface is invisible without it."""

    classification = models.CharField(max_length=32, choices=Classification.choices)
    prior_markers = models.JSONField(default=dict, blank=True)
    """Every ``astrolift*`` tag or metadata key the resource carried before the
    write, verbatim. The reason this model exists."""

    prior_managed_by = models.CharField(max_length=128, blank=True, default="")
    prior_managed_service_id = models.CharField(max_length=128, blank=True, default="")
    prior_binding_id = models.CharField(max_length=128, blank=True, default="")
    """Pulled out of ``prior_markers`` for querying -- "has anything ever been
    adopted away from service X" is the question an incident asks first."""

    acknowledged_prior_owner = models.CharField(max_length=128, blank=True, default="")
    """The owner identity the caller named to displace it. Empty for anything
    that was not owned; required, and equal to ``prior_managed_service_id``,
    for a ``foreign_owner`` adoption."""

    reason = models.TextField()
    """Operator justification. Required by the mutation: an adoption without a
    stated reason is a record of a click, not of a decision."""

    stamped_markers = models.JSONField(default=dict, blank=True)
    """The envelope actually written, so the record shows both sides of the
    change without a second cloud read."""

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ADOPTED)
    error = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["prior_managed_service_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.cloud}:{self.resource_id} -> managed_service#{self.managed_service_id} ({self.status})"
