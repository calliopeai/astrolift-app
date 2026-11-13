"""Owned pinned runtime + actual SDK/mTLS/Helm 4 execution-tree recovery."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.nvke.support import PAIRED_OPSCODE_REVISION, prepare_owned_native_agent


@pytest.mark.skipif(
    not all(
        os.getenv(name)
        for name in ("NVKE_TEST_AGENT_OPSCODE", "NVKE_TEST_AGENT_BUNDLE", "NVKE_TEST_HELM4", "NVKE_TEST_CHART_HELPER")
    ),
    reason="Select the owned pinned Opscode runtime, native bundle, Helm 4 and helper",
)
def test_real_sdk_mtls_tree_lost_reply_cold_agent_recovery_and_private_retry(tmp_path):
    prepare_owned_native_agent()
    source = Path(os.environ["NVKE_TEST_AGENT_OPSCODE"]).resolve()
    revision = PAIRED_OPSCODE_REVISION
    for name in (
        "operator_agent.py",
        "operator_contract.py",
        "management_recovery.py",
        "management_plan.py",
        "management_plan_store.py",
        "management_plan_preparation.py",
        "management_dependencies.py",
        "management_chart_archive.py",
        "management_chart_materialization.py",
        "management_plan_chart_service.py",
        "management_execution_release.py",
        "management_execution_arguments.py",
        "management_execution_tree.py",
        "management_template_rendering.py",
        "management_render_inputs.py",
        "management_render_discovery.py",
        "management_render_release.py",
        "management_render_lookup.py",
        "management_render_capture.py",
        "management_render_context_store.py",
        "management_render_authenticated.py",
        "management_render_transport.py",
        "management_render_executor.py",
        "management_plan_context_service.py",
        "management_frozen_chart.py",
        "management_plan_tree_service.py",
        "qualify_bootstrap.py",
        "tooling.py",
        "test_operator_agent.py",
        "chart-helper/main.go",
        "chart-helper/render.go",
        "chart-helper/release.go",
        "chart-helper/go.mod",
        "chart-helper/go.sum",
    ):
        relative = "kubernetes/nvke/" + name
        assert (source / relative).read_bytes() == subprocess.check_output(
            ["git", "show", revision + ":" + relative], cwd=source
        )
    driver = Path(__file__).parent / "fixtures/nvke_operator_tree_driver.py"
    driver_log = tmp_path / "tree-sdk-driver.log"
    with os.fdopen(os.open(driver_log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as output:
        exit_code = subprocess.run(
            [
                sys.executable,
                "-I",
                str(driver),
                str(Path(__file__).resolve().parents[2]),
                str(source / "kubernetes/nvke"),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=420,
        ).returncode
    assert exit_code == 0, f"Owned SDK/mTLS/Helm 4 tree qualification failed; private log: {driver_log}"
