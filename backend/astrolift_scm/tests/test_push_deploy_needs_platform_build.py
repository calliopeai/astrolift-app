"""A push deploys only what the platform builds (ConflictHQ/bdr, 2026-09-27).

The webhook deployed a ``ci_pushed`` app's commit SHA as its image tag, a tag
no one had built; every pod failed to pull it and the app read "running".
"""

from types import SimpleNamespace

import pytest

from astrolift_registry.models import RegisteredApp
from auth1.scm_webhook import _push_builds_the_image


@pytest.mark.parametrize(
    ("mode", "deploys"),
    [
        (RegisteredApp.BuildMode.PLATFORM_BUILD, True),
        (RegisteredApp.BuildMode.CI_PUSHED, False),
        (RegisteredApp.BuildMode.NONE, False),
    ],
)
def test_only_a_platform_build_deploys_straight_from_a_push(mode, deploys):
    assert _push_builds_the_image(SimpleNamespace(build_mode=mode)) is deploys
