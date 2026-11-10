import json
import socket
import ssl
import sys
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])

import test_operator_agent_local_images as fixture

from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

fixture.LocalImageAgentMTLSTests.setUpClass()
case = fixture.LocalImageAgentMTLSTests()
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
        query = {k: v for k, v in case.body.items() if k in service.LOCAL_BASE}
        with OperatorAgentClient(n.url, **config) as sdk:
            try:
                sdk.install_plan_image(c.job, body=query, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_IMAGE_INCOMPLETE" and not error.ambiguous
            else:
                raise AssertionError("Absent image accepted")
        handler, discarded = n.server.RequestHandlerClass, []
        reply = handler._reply

        def lose(connection, status, body):
            if status == 200 and isinstance(body, dict) and "rootDigest" in body:
                discarded.append(body["artifactId"])
                connection.close_connection = True
                connection.connection.shutdown(socket.SHUT_RDWR)
                connection.connection.close()
                return
            return reply(connection, status, body)

        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(handler, "_reply", side_effect=lose, autospec=True),
            patch.object(
                service.images.registry, "capture", side_effect=AssertionError("Local capture contacted registry")
            ),
        ):
            try:
                sdk.prepare_install_plan_image(c.job, body=case.body, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
            else:
                raise AssertionError("Lost reply did not preserve ambiguity")
        assert discarded == [case.body["artifactId"]]
        root = n.engine.install_registry_images / case.body["artifactId"]
        attempts = sorted(p.name for p in (root / "work").iterdir())
        assert len(attempts) == 1 and (root / "index.json").is_file()
        assert not hasattr(n.engine, "image_profile") or n.engine.image_profile is None
        endpoint = n.url
        c.restart()
        assert endpoint == n.url and n.engine.instance_id == config["instance_id"]
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(
                service.images.registry,
                "capture",
                side_effect=AssertionError("Historical recovery recollected"),
            ),
        ):
            result = sdk.install_plan_image(c.job, body=query, **bindings)
            assert sdk.prepare_install_plan_image(c.job, body=case.body, **bindings) == result
            assert result["rootDigest"] == case.descriptor["digest"]
            assert result["sourceKind"] == "local"
            assert result["continuationAuthorized"] is False
            assert fixture.native.PRIVATE not in json.dumps(result) and str(n.root) not in json.dumps(result)
            index = fixture.registry_tests.agent.read_private(root / "index.json")["body"]
            assert index["continuationAuthorized"] is False
        assert sorted(p.name for p in (root / "work").iterdir()) == attempts
        case.unchanged()
    finally:
        case.doCleanups()
finally:
    fixture.LocalImageAgentMTLSTests.tearDownClass()
