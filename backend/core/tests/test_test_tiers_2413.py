"""Guardrail for the CI test tiers (#2413).

The tiers are only as good as their markers: a misspelt ``@pytest.mark.slwo``
must fail collection instead of quietly landing in the pull-request tier,
and a ``manual`` test must never start because some ``-m`` expression
forgot to exclude it.
"""

import pytest

from core.testing.tiers import TIERS, names_manual, refuse_manual_in_ci, split_manual

CI = {"CI": "true"}
LOCAL: dict[str, str] = {}
PR_TIER = "not slow and not rarely and not manual"
MAIN_TIER = "not rarely and not manual"
NIGHTLY_TIER = "not manual"


class _Item:
    def __init__(self, *markers):
        self.markers = set(markers)

    def get_closest_marker(self, name):
        return name if name in self.markers else None


def test_tier_markers_are_registered_and_strict(pytestconfig):
    registered = {line.split(":")[0].split("(")[0].strip() for line in pytestconfig.getini("markers")}
    assert set(TIERS) <= registered
    assert pytestconfig.getini("strict_markers") is True


def test_an_unregistered_marker_fails_instead_of_running_untiered():
    with pytest.raises(pytest.fail.Exception, match="not found in `markers`"):
        pytest.mark.slwo  # noqa: B018


@pytest.mark.parametrize("markexpr", ["", "slow", "not slow", MAIN_TIER, PR_TIER, NIGHTLY_TIER])
def test_ci_tier_expressions_are_accepted(markexpr):
    refuse_manual_in_ci(markexpr, CI)


@pytest.mark.parametrize("markexpr", ["manual", "slow or manual", "manual and slow", "not slow and manual"])
def test_ci_refuses_any_expression_that_asks_for_manual_tests(markexpr):
    with pytest.raises(pytest.UsageError, match="never run in CI"):
        refuse_manual_in_ci(markexpr, CI)
    refuse_manual_in_ci(markexpr, LOCAL)


@pytest.mark.parametrize(
    ("markexpr", "environ", "kept"),
    [
        ("", LOCAL, {"plain", "slow"}),
        ("not slow", LOCAL, {"plain", "slow"}),
        ("slow", LOCAL, {"plain", "slow"}),
        ("", CI, {"plain", "slow"}),
        (PR_TIER, CI, {"plain", "slow"}),
        (NIGHTLY_TIER, CI, {"plain", "slow"}),
        # Naming the tier hands the choice to pytest's own -m filter.
        ("manual", LOCAL, {"plain", "slow", "manual"}),
        ("not manual", LOCAL, {"plain", "slow", "manual"}),
    ],
)
def test_manual_tests_stay_only_when_named_outside_ci(markexpr, environ, kept):
    items = {"plain": _Item(), "slow": _Item("slow"), "manual": _Item("manual")}
    selected, deselected = split_manual(markexpr, environ, items.values())
    assert {name for name, item in items.items() if item in selected} == kept
    assert len(selected) + len(deselected) == len(items)


def test_this_run_holds_no_unrequested_manual_test(request):
    markexpr = request.config.getoption("markexpr")
    manual = [item.nodeid for item in request.session.items if item.get_closest_marker("manual")]
    assert not manual or names_manual(markexpr), manual
