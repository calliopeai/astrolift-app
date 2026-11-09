"""Pinned real agent/Kubernetes mTLS and frozen helper through the actual SDK."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


@pytest.mark.skipif(
    not all(
        os.getenv(k)
        for k in (
            "NVKE_TEST_AGENT_OPSCODE",
            "NVKE_TEST_AGENT_BUNDLE",
            "NVKE_TEST_HELM4",
            "NVKE_TEST_CHART_HELPER",
            "NVKE_TEST_CHART_RENDER_HELPER",
        )
    ),
    reason="Select the owned pinned agent, native bundle, Helm 4 and render helper",
)
def test_real_context_sdk_mtls_lost_reply_and_cold_recovery(tmp_path):
    source = Path(os.environ["NVKE_TEST_AGENT_OPSCODE"]).resolve()
    revision = "8ad214a1a71f6b082707da5317e20a612a54ae7e"
    archive = subprocess.check_output(["git", "archive", revision, "kubernetes/nvke"], cwd=source)
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        for entry in files:
            if entry.isfile() and (
                entry.name.endswith((".py", ".go")) or Path(entry.name).name in {"go.mod", "go.sum"}
            ):
                assert (source / entry.name).read_bytes() == files.extractfile(entry).read()
    driver = Path(__file__).parent / "fixtures/nvke_operator_context_driver.py"
    log = tmp_path / "context-sdk-driver.log"
    with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as output:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(driver),
                str(Path(__file__).resolve().parents[2]),
                str(source / "kubernetes/nvke"),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
    assert result.returncode == 0, f"SDK context qualification failed; private log: {log}"
