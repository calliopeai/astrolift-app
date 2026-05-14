"""Unit tests for the five-field cron validator (#290)."""

from __future__ import annotations

import pytest

from astrolift_registry.cron import CronValidationError, validate_cron_expression


@pytest.mark.parametrize(
    "expr",
    [
        "* * * * *",
        "0 6 * * *",
        "*/5 * * * *",
        "0,15,30,45 * * * *",
        "0 0 1-15 * *",
        "30 9-17 * * 1-5",
        "0 0 1 1 0",
        "59 23 31 12 6",
        "0,30 */2 * * *",
    ],
)
def test_validate_cron_expression_accepts_valid(expr):
    assert validate_cron_expression(expr) == " ".join(expr.split())


def test_validate_cron_collapses_internal_whitespace():
    assert validate_cron_expression("0   6  *   *  *") == "0 6 * * *"


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "   ",
        "0 6 * *",
        "0 6 * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * 32 * *",
        "* * * 0 *",
        "* * * 13 *",
        "* * * * 7",
        "*/0 * * * *",
        "*/-1 * * * *",
        "5-3 * * * *",
        "10-90 * * * *",
        "0,, * * * *",
        "MON * * * *",
        "0 6 * * mon",
        "0 6 * * #1",
        "@daily",
    ],
)
def test_validate_cron_expression_rejects_invalid(expr):
    with pytest.raises(CronValidationError):
        validate_cron_expression(expr)
