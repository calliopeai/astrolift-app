"""Tests for ``preview_to_type`` serialization (#431).

Covers the new fields: ``ttl_until``, ``source_url``, ``pr_url`` (with
GitHub URL normalization), ``aggregate_resources`` (default zero when
not injected), ``estimated_daily_cost_usd`` (null by default).
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_lifecycle.schema.types import (
    PreviewAggregateResourcesType,
    _build_pr_url,
    preview_to_type,
)

pytestmark = pytest.mark.django_db


def _make_preview(app, env, *, pr_number: int = 42):
    return PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=env,
        pr_number=pr_number,
        branch=f"feat-{pr_number}",
        commit_sha="abc1234",
        status=PreviewEnvironment.Status.RUNNING.value,
        hostname=f"pr-{pr_number}-hello.pr.acme.example.com",
        namespace=f"preview-{pr_number}",
    )


@pytest.mark.parametrize(
    "source_url,pr_number,expected",
    [
        (
            "https://github.com/acme/hello",
            42,
            "https://github.com/acme/hello/pull/42",
        ),
        (
            "https://github.com/acme/hello/",
            42,
            "https://github.com/acme/hello/pull/42",
        ),
        (
            "https://github.com/acme/hello.git",
            42,
            "https://github.com/acme/hello/pull/42",
        ),
    ],
)
def test_build_pr_url_normalises_repo(source_url, pr_number, expected):
    assert _build_pr_url(source_url, pr_number) == expected


def test_preview_to_type_resolves_source_and_pr_urls(app, env):
    app.source_url = "https://github.com/acme/hello.git"
    app.save(update_fields=["source_url"])
    preview = _make_preview(app, env, pr_number=99)
    out = preview_to_type(preview)
    assert out.source_url == "https://github.com/acme/hello.git"
    assert out.pr_url == "https://github.com/acme/hello/pull/99"


def test_preview_to_type_blank_pr_url_when_no_source(app, env):
    """No source_url → empty pr_url (renderer hides the link)."""
    app.source_url = ""
    app.save(update_fields=["source_url"])
    preview = _make_preview(app, env, pr_number=99)
    out = preview_to_type(preview)
    assert out.source_url == ""
    assert out.pr_url == ""


def test_preview_to_type_defaults_aggregate_to_zero_when_not_injected(app, env):
    preview = _make_preview(app, env)
    out = preview_to_type(preview)
    assert out.aggregate_resources.cpu_cores == 0.0
    assert out.aggregate_resources.memory_bytes == 0.0
    assert out.aggregate_resources.pod_count == 0
    assert out.estimated_daily_cost_usd is None


def test_preview_to_type_passes_through_injected_aggregate(app, env):
    preview = _make_preview(app, env)
    agg = PreviewAggregateResourcesType(cpu_cores=0.75, memory_bytes=1024**3, pod_count=3)
    out = preview_to_type(
        preview,
        aggregate_resources=agg,
        estimated_daily_cost_usd=2.34,
    )
    assert out.aggregate_resources.cpu_cores == pytest.approx(0.75)
    assert out.aggregate_resources.memory_bytes == 1024**3
    assert out.aggregate_resources.pod_count == 3
    assert out.estimated_daily_cost_usd == pytest.approx(2.34)


def test_preview_to_type_exposes_ttl_until(app, env):
    preview = _make_preview(app, env)
    out = preview_to_type(preview)
    assert out.ttl_until == preview.ttl_until
