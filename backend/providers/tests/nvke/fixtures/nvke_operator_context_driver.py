import hashlib
import json
import re
import socket
import ssl
import sys
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])

import test_operator_agent_context as fixture

from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

fixture.ContextMTLSTests.setUpClass()
case = fixture.ContextMTLSTests()
try:
    case.setUp()
    try:
        case.sources()
        n, service = case.n, fixture.service
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
            intent=case.intent,
            chart_artifact_id=case.chart_id,
            chart_artifact_digest=case.tree_body["chartArtifactDigest"],
        )
        query = {k: v for k, v in case.context_body.items() if k in service.BASE}
        handler = n.server.RequestHandlerClass
        reply = handler._reply

        def failure_metadata(connection, status, body):
            if status >= 400 and isinstance(body, dict):
                code = body.get("errorCode")
                print(
                    "AGENT_FAILURE_METADATA",
                    json.dumps(
                        {
                            "status": status,
                            "code": code
                            if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,96}", code)
                            else "UNCLASSIFIED",
                        }
                    ),
                    flush=True,
                )
            return reply(connection, status, body)

        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(handler, "_reply", side_effect=failure_metadata, autospec=True),
        ):
            try:
                sdk.install_plan_context(case.job, body=query, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_CONTEXT_INCOMPLETE" and not error.ambiguous
            else:
                raise AssertionError("Missing context was accepted")
        handler = n.server.RequestHandlerClass
        reply = handler._reply
        discarded = []

        def lose(connection, status, body):
            if status == 200 and isinstance(body, dict) and "contextDigest" in body:
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
                sdk.prepare_install_plan_context(case.job, body=case.context_body, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
            else:
                raise AssertionError("Lost reply did not preserve ambiguity")
        assert discarded == [case.context_body["artifactId"]]
        root = n.engine.install_render_contexts / case.context_body["artifactId"]
        attempts = sorted(p.name for p in (root / "work").iterdir())
        assert len(attempts) == 1 and (root / "index.json").is_file()
        reads = len(case.kube.flow.http.requests)
        assert reads > 0 and all(p == "client" for p in case.kube.peers)
        assert all(method == "GET" for method, _ in case.kube.flow.http.requests)
        endpoint = n.url
        case.restart()
        assert n.url == endpoint and n.engine.instance_id == config["instance_id"]
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(
                service.authenticated.transport,
                "FrozenKubernetesRead",
                side_effect=AssertionError("Offline recovery opened transport"),
            ),
            patch.object(
                service.authenticated.executor,
                "FrozenHelper",
                side_effect=AssertionError("Offline recovery opened helper"),
            ),
        ):
            result = sdk.install_plan_context(case.job, body=query, **bindings)
            assert sdk.prepare_install_plan_context(case.job, body=case.context_body, **bindings) == result
            assert result["ordinal"] == 2 and result["documentCount"] == 1
            assert result["continuationAuthorized"] is False
            assert fixture.native.PRIVATE not in json.dumps(result) and str(n.root) not in json.dumps(result)
            index = fixture.agent.read_private(root / "index.json")["body"]
            work = root / "work" / index["attemptId"]
            assert result["captureDigest"] == hashlib.sha256((work / "capture.json").read_bytes()).hexdigest()
            assert result["frozenChartSha256"] == hashlib.sha256((work / "frozen.tgz").read_bytes()).hexdigest()
        assert len(case.kube.flow.http.requests) == reads
        assert sorted(p.name for p in (root / "work").iterdir()) == attempts
        storage = service.preparation.storage
        assert (n.engine._job(case.job) / "state.json").read_bytes() == case.original
        assert (n.engine.workspace / "management-install-state.json").read_bytes() == case.guard
        assert storage._scan(n.engine.workspace / "runs", 67108864, private=True) == case.native_before
        assert storage._scan(n.engine._job(case.job), 67108864, private=True) == case.job_before
        for name, folder in (
            ("plans", n.engine.install_plans.root),
            ("charts", n.engine.install_chart_artifacts),
            ("trees", n.engine.install_execution_trees),
        ):
            assert storage._scan(folder, 1073741824) == case.parents_before[name]
        assert list(n.engine.install_plans.links.iterdir()) == []
        assert list(n.engine.install_render_work.iterdir()) == []
    finally:
        case.doCleanups()
finally:
    fixture.ContextMTLSTests.tearDownClass()
