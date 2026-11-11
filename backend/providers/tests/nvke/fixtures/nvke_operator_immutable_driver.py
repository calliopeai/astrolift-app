import json
import socket
import ssl
import sys
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])

import test_operator_agent_immutable_execution as fixture

from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

fixture.ImmutableAgentMTLSTests.setUpClass()
case = fixture.ImmutableAgentMTLSTests()
try:
    case.setUp()
    try:
        c, payloads, executions = case.c, fixture.payloads, fixture.executions
        base, _image_body, binding_body = case.binding()
        n = c.n
        tls = ssl.create_default_context(cafile=str(n.certs / "ca.pem"))
        tls.load_cert_chain(n.certs / "client.pem", n.certs / "client.key")
        config = dict(
            site_id=n.engine.site_id,
            instance_id=n.engine.instance_id,
            bundle_manifest_sha256=n.bundle_digest,
            verify=tls,
            timeout=360,
        )
        source = dict(
            intent=c.intent,
            chart_artifact_id=c.chart_id,
            chart_artifact_digest=c.tree_body["chartArtifactDigest"],
            workload_id=binding_body["workloadId"],
            workload_digest=binding_body["workloadDigest"],
        )
        parent = n.engine.install_plans.load(base["planId"], expected_digest=base["planDigest"])
        phases = parent["plan"]["items"]
        selected = {}

        def dropped(field):
            handler, discarded = n.server.RequestHandlerClass, []
            reply = handler._reply

            def lose(connection, status, response):
                if status == 200 and isinstance(response, dict) and field in response:
                    discarded.append(response)
                    connection.close_connection = True
                    connection.connection.shutdown(socket.SHUT_RDWR)
                    connection.connection.close()
                    return
                return reply(connection, status, response)

            return handler, discarded, lose

        reads = len(case.registry.requests)
        for number, phase in enumerate(phases):
            body = {
                "format": 1,
                "artifactId": str(uuid4()),
                **base,
                "item": phase["name"],
                "maximumBytes": "1048576",
                "maximumResources": 100,
                "timeoutSeconds": 300,
            }
            if number == 0:
                handler, discarded, lose = dropped("payloadDigest")
                with (
                    OperatorAgentClient(n.url, **config) as sdk,
                    patch.object(handler, "_reply", side_effect=lose, autospec=True),
                ):
                    try:
                        sdk.prepare_install_plan_image_payload(c.job, body=body, **source)
                    except NvkeError as error:
                        assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
                    else:
                        raise AssertionError("Lost payload response accepted")
                assert len(discarded) == 1
                result = discarded[0]
                c.restart()
            else:
                with OperatorAgentClient(n.url, **config) as sdk:
                    result = sdk.prepare_install_plan_image_payload(c.job, body=body, **source)
            query = {k: v for k, v in body.items() if k in payloads.BASE}
            with (
                OperatorAgentClient(n.url, **config) as sdk,
                patch.object(
                    payloads.store.rewrite, "materialize", side_effect=AssertionError("cold SDK payload rewrite")
                ),
            ):
                assert sdk.install_plan_image_payload(c.job, body=query, **source) == result
                assert sdk.prepare_install_plan_image_payload(c.job, body=body, **source) == result
            assert result["continuationAuthorized"] is False
            selected[phase["name"]] = {"artifactId": result["artifactId"], "artifactDigest": result["artifactDigest"]}
        body = {
            "format": 1,
            "artifactId": str(uuid4()),
            **base,
            "payloadBindings": selected,
            "maximumBytes": "16777216",
            "timeoutSeconds": 300,
        }
        handler, discarded, lose = dropped("executionDigest")
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(handler, "_reply", side_effect=lose, autospec=True),
        ):
            try:
                sdk.prepare_install_plan_immutable_execution_tree(c.job, body=body, **source)
            except NvkeError as error:
                assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
            else:
                raise AssertionError("Lost immutable tree response accepted")
        assert len(discarded) == 1
        result = discarded[0]
        c.restart()
        query = {k: v for k, v in body.items() if k in executions.BASE}
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(executions.store.execution, "assemble", side_effect=AssertionError("cold SDK tree assembly")),
            patch.object(payloads.store.rewrite, "materialize", side_effect=AssertionError("cold SDK phase rewrite")),
        ):
            assert (
                sdk.install_plan_immutable_execution_tree(c.job, body=query, payload_bindings=selected, **source)
                == result
            )
            assert sdk.prepare_install_plan_immutable_execution_tree(c.job, body=body, **source) == result
        assert result["continuationAuthorized"] is False
        assert result["phaseCount"] == sum(p["proposedAction"] == "execute" for p in phases)
        assert len(case.registry.requests) == reads
        for private in (case.registry.reference, "treeLocation", "arguments", "payloadBindings"):
            assert private not in json.dumps(result)
        case.unchanged()
        print(
            json.dumps(
                {
                    "sdk": True,
                    "mutualTls": True,
                    "phaseCount": result["phaseCount"],
                    "skippedPhaseCount": result["skippedPhaseCount"],
                    "coldRecovery": True,
                    "originalSourceUnchanged": True,
                    "continuationAuthorized": False,
                }
            ),
            flush=True,
        )
    finally:
        case.doCleanups()
finally:
    fixture.ImmutableAgentMTLSTests.tearDownClass()
