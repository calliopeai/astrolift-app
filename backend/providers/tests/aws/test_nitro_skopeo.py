"""Exercise the real image-tool protocol without AWS, registries or run secrets."""

import os
import shutil

import pytest

from aws.nitro_skopeo import verify_keyprovider


def test_real_keyprovider_encryption_round_trip(tmp_path):
    binary = os.environ.get("NITRO_TEST_SKOPEO") or shutil.which("skopeo")
    if not binary:
        pytest.skip("Skopeo is required; the backend image build always runs this probe")
    verify_keyprovider(binary, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_failed_image_tool_refuses_capability_and_removes_fixture(tmp_path):
    with pytest.raises(ValueError, match="offline keyprovider"):
        verify_keyprovider("/usr/bin/false", str(tmp_path))
    assert list(tmp_path.iterdir()) == []
