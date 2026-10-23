"""A native source stage's budget counts its native work, not its checkpoint calls (#2509)."""

from threading import Thread

import pytest

from astrolift_services.gcp_app_identity_plan import SourceBudget


class Clock:
    """Moved only by the test; nothing sleeps."""

    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def test_time_inside_checkpoint_calls_does_not_spend_the_budget():
    clock = Clock()
    budget = SourceBudget(120, clock=clock)

    def readmission():
        clock.now += 500

    checkpoint = budget.unspent(readmission)
    for _ in range(3):
        checkpoint()
    assert not budget.expired()
    clock.now += 119
    assert not budget.expired()
    clock.now += 1
    assert budget.expired()


def test_a_check_inside_a_call_skips_the_call_so_far_and_nested_calls_count_once():
    clock = Clock()
    budget = SourceBudget(120, clock=clock)
    seen = []

    def inner():
        clock.now += 50
        seen.append(budget.expired())

    nested = budget.unspent(inner)

    def outer():
        clock.now += 50
        nested()
        clock.now += 50
        seen.append(budget.expired())

    clock.now += 100
    budget.unspent(outer)()
    assert seen == [False, False]
    clock.now += 19
    assert not budget.expired()
    clock.now += 1
    assert budget.expired()


def test_refused_calls_and_calls_from_another_thread_do_not_spend_it_either():
    clock = Clock()
    budget = SourceBudget(120, clock=clock)

    def refused():
        clock.now += 500
        raise PermissionError("withdrawn")

    with pytest.raises(PermissionError):
        budget.unspent(refused)()

    def callback():
        clock.now += 500

    thread = Thread(target=budget.unspent(callback))
    thread.start()
    thread.join()
    assert not budget.expired()
    clock.now += 120
    assert budget.expired()
