"""Owned Chromium -> real GraphQL/Postgres/mTLS authority lifecycle qualification."""

import importlib.util
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID

import pytest

from astrolift_clusters.models import NvkeOperatorAuthority
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_browser_integration import FRONTEND
from astrolift_clusters.tests.test_nvke_browser_integration import browser_endpoint as _browser_endpoint
from astrolift_clusters.tests.test_nvke_browser_integration import story_origin as _story_origin
from astrolift_clusters.tests.test_nvke_sites import context, create
from astrolift_clusters.tests.test_nvke_sites import world as _world
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken
from core.schema.audit import MutationAuditLog

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def story_origin():
    yield from _story_origin.__wrapped__()


@pytest.fixture
def browser_endpoint(settings, story_origin):
    yield from _browser_endpoint.__wrapped__(settings, story_origin)


@pytest.fixture
def redirect_peer():
    state = SimpleNamespace(count=0, private_body_received=False)

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "authorization, content-type, x-astrolift-organization"
            )

        def do_OPTIONS(self):
            self.send_response(204)
            self.cors()
            self.end_headers()

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.private_body_received = b'"clientKeyPem"' in data and b"PRIVATE KEY" in data
            state.count += 1
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"data":{"configureNvkeOperatorAuthority":{"ok":false,"errors":[{"code":"SOURCE_CHANGED"}]}}}'
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("redirect_probe", [False, True])
def test_browser_configures_once_recovers_fresh_versions_and_verifies_actual_agent(
    story_origin, browser_endpoint, tmp_path, redirect_peer, redirect_probe
):
    world = _world.__wrapped__()
    with context(world):
        site = create()
    source = FRONTEND.parent / "backend/providers/tests/nvke/test_operator_agent_client.py"
    spec = importlib.util.spec_from_file_location("browser_operator_agent_helpers", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = module.operator_agent(tmp_path, site_id=str(site.guid))
    state = next(agent)
    try:
        issued = mint_token()
        token = ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            name="owned agent browser",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=["read:nvke", "admin:nvke"],
        )
        config = {
            "storyOrigin": story_origin,
            "endpoint": browser_endpoint,
            "token": issued.plaintext,
            "organizationId": str(world.org.guid),
            "actorId": world.user.pk,
            "siteId": str(site.guid),
            "agentEndpoint": str(state.client._http.base_url).rstrip("/"),
            "instanceId": state.instance,
            "bundleSha256": state.digest,
            "caPem": (tmp_path / "ca.pem").read_text(),
            "clientCertificatePem": (tmp_path / "client.pem").read_text(),
            "clientKeyPem": (tmp_path / "client.key").read_text(),
        }
        if redirect_probe:
            config["redirectUrl"] = redirect_peer.url
        node = shutil.which("node")
        assert node
        result = subprocess.run(
            [node, str(FRONTEND / "e2e/nvke-agent-connected-runner.mjs")],
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=100,
            check=False,
            env=os.environ.copy(),
        )
        assert issued.plaintext not in result.stdout + result.stderr
        assert "PRIVATE KEY" not in result.stdout + result.stderr
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        if redirect_probe:
            assert receipt["redirectProbe"] and receipt["writes"]["configure"] == 1
            assert redirect_peer.count == 0, (
                "Private credential redirect reached owned sink",
                redirect_peer.count,
                redirect_peer.private_body_received,
            )
            assert not NvkeOperatorAuthority.all_objects.filter(site=site).exists()
            return
        assert receipt["writes"] == {"configure": 1, "verify": 2, "disable": 1, "enable": 1, "remove": 1}
        assert receipt["recoveredConfiguration"] and receipt["privateDraftCleared"] and receipt["removed"]
        row = NvkeOperatorAuthority.all_objects.get(site=site)
        assert row.deleted_at and not row.is_enabled and row.instance_id == UUID(state.instance)
        assert b"PRIVATE KEY" not in bytes(row.credential_ciphertext)
        assert not list((tmp_path / "state/jobs").iterdir())
        logs = MutationAuditLog.objects.filter(
            organization_id=world.org.pk, operation="nvke.operator_configure"
        )
        assert logs.count() == 1
        assert "PRIVATE KEY" not in str(logs.first().variables)
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
    finally:
        agent.close()
