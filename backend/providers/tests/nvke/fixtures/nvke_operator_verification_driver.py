import json
import socket
import ssl
import sys
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])

import test_operator_agent_verification as fixture

from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

fixture.VerificationAgentMTLSTests.setUpClass()
case = fixture.VerificationAgentMTLSTests()
try:
    case.setUp()
    try:
        c, service = case.case, fixture.service
        n = c.n
        tls = ssl.create_default_context(cafile=str(n.certs / "ca.pem"))
        tls.load_cert_chain(n.certs / "client.pem", n.certs / "client.key")
        config = dict(
            site_id=fixture.native.SITE,
            instance_id=n.engine.instance_id,
            bundle_manifest_sha256=n.bundle_digest,
            verify=tls,
            timeout=360,
        )
        bindings = dict(
            intent=c.intent, chart_artifact_id=c.chart_id, chart_artifact_digest=c.tree_body["chartArtifactDigest"]
        )
        query = {k: v for k, v in case.body.items() if k in service.BASE}
        with OperatorAgentClient(n.url, **config) as sdk:
            try:
                sdk.install_plan_verification(c.job, body=query, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_VERIFICATION_INCOMPLETE" and not error.ambiguous
            else:
                raise AssertionError("Absent verification accepted")
        handler, discarded = n.server.RequestHandlerClass, []
        reply = handler._reply

        def lose(connection, status, body):
            if status == 200 and isinstance(body, dict) and "verificationDigest" in body:
                discarded.append(body["artifactId"])
                connection.close_connection = True
                connection.connection.shutdown(socket.SHUT_RDWR)
                connection.connection.close()
                return
            return reply(connection, status, body)

        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(handler, "_reply", side_effect=lose, autospec=True),
        ):
            try:
                sdk.prepare_install_plan_verification(c.job, body=case.body, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
            else:
                raise AssertionError("Lost reply did not preserve ambiguity")
        assert discarded == [case.body["artifactId"]]
        root = n.engine.install_render_verifications / case.body["artifactId"]
        attempts = sorted(p.name for p in (root / "work").iterdir())
        assert len(attempts) == 1 and (root / "index.json").is_file()
        reads = len(c.kube.flow.http.requests)
        assert reads > 0 and all(peer == "client" for peer in c.kube.peers)
        assert all(method == "GET" for method, _ in c.kube.flow.http.requests)
        endpoint = n.url
        c.restart()
        assert endpoint == n.url and n.engine.instance_id == config["instance_id"]
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(
                service.verifications.authenticated,
                "revalidate",
                side_effect=AssertionError("Historical recovery recollected"),
            ),
        ):
            result = sdk.install_plan_verification(c.job, body=query, **bindings)
            assert sdk.prepare_install_plan_verification(c.job, body=case.body, **bindings) == result
            assert result["contextDigest"] == case.body["contextDigest"]
            assert result["continuationAuthorized"] is False
            assert fixture.native.PRIVATE not in json.dumps(result) and str(n.root) not in json.dumps(result)
            index = fixture.agent.read_private(root / "index.json")["body"]
            assert result["artifactDigest"] == service.verifications._hash(index)
        assert len(c.kube.flow.http.requests) == reads
        assert sorted(p.name for p in (root / "work").iterdir()) == attempts
        case.unchanged()
    finally:
        case.doCleanups()
finally:
    fixture.VerificationAgentMTLSTests.tearDownClass()
