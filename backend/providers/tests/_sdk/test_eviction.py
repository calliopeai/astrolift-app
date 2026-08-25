"""The observability eviction seam (#1602 step 3).

The SDK had no delete verb at all, so four `Organization` retention columns
described a promise nothing could keep. These tests cover the two things
that make the seam safe rather than the happy path:

* `supported=False` is a **result**, not an exception. Most observability
  backends genuinely cannot delete, and a driver that raised for them would
  make the sweep's error count meaningless -- every run reporting failures
  for backends behaving as designed.
* An unsupported backend must never be reported as a **zero**. "0 evicted"
  for a backend that cannot delete is indistinguishable from "nothing to
  delete", so the operator reads an unenforced policy as enforced. That
  false zero is the #1594 failure mode.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.observability.eviction import (
    UNSUPPORTED_BACKENDS,
    EvictionOutcome,
    EvictionRequest,
    LokiEvictionDriver,
    UnsupportedEvictionDriver,
    unsupported_driver_for,
)

UTC = dt.UTC
START = dt.datetime(2026, 1, 1, tzinfo=UTC)
END = dt.datetime(2026, 1, 8, tzinfo=UTC)


@dataclass
class FakeHttp:
    status: int = 204
    raises: Exception | None = None
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def post(self, url: str, *, params: dict[str, str] | None = None) -> int:
        self.calls.append((url, dict(params or {})))
        if self.raises is not None:
            raise self.raises
        return self.status


@dataclass
class FakeLokiConfig:
    base_url: str = "http://loki:3100"
    timeout_seconds: int = 30
    bearer_token: str | None = None
    org_id: str | None = None
    http_client: Any | None = None


def _request(**kw) -> EvictionRequest:
    return EvictionRequest(**{"stream": "log", "start": START, "end": END, **kw})


# ---------------------------------------------------------------------------
# The request contract
# ---------------------------------------------------------------------------


def test_dry_run_is_the_default():
    """A delete verb whose default is "actually delete" is one bad kwarg
    away from an unrecoverable mistake."""
    assert _request().dry_run is True


def test_a_naive_window_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        EvictionRequest(stream="log", start=dt.datetime(2026, 1, 1), end=END)


def test_an_empty_window_is_refused():
    """Not pedantry: an inverted or empty window sent to Loki is accepted
    and deletes nothing, so the sweep would count a successful eviction
    that removed no data."""
    with pytest.raises(ValueError, match="non-empty"):
        EvictionRequest(stream="log", start=END, end=START)


def test_an_unsupported_outcome_cannot_claim_a_request():
    """The invariant that keeps the sweep's accounting honest."""
    with pytest.raises(ValueError):
        EvictionOutcome(supported=False, requested=3)


# ---------------------------------------------------------------------------
# Unsupported backends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", sorted(UNSUPPORTED_BACKENDS))
def test_every_unsupported_backend_says_where_retention_actually_lives(backend):
    """An operator reading "skipped 3" learns nothing. "tempo has no delete
    API; evict via the trace object store's lifecycle policy" tells them
    where retention really is."""
    outcome = unsupported_driver_for(backend).evict_before(_request(dry_run=False))

    assert outcome.supported is False
    assert outcome.requested == 0
    assert backend in outcome.detail
    assert UNSUPPORTED_BACKENDS[backend] in outcome.detail


def test_an_unsupported_backend_returns_rather_than_raising():
    """A raise here would make the sweep's error count meaningless: every
    run would report failures for backends behaving exactly as designed."""
    outcome = UnsupportedEvictionDriver(backend="tempo").evict_before(_request(dry_run=False))

    assert outcome.supported is False


def test_unsupported_is_distinguishable_from_a_real_zero():
    """The whole point of the flag. Both report zero evictions; only one of
    them means the retention policy is being enforced."""
    unsupported = unsupported_driver_for("prometheus").evict_before(_request(dry_run=False))
    nothing_to_do = EvictionOutcome(supported=True, requested=0, detail="no rows older than cutoff")

    assert unsupported.requested == nothing_to_do.requested == 0
    assert unsupported.supported != nothing_to_do.supported


# ---------------------------------------------------------------------------
# Loki
# ---------------------------------------------------------------------------


def test_a_dry_run_issues_no_call_at_all():
    """Loki has no dry-run parameter, so the only honest dry run is not
    issuing the request. Sending a real delete with a flag set would be the
    opposite of a dry run."""
    http = FakeHttp()
    driver = LokiEvictionDriver(config=FakeLokiConfig(http_client=http))

    outcome = driver.evict_before(_request(dry_run=True))

    assert http.calls == []
    assert outcome.supported is True
    assert outcome.requested == 0
    assert "dry run" in outcome.detail


def test_a_real_eviction_posts_the_window_in_unix_seconds():
    http = FakeHttp()
    driver = LokiEvictionDriver(config=FakeLokiConfig(http_client=http))

    outcome = driver.evict_before(_request(dry_run=False, selector='{namespace="acme"}'))

    (url, params) = http.calls[0]
    assert url == "http://loki:3100/loki/api/v1/delete"
    assert params["query"] == '{namespace="acme"}'
    assert params["start"] == str(int(START.timestamp()))
    assert params["end"] == str(int(END.timestamp()))
    assert outcome.supported is True
    assert outcome.requested == 1


def test_the_outcome_does_not_claim_rows_were_deleted():
    """Loki's delete API is asynchronous -- the compactor applies it later
    -- so claiming rows were deleted at return time would be a lie the API
    cannot back up. Hence `requested`, and the detail saying so."""
    driver = LokiEvictionDriver(config=FakeLokiConfig(http_client=FakeHttp()))

    outcome = driver.evict_before(_request(dry_run=False))

    assert "asynchronously" in outcome.detail


def test_deletion_disabled_is_unsupported_not_an_error():
    """Loki ships with deletion off. That is a configuration choice, not a
    fault, and raising every 24 hours on a correctly configured install
    would train operators to ignore the sweep."""
    http = FakeHttp(raises=RuntimeError("loki delete failed (400): deletion is not available"))
    driver = LokiEvictionDriver(config=FakeLokiConfig(http_client=http))

    outcome = driver.evict_before(_request(dry_run=False))

    assert outcome.supported is False
    assert outcome.requested == 0
    assert "not enabled" in outcome.detail


def test_a_genuine_failure_still_raises():
    """The other side of the above. If every 400 became `supported=False`,
    a broken Loki would look like a deliberately configured one and the
    sweep would report a tidy skip forever."""
    http = FakeHttp(raises=RuntimeError("loki delete failed (500): internal error"))
    driver = LokiEvictionDriver(config=FakeLokiConfig(http_client=http))

    with pytest.raises(RuntimeError, match="500"):
        driver.evict_before(_request(dry_run=False))


def test_the_default_selector_is_not_a_no_op():
    """An empty selector must not silently mean "delete nothing" -- the
    sweep would report an eviction that removed no data."""
    http = FakeHttp()
    driver = LokiEvictionDriver(config=FakeLokiConfig(http_client=http))

    driver.evict_before(_request(dry_run=False, selector=""))

    assert http.calls[0][1]["query"].strip() not in ("", "{}")
