"""A run records the commit it is of, not just the ref (#1531).

`trigger_ref` is a branch or tag name. Names move: two runs of "main" a
week apart are different code, and a pipeline definition cannot be pinned
to something that will not hold still.

Both webhook receivers already had the commit in the payload and were
dropping it on the floor.
"""

from __future__ import annotations

import pytest

from astrolift_pipelines.gitlab_webhook_views import _extract_gitlab_info
from astrolift_pipelines.webhook_views import _extract_repo_info

REPO = {"clone_url": "https://example.invalid/acme/shop.git", "html_url": "https://x/y"}
SHA = "9f2c1e4b8a7d6c5f4e3d2c1b0a9f8e7d6c5b4a39"


def test_a_github_push_records_the_commit_it_landed_on():
    _, _, ref, _, sha = _extract_repo_info(
        "push",
        {"repository": REPO, "ref": "refs/heads/main", "after": SHA, "sender": {"login": "dev"}},
    )

    assert ref == "refs/heads/main"
    assert sha == SHA


def test_a_github_pull_request_records_its_head():
    # The head sha, not the base: a PR run tests the proposed code.
    _, _, ref, _, sha = _extract_repo_info(
        "pull_request",
        {
            "repository": REPO,
            "pull_request": {"head": {"ref": "feature/x", "sha": SHA}},
            "sender": {"login": "dev"},
        },
    )

    assert ref == "feature/x"
    assert sha == SHA


def test_a_gitlab_push_records_the_checkout_sha():
    _, _, ref, _, sha = _extract_gitlab_info(
        "Push Hook",
        {"project": {"git_ssh_url": "git@x:y.git"}, "ref": "refs/heads/main", "checkout_sha": SHA},
    )

    assert ref == "refs/heads/main"
    assert sha == SHA


def test_a_gitlab_merge_request_records_its_last_commit():
    _, _, ref, _, sha = _extract_gitlab_info(
        "Merge Request Hook",
        {
            "project": {"git_ssh_url": "git@x:y.git"},
            "object_attributes": {"source_branch": "feature/x", "last_commit": {"id": SHA}},
        },
    )

    assert ref == "feature/x"
    assert sha == SHA


@pytest.mark.parametrize(
    ("extract", "event", "payload"),
    [
        (_extract_repo_info, "push", {"repository": REPO, "ref": "refs/heads/main"}),
        (_extract_gitlab_info, "Push Hook", {"project": {}, "ref": "refs/heads/main"}),
    ],
)
def test_a_payload_without_a_sha_is_blank_not_a_crash(extract, event, payload):
    # A ping, a malformed body, or a provider that changes its shape must
    # not take the receiver down — the run is still worth creating.
    result = extract(event, payload)

    assert result[4] == ""


@pytest.mark.parametrize(
    ("extract", "event"),
    [(_extract_repo_info, "issues"), (_extract_gitlab_info, "Issue Hook")],
)
def test_an_unrelated_event_carries_no_sha(extract, event):
    result = extract(event, {"repository": REPO, "project": {}})

    assert result[2] == ""
    assert result[4] == ""
