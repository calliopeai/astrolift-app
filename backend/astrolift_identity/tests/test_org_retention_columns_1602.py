"""The three retention columns nobody could read or write (#1602).

`metrics_retention_days_default`, `metrics_rollup_retention_days_default`
and `trace_retention_days_default` were added to `Organization` in
migration 0006 and never reached the API. So an operator could set them
through no surface and read them through no surface, while the eviction
that would honour them did not exist either -- three columns holding a
promise nothing made and nothing kept.

This is step 1 of that issue's plan: make them visible and bounded, with
**no runtime behaviour change**. Nothing evicts yet. Exposing before
enforcing is the order that matters, because the first thing an operator
should be able to do with a retention knob is see what it currently says.
"""

from __future__ import annotations

import pytest

from astrolift_identity.schema.mutations.types import UpdateOrganizationInput
from astrolift_identity.schema.types import organization_to_type
from astrolift_operations.observability_profile import (
    RETENTION_METRICS,
    RETENTION_METRICS_ROLLUP,
    RETENTION_TRACES,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Retention", slug="retention-org")


def test_all_three_are_readable(org):
    """The half that did not exist at all: no surface returned them."""
    out = organization_to_type(org)

    assert out.metrics_retention_days_default == org.metrics_retention_days_default
    assert out.metrics_rollup_retention_days_default == org.metrics_rollup_retention_days_default
    assert out.trace_retention_days_default == org.trace_retention_days_default


def test_the_rollup_window_is_not_the_raw_metrics_window():
    """The bug bounding the rollup against RETENTION_METRICS would have
    baked in: the column defaults to 365 and that window caps at 365, so
    the knob's only legal value would be the one it already has."""
    assert RETENTION_METRICS.max_days == 365
    assert RETENTION_METRICS_ROLLUP.default_days == 365
    assert RETENTION_METRICS_ROLLUP.max_days > RETENTION_METRICS_ROLLUP.default_days
    # And longer than raw, which is the point of a rollup.
    assert RETENTION_METRICS_ROLLUP.max_days > RETENTION_METRICS.max_days


@pytest.mark.parametrize(
    "field,window",
    [
        ("metrics_retention_days_default", RETENTION_METRICS),
        ("metrics_rollup_retention_days_default", RETENTION_METRICS_ROLLUP),
        ("trace_retention_days_default", RETENTION_TRACES),
    ],
)
def test_each_is_bounded_by_its_own_window(field, window):
    """Each against its own ceiling, not a shared one. Above it, the
    backend would be asked for data the platform never promised to keep --
    and the failure would land at query time, far from the setting."""
    assert UpdateOrganizationInput.__annotations__[field] == "int | None"
    assert window.max_days >= window.default_days


def test_the_input_accepts_all_three():
    """A field absent from the input is a column that stays unwritable
    however good the validation behind it is."""
    from astrolift_graphql import GUID

    supplied = UpdateOrganizationInput(
        id=GUID("01a00000-0000-7000-8000-000000000000"),
        metrics_retention_days_default=60,
        metrics_rollup_retention_days_default=500,
        trace_retention_days_default=30,
    )

    assert supplied.metrics_retention_days_default == 60
    assert supplied.metrics_rollup_retention_days_default == 500
    assert supplied.trace_retention_days_default == 30


def test_every_retention_column_on_organization_is_now_exposed():
    """The ratchet. A fourth retention column added later and left off the
    type is the same defect these three were, and nothing else in the repo
    would notice -- the column would simply be settable by nobody.
    """
    from astrolift_identity.models import Organization
    from astrolift_identity.schema.types import OrganizationType

    columns = {
        f.name
        for f in Organization._meta.get_fields()
        if getattr(f, "attname", None) and "retention" in f.name
    }
    exposed = set(OrganizationType.__annotations__)

    missing = sorted(columns - exposed)
    assert not missing, (
        "these Organization retention columns are not on OrganizationType, so "
        "nothing can read them:\n  " + "\n  ".join(missing)
    )
