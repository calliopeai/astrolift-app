"""Give a preview environment a managed-service envelope (#1578).

The complaint this closes: a preview `AppEnvironment` owns zero
`ManagedService` rows and inherits none, and the deploy render only
synthesizes the `astrolift-bindings-<slug>` Secret when that set is
non-empty -- so a preview workload boots with **no DB / redis / queue
envelope at all**.

Composes the three features the issue asked for, all of which now exist:

* the primary/previewed environment link (#1655) says what to inherit from
* `PreviewPolicy` / `resolve_policy` say shared-vs-dedicated per kind
* `SliceCapableDriver.provision_slice` (#1649, CNPG in #1665) carves the
  per-preview slice, so a shared service does not mean shared data

**What is deliberately not used: `shared_plan_for` / `teardown_plan_for`.**
They compute per-preview identifiers -- `<app>_pr_<n>` for a database, a
user, a key prefix -- and the slice contract requires the *driver* to derive
its own identifiers deterministically from `slice_id`. Wiring both would
give two authorities for "what is this preview's database called", and only
one of them creates it. The naming half of that module is superseded; the
policy half is what this calls.

Attachment, not copying. A preview gets a `ManagedServiceAttachment` to the
primary's service plus slice-specific env overrides, rather than its own
`ManagedService` row: a second row would provision a second instance, which
is the DEDICATED policy and not what SHARED_WITH_MAIN means.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from astrolift_lifecycle.preview_managed_services import (
    PreviewPolicy,
    resolve_policy,
)

log = logging.getLogger(__name__)


@dataclass
class PreviewServiceOutcome:
    """What a preview ended up with, and what it did not."""

    attached: list[str] = field(default_factory=list)
    """Service slugs the preview can now reach."""

    sliced: list[str] = field(default_factory=list)
    """Slugs where an isolated slice was carved."""

    shared_unsliced: list[str] = field(default_factory=list)
    """Shared services whose driver cannot slice.

    Counted separately and loudly, because this is the case that silently
    hands a preview the parent's data. Attaching anyway would be the wrong
    default -- see `provision_preview_managed_services`.
    """

    skipped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def env_overrides(self) -> dict[str, Any]:
        return self._overrides

    def __post_init__(self) -> None:
        self._overrides: dict[str, Any] = {}


@dataclass
class PreviewTeardownOutcome:
    """What a teardown dropped, and what it left behind."""

    dropped: list[str] = field(default_factory=list)
    """Slugs whose slice is gone."""

    leaked: list[str] = field(default_factory=list)
    """Slugs whose driver reported a delete that did not land.

    Separate from `errors` because nothing is broken -- something is left
    behind. A leak wants someone to go and drop a database; an error wants
    someone to work out why the call failed.
    """

    no_slice: list[str] = field(default_factory=list)
    """Attached directly to the parent, so there was nothing to drop."""

    errors: dict[str, str] = field(default_factory=dict)


@transaction.atomic
def provision_preview_managed_services(preview_env: Any) -> PreviewServiceOutcome:
    """Attach and slice the primary environment's services onto a preview.

    Returns an outcome rather than raising: a preview whose redis could not
    be sliced should still get its postgres, and the operator needs to see
    which half happened.

    **A shared service whose driver cannot slice is not attached.** That is
    the load-bearing decision here. Attaching it would give the preview a
    binding straight to the parent instance with the parent's credentials,
    which is the exact failure #1578 was filed about -- production data in a
    PR preview -- and it would look like success. Left unattached, the
    workload boots without that envelope key and fails loudly on first use,
    which is recoverable and visible.
    """
    from astrolift_services.models import ManagedService, ManagedServiceAttachment

    outcome = PreviewServiceOutcome()

    primary = getattr(preview_env, "previewed_environment", None)
    if primary is None:
        # No lineage: either the app has no non-preview environment on this
        # cluster, or the preview predates #1655. Nothing to inherit, and
        # guessing a source would be worse than inheriting nothing.
        return outcome

    services = ManagedService.objects.filter(
        app_environment=primary,
        deleted_at__isnull=True,
    ).select_related("registered_app")

    for service in services:
        # `name` is blank-tolerant on the model, so fall through to the pk
        # rather than keying an outcome map on "".
        slug = (getattr(service, "name", "") or "").strip() or f"service-{service.pk}"
        try:
            policy = resolve_policy(
                kind=service.kind,
                manifest_override=(service.config or {}).get("preview_policy"),
            )
        except ValueError as exc:
            # An unknown policy string in the manifest. Skipped rather than
            # defaulted: defaulting to shared would be the less safe reading
            # of an operator's typo.
            outcome.errors[slug] = f"unknown preview_policy: {exc}"
            continue

        if policy is PreviewPolicy.DEDICATED:
            # A dedicated preview service is a provision, not an attach, and
            # belongs to the managed-service lifecycle workflow rather than
            # here. Recorded so the caller can start one.
            outcome.skipped.append(slug)
            continue

        try:
            slice_result = _slice_for(service=service, preview_env=preview_env)
        except Exception as exc:  # noqa: BLE001 - one service must not stop the rest
            log.exception("preview services: slice failed for %s", slug)
            outcome.errors[slug] = str(exc)
            continue

        if slice_result is None:
            # Shared, but unsliceable. Not attached -- see the docstring.
            outcome.shared_unsliced.append(slug)
            continue

        attachment, _ = ManagedServiceAttachment.objects.get_or_create(
            managed_service=service,
            app_environment=preview_env,
        )
        # The handle is what teardown drops. Persisted here because it is
        # the only moment it exists: `provision_slice` derives it from
        # `slice_id` and returns it, and nothing else can recover it. Without
        # this the slice outlives the preview as an orphan database in the
        # parent instance, invisible until someone reads the database list
        # (#1670).
        handle = getattr(slice_result, "slice_handle", "") or ""
        if handle and attachment.slice_handle != handle:
            attachment.slice_handle = handle
            attachment.save(update_fields=["slice_handle"])
        outcome.attached.append(slug)
        outcome.sliced.append(slug)
        outcome.env_overrides.update(slice_result.env_overrides)

    return outcome


def _slice_for(*, service: Any, preview_env: Any) -> Any | None:
    """Carve a slice of ``service`` for ``preview_env``, or None.

    None means "this driver cannot slice", which the caller treats as a
    refusal to attach rather than as an error: a queue or a topic has no
    equivalent of a logical database, and a driver made to pretend would
    hand back the parent instance under a different name.
    """
    from astrolift_lifecycle.services.preview_slice_bindings import (
        configured_slice_driver,
        live_slice_source,
        slice_driver_registration,
        validate_attachment,
    )

    current, preview, cluster, spec = live_slice_source(service, preview_env)
    if (
        resolve_policy(kind=current.kind, manifest_override=(current.config or {}).get("preview_policy"))
        is not PreviewPolicy.SHARED_WITH_MAIN
    ):
        raise ValueError("preview managed-service policy changed before slice admission")
    driver = slice_driver_registration(current, cluster).driver_cls()
    if not getattr(driver, "supports_slicing", lambda: False)():
        return None
    validate_attachment(current, preview, driver, spec)
    driver = configured_slice_driver(current, cluster, credentials=True)
    return driver.provision_slice(spec)


@transaction.atomic
def deprovision_preview_managed_services(preview_env: Any) -> PreviewTeardownOutcome:
    """Drop the slices a preview owns, leaving every parent instance alone.

    The counterpart `provision_preview_managed_services` never had (#1670).
    `supports_slicing` requires both verbs precisely so a driver cannot
    carve a slice it can never remove, and then nothing called the removal
    half: the capability was present, tested, and reachable from its own
    test suite, so coverage stayed green while every teardown leaked.

    Returns an outcome rather than raising, and rather than returning
    nothing. A slice that will not drop is a database sitting in a shared
    instance; the operator needs to see which one, and the rest of the
    teardown needs to continue regardless. Raising here would strand the
    preview's namespace and DNS cleanup behind one stuck database.

    Idempotent: a preview torn down twice, or torn down after a partially
    failed provision, finds no handle to drop and reports nothing rather
    than failing.
    """
    from astrolift_services.models import ManagedServiceAttachment

    outcome = PreviewTeardownOutcome()

    attachments = ManagedServiceAttachment.objects.filter(
        app_environment=preview_env,
        deleted_at__isnull=True,
    ).select_related("managed_service")

    for attachment in attachments:
        service = attachment.managed_service
        slug = (getattr(service, "name", "") or "").strip() or f"service-{service.pk}"

        if not attachment.slice_handle:
            # Attached straight to the parent, or attached before #1670 gave
            # the row somewhere to record its slice. Nothing to drop either
            # way, and guessing a handle would risk dropping the parent's
            # own database.
            outcome.no_slice.append(slug)
            continue

        try:
            dropped = _drop_slice(service=service, attachment=attachment, preview_env=preview_env)
        except Exception as exc:  # noqa: BLE001 - one stuck slice must not stop the rest
            log.exception("preview teardown: slice drop failed for %s", slug)
            outcome.errors[slug] = str(exc)
            continue

        if not dropped:
            # The driver reports a delete that did not land. Recorded as a
            # leak, not an error: nothing is broken, something is left
            # behind, and those want different operator responses.
            outcome.leaked.append(slug)
            continue

        attachment.slice_handle = ""
        attachment.save(update_fields=["slice_handle"])
        outcome.dropped.append(slug)

    return outcome


def _drop_slice(*, service: Any, attachment: Any, preview_env: Any) -> bool:
    """Ask the driver to drop this attachment's slice.

    Mirrors `_slice_for` on the way up, including the `supports_slicing`
    check: a driver that has lost its slice verbs between provision and
    teardown must not be called with a handle it can no longer interpret.
    """
    from astrolift_lifecycle.services.preview_slice_bindings import (
        configured_slice_driver,
        live_slice_source,
        slice_driver_registration,
        validate_attachment,
    )

    current, preview, cluster, spec = live_slice_source(service, preview_env)
    driver = slice_driver_registration(current, cluster).driver_cls()
    if not getattr(driver, "supports_slicing", lambda: False)():
        raise ValueError("persisted preview slice driver cannot remove its slice")
    validate_attachment(current, preview, driver, spec)
    driver = configured_slice_driver(current, cluster)
    return bool(driver.deprovision_slice(spec, attachment.slice_handle))
