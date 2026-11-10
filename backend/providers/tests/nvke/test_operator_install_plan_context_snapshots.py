from collections import Counter

import pytest

from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import JOB, items_page
from tests.nvke.test_operator_install_plan_context import call, peer
from tests.nvke.test_operator_install_plan_verification import intercept


def test_complete_context_source_is_checked_twice_on_each_side_without_recursive_parent_fetches():
    sdk, calls = peer()
    with sdk:
        call(sdk)
    split = next(
        i for i, (method, path, _) in enumerate(calls) if method == "POST" and path.endswith("/install-plan-context")
    )
    for side in (calls[:split], calls[split + 1 :]):
        counts = Counter(path.rsplit("/", 1)[-1] + ("/items" if query.get("view") else "") for _, path, query in side)
        assert all(
            counts[name] >= 2
            for name in ("install-plan", "install-plan-charts", "install-plan-tree", "install-plan/items")
        )
    assert len(calls) <= 50


@pytest.mark.parametrize("node", ["original", "plan", "chart", "tree", "item", "capability"])
def test_context_observation_does_not_hide_any_parent_change(node):
    sdk, calls = peer()

    def change(request, data):
        if not any(method == "POST" for method, _, _ in calls):
            return data
        path = request.url.path
        if node == "original" and path.endswith(JOB):
            data["cancellationRequested"] = True
        elif node == "plan" and path.endswith("/install-plan") and not request.url.params.get("view"):
            data["proposedBytes"] = "457"
        elif node == "chart" and path.endswith("/install-plan-charts"):
            data["expandedBytes"] = "457"
        elif node == "tree" and path.endswith("/install-plan-tree"):
            data["fileCount"] = 8
        elif node == "item" and request.url.params.get("view"):
            data["items"][1]["contentSha256"] = "e" * 64
        elif node == "capability" and path == "/v1/identity":
            data["installPlanTree"]["continuation"] = True
        return data

    intercept(sdk, change)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


@pytest.mark.parametrize("node", ["original", "chart", "capability"])
def test_drift_within_initial_graph_read_refuses_before_context_collection(node):
    sdk, calls = peer()

    def change(request, data):
        if any(path.endswith("/install-plan-tree") for _, path, _ in calls):
            if node == "original" and request.url.path.endswith(JOB):
                data["cancellationRequested"] = True
            elif node == "chart" and request.url.path.endswith("/install-plan-charts"):
                data["expandedBytes"] = "457"
            elif node == "capability" and request.url.path == "/v1/identity":
                data["installPlanCharts"]["prepare"] = False
        return data

    intercept(sdk, change)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert not any(method == "POST" for method, _, _ in calls)


@pytest.mark.parametrize(
    "fault", [None, "duplicate-name", "duplicate-ordinal", "changed-total", "missing-tail", "cursor-cycle"]
)
def test_context_item_pages_are_complete_ordered_and_finite(fault):
    sdk, calls = peer()
    total = 201 if fault == "cursor-cycle" else 101
    template = items_page()["items"][1]

    def change(request, data):
        if not request.url.path.endswith("/install-plan"):
            return data
        if not request.url.params.get("view"):
            data["itemCount"] = total
            return data
        tail = bool(request.url.params.get("after"))
        rows = [
            {
                **template,
                "name": "nktp" if i == 1 else f"phase-{i}",
                "ordinal": i,
                "originalObservation": "returned-before-next-observed-item"
                if i == 0
                else "last-observed-in-unsuccessful-run"
                if i == 1
                else "not-observed",
                "proposedAction": "skip" if i == 0 else "execute",
            }
            for i in range(total)
        ]
        data.update(
            totalCount=total,
            items=rows[100:200] if tail else rows[:100],
            nextCursor="cursor" if not tail or fault == "cursor-cycle" else None,
        )
        if tail and fault == "duplicate-name":
            data["items"][0]["name"] = "nktp"
        elif tail and fault == "duplicate-ordinal":
            data["items"][0]["ordinal"] = 99
        elif tail and fault == "changed-total":
            data["totalCount"] = 100
        elif tail and fault == "missing-tail":
            data["items"] = []
        return data

    intercept(sdk, change)
    with sdk:
        if fault is None:
            call(sdk)
        else:
            with pytest.raises(NvkeError) as error:
                call(sdk)
            assert error.value.code == "OPERATOR_INVALID_RESPONSE"
            assert not any(method == "POST" for method, _, _ in calls)
