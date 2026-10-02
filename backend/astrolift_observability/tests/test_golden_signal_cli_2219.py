"""Compiled public CLI reads actual authenticated PostgreSQL-backed GraphQL."""

import json
import os
import subprocess

import pytest

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_user

from .test_workload_signal_scope_2219 import QUERY

CLI = os.environ.get("ASTROLIFT_TEST_CLI")
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not CLI, reason="requires an explicitly compiled public astro binary"),
]
pytest_plugins = ["core.tests.test_metric_runtime_identity_2219"]


def test_public_cli_discloses_exact_target_and_refuses_token_ceiling(runtime, live_server, tmp_path):
    user = make_user("signals-cli-2219")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=runtime.org.pk)
    bind_role(
        user,
        permissions=[Permission.APP_READ, Permission.ORG_READ],
        kind="ORG",
        scope_id=runtime.org.pk,
        slug="signal-cli-reader",
    )
    issued = mint_token()
    row = ApiToken.objects.create(
        user=user,
        organization=runtime.org,
        name="disposable golden signals verification",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:apps"],
    )
    document = tmp_path / "signals.graphql"
    document.write_text(QUERY)
    environment = dict(os.environ, ASTROLIFT_TOKEN=issued.plaintext, ASTROLIFT_NO_UPDATE_CHECK="1")
    command = [
        CLI,
        "--api-url",
        live_server.url,
        "--org",
        str(runtime.org.guid),
        "--no-prompt",
        "api",
        "graphql",
        "--file",
        str(document),
        "--var",
        "app=" + runtime.app.slug,
        "--var",
        "environment=" + runtime.environment.name,
        "--var",
        "workload=" + runtime.workload.slug,
    ]
    result = subprocess.run(command, env=environment, capture_output=True, timeout=20, text=True)
    assert issued.plaintext not in result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)["astroliftAppGoldenSignals"]["signals"]
    assert len(rows) == 8
    for signal in rows:
        measured = signal["measurement"]
        assert measured["target"]["environmentId"] == str(runtime.environment.guid)
        assert measured["target"]["workloadId"] == str(runtime.workload.guid)
        assert measured["effectiveScope"] == "WORKLOAD"
        assert measured["unavailableReason"] == "NOT_CONFIGURED"
        assert signal["samples"] == []
    row.scopes = ["read:clusters"]
    row.save(update_fields=["scopes"])
    denied = subprocess.run(command, env=environment, capture_output=True, timeout=20, text=True)
    assert denied.returncode != 0
    assert denied.stdout == ""
    assert issued.plaintext not in denied.stdout + denied.stderr
