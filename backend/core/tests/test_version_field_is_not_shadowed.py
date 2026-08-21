"""No model may redefine ``version`` as anything but the lock counter (#1517).

``TrackingMixin.version`` is an ``IntegerField`` and ``BaseCoreModel.save()``
increments it on every write. ``ProviderPlugin`` declared its own semver
``version = CharField(...)``, which shadowed it, so every save evaluated
``"0.0.0" + 1`` and raised ``TypeError``. The model could not be created,
saved, soft-deleted or restored through the ORM at all.

Nothing said so. The failure surfaced as a ``TypeError`` from a line in
``core/models/base.py`` that has nothing to do with what the caller was
doing, and the whole tree — 100+ sites, plus the bootstrap management
command — had quietly settled on ``bulk_create`` to route around it.

This is the guard that would have caught it on the day it was written, and
which stops the next subclass wanting a ``version`` of its own from
rediscovering it the same way.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import models

from core.mixins import TrackingMixin


def _tracking_models():
    return [m for m in apps.get_models() if issubclass(m, TrackingMixin)]


def test_the_guard_has_something_to_check():
    # A registry query that silently matched nothing would pass forever.
    assert len(_tracking_models()) > 20


@pytest.mark.django_db
def test_no_tracking_model_shadows_the_lock_counter():
    offenders = []
    for model in _tracking_models():
        field = model._meta.get_field("version")
        if not isinstance(field, models.IntegerField):
            offenders.append(f"{model._meta.label}.version is {type(field).__name__}")

    assert not offenders, (
        "`version` is TrackingMixin's optimistic-lock counter and "
        "BaseCoreModel.save() increments it. A model redefining it as another "
        "type makes every save raise TypeError: " + "; ".join(offenders)
    )


@pytest.mark.django_db
def test_a_provider_plugin_can_be_saved_through_the_orm():
    """The concrete case, end to end.

    Every one of these raised ``TypeError`` before the rename, which is why
    the suite reached for ``bulk_create`` everywhere.
    """
    from astrolift_clusters.models import ProviderPlugin

    plugin = ProviderPlugin.objects.create(
        name="AWS",
        slug="aws-orm-check",
        plugin_version="1.2.3",
        capabilities_manifest={},
        config_schema={},
    )
    assert plugin.plugin_version == "1.2.3"
    assert plugin.version == 1, "the lock counter should have been bumped by the create"

    plugin.name = "AWS (renamed)"
    plugin.save()
    plugin.refresh_from_db()
    assert plugin.version == 2
    assert plugin.plugin_version == "1.2.3", "the semver is not the counter"

    # Soft delete and restore both go through save(update_fields=[..., "version"]),
    # so they were broken too — on a model whose base class exists partly to
    # guarantee them.
    plugin.soft_delete()
    plugin.refresh_from_db()
    assert plugin.deleted_at is not None

    plugin.restore()
    plugin.refresh_from_db()
    assert plugin.deleted_at is None
