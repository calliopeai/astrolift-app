from __future__ import annotations

from _sdk.managed_service import UpdateResult


def test_update_failures_retry_by_default() -> None:
    result = UpdateResult(
        ok=False,
        handle="postgres/example",
        message="provider temporarily unavailable",
    )

    assert result.retryable is True
