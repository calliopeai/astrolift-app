"""A cluster registered before #1681 still gets the install's boundary."""

from __future__ import annotations

import pytest

from core.app_deploy import permissions_boundary_arn

BOUNDARY = "arn:aws:iam::790347818717:policy/conflict-agent-boundary"


@pytest.fixture
def env_boundary(monkeypatch):
    monkeypatch.setenv("ASTROLIFT_CLUSTER_IAM_PERMISSIONS_BOUNDARY_ARN", BOUNDARY)


def test_the_recorded_boundary_wins(env_boundary):
    recorded = "arn:aws:iam::790347818717:policy/other"
    assert (
        permissions_boundary_arn({"iam_permissions_boundary_arn": recorded, "account_id": "790347818717"})
        == recorded
    )


def test_an_unrecorded_cluster_in_the_install_account_takes_the_env_boundary(env_boundary):
    assert permissions_boundary_arn({"account_id": "790347818717"}) == BOUNDARY


@pytest.mark.parametrize("pc", [{"account_id": "111111111111"}, {}])
def test_a_cluster_in_another_or_unknown_account_does_not(env_boundary, pc):
    assert permissions_boundary_arn(pc) == ""


def test_no_env_and_nothing_recorded_means_no_boundary(monkeypatch):
    monkeypatch.delenv("ASTROLIFT_CLUSTER_IAM_PERMISSIONS_BOUNDARY_ARN", raising=False)
    assert permissions_boundary_arn({"account_id": "790347818717"}) == ""
