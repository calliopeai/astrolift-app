"""The GitHub notify step follows the deployment to a terminal state (v12).

A 2xx from ``/deploy/`` only means accepted. The step now polls the
response's ``polling_url``: success and superseded exit 0, a failure exits 1
with the platform's reason, and so does a deploy that never finishes.

These run the rendered step's own shell: ``curl`` is a stub that writes the
accept response, and a local HTTP server plays the status endpoint. The test
only shortens the 10 s sleep and, for the timeout case, the 15 minute bound.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
import yaml

from astrolift_scm.services.workflow_sync import render_astrolift_ci_workflow

_POLLING_URL = "/api/cli/v1/deployments/abc/status/"


def _app() -> SimpleNamespace:
    return SimpleNamespace(
        guid="11111111-1111-4111-8111-111111111111",
        organization_id=1,
        build_mode="none",
        dockerfile_path="Dockerfile",
        build_context=".",
        build_args={},
        slug="hello-app",
        deploy_branch="main",
        registry_repo_uri="",
        push_role_ref="",
        source_kind="github",
        source_repo="acme/hello-app",
    )


def _notify_script(settings) -> str:
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    workflow = yaml.safe_load(render_astrolift_ci_workflow(_app()))
    steps = workflow["jobs"]["build-and-deploy"]["steps"]
    return next(s for s in steps if s.get("name") == "Notify Astrolift")["run"]


class _StatusServer:
    """Serves the queued status bodies in order, repeating the last one."""

    def __init__(self, responses: list[tuple[int, dict]]):
        self.responses = list(responses)
        self.auth: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.auth.append(self.headers.get("Authorization", ""))
                code, body = outer.responses.pop(0) if len(outer.responses) > 1 else outer.responses[0]
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def _run(settings, tmp_path, responses, *, script_edit=lambda s: s):
    script = script_edit(_notify_script(settings).replace("time.sleep(10)", "time.sleep(0)"))
    # GitHub substitutes expressions before the shell runs; do the same.
    script = script.replace("${{ github.sha }}", "a" * 40).replace("${{ github.ref_name }}", "main")
    # The stub stands in for the accept POST.
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f"printf '%s' '{json.dumps({'deployment_id': 'abc', 'polling_url': _POLLING_URL})}'"
        " > /tmp/astrolift-deploy-response.json\n"
        "printf 201\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    server = _StatusServer(responses)
    try:
        result = subprocess.run(
            ["bash", "-e", "-c", script],
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "API_URL": server.url,
                "APP_SLUG": "hello-app",
                "TOKEN": "alft_dt_test",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        server.close()
    return result, server


def _status(state, status, *, superseded=False, reason=""):
    return 200, {"state": state, "status": status, "superseded": superseded, "reason": reason}


def test_waits_through_in_flight_states_then_succeeds(settings, tmp_path):
    result, server = _run(
        settings,
        tmp_path,
        [_status("pending", "pending"), _status("deploying", "deploying"), _status("succeeded", "running")],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Astrolift deploy succeeded (running)." in result.stdout
    assert len(server.auth) == 3
    assert set(server.auth) == {"Bearer alft_dt_test"}


def test_superseded_exits_zero_and_says_so(settings, tmp_path):
    result, _ = _run(
        settings,
        tmp_path,
        [
            _status("deploying", "deploying"),
            _status("cancelled", "superseded", superseded=True, reason="superseded by a newer deploy"),
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "superseded by a newer deploy" in result.stdout


def test_failure_exits_one_with_the_reason(settings, tmp_path):
    result, _ = _run(settings, tmp_path, [_status("failed", "failed", reason="rollout timed out on web")])

    assert result.returncode == 1
    assert "Astrolift deploy FAILED (failed): rollout timed out on web" in result.stdout


def test_status_endpoint_refusal_fails_at_once(settings, tmp_path):
    result, server = _run(settings, tmp_path, [(403, {"detail": "nope"})])

    assert result.returncode == 1
    assert "HTTP 403" in result.stdout
    assert len(server.auth) == 1


def test_transient_server_error_is_retried(settings, tmp_path):
    result, _ = _run(settings, tmp_path, [(502, {}), _status("succeeded", "running")])

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_deploy_that_never_finishes_fails_after_the_bound(settings, tmp_path):
    result, _ = _run(
        settings,
        tmp_path,
        [_status("deploying", "deploying")],
        script_edit=lambda s: s.replace("15 * 60", "0"),
    )

    assert result.returncode == 1
    assert "did not finish within 15 minutes" in result.stdout


@pytest.mark.parametrize("needle", ["time.sleep(10)", "15 * 60", "polling_url"])
def test_the_bound_and_interval_are_in_the_rendered_step(settings, needle):
    assert needle in _notify_script(settings)
