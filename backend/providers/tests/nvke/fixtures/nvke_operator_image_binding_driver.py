import faulthandler
import json
import socket
import ssl
import sys
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])

import test_operator_agent_workloads as fixture

from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient


def phase(name):
    print("SDK_NATIVE_IMAGE_BINDING_PHASE " + name, flush=True)
    faulthandler.dump_traceback_later(600, repeat=False)


phase("fixture-class-setup")
fixture.WorkloadAgentMTLSTests.setUpClass()
case = fixture.WorkloadAgentMTLSTests()
try:
    phase("fixture-setup")
    case.setUp()
    try:
        c, service, image_service = case.c, fixture.service, fixture.image_service
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
            intent=c.intent,
            chart_artifact_id=c.chart_id,
            chart_artifact_digest=c.tree_body["chartArtifactDigest"],
        )
        inventory_query = {k: v for k, v in case.workload_body.items() if k in service.BASE}

        def lose_reply(field):
            handler, discarded = n.server.RequestHandlerClass, []
            reply = handler._reply

            def lose(connection, status, body):
                if status == 200 and isinstance(body, dict) and field in body:
                    discarded.append(body)
                    connection.close_connection = True
                    connection.connection.shutdown(socket.SHUT_RDWR)
                    connection.connection.close()
                    return
                return reply(connection, status, body)

            return handler, discarded, lose

        phase("workload-lost-reply")
        handler, discarded, lose = lose_reply("workloadDigest")
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(handler, "_reply", side_effect=lose, autospec=True),
        ):
            try:
                sdk.prepare_install_plan_workload(c.job, body=case.workload_body, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
            else:
                raise AssertionError("Lost inventory reply accepted")
        assert len(discarded) == 1
        phase("workload-cold-recovery")
        c.restart()
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(service.workloads.inventory, "from_context", side_effect=AssertionError("Cold rerender")),
            patch.object(service.workloads.inventory, "capture_native", side_effect=AssertionError("Cold collection")),
        ):
            workload = sdk.install_plan_workload(c.job, body=inventory_query, **bindings)
            assert workload == discarded[0]
            assert sdk.prepare_install_plan_workload(c.job, body=case.workload_body, **bindings) == workload
        image = {
            "format": 1,
            "artifactId": str(uuid4()),
            **{key: case.workload_body[key] for key in ("planId", "planDigest", "treeId", "treeDigest")},
            "reference": case.registry.reference,
            "policy": "reviewed",
            "workloadId": workload["artifactId"],
            "workloadDigest": workload["workloadDigest"],
            "maximumBytes": "4194304",
            "maximumCalls": 64,
            "timeoutSeconds": 300,
        }
        image_query = {k: v for k, v in image.items() if k in image_service.BASE | image_service.WORKLOAD}
        phase("image-lost-reply")
        handler, discarded, lose = lose_reply("rootDigest")
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(handler, "_reply", side_effect=lose, autospec=True),
        ):
            try:
                sdk.prepare_install_plan_image(c.job, body=image, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
            else:
                raise AssertionError("Lost registry reply accepted")
        assert len(discarded) == 1
        reads = len(case.registry.requests)
        phase("image-cold-recovery")
        c.restart()
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(image_service.images.registry, "capture", side_effect=AssertionError("Cold acquisition")),
        ):
            result = sdk.install_plan_image(c.job, body=image_query, **bindings)
            assert result == discarded[0]
            assert sdk.prepare_install_plan_image(c.job, body=image, **bindings) == result
            assert result["rootDigest"] == case.registry.descriptor["digest"]
            assert result["workloadDigest"] == workload["workloadDigest"]
        assert len(case.registry.requests) == reads
        binding = {
            "format": 1,
            "artifactId": str(uuid4()),
            **{
                key: image[key]
                for key in ("planId", "planDigest", "treeId", "treeDigest", "workloadId", "workloadDigest")
            },
            "imageBindings": {
                case.registry.reference: {
                    "sourceKind": "registry",
                    "artifactId": result["artifactId"],
                    "artifactDigest": result["artifactDigest"],
                }
            },
            "maximumImageBytes": "8388608",
            "maximumBytes": "1048576",
            "timeoutSeconds": 300,
        }
        phase("binding-lost-reply")
        handler, discarded, lose = lose_reply("bindingDigest")
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(handler, "_reply", side_effect=lose, autospec=True),
        ):
            try:
                sdk.prepare_install_plan_image_binding(c.job, body=binding, **bindings)
            except NvkeError as error:
                assert error.code == "OPERATOR_UNAVAILABLE" and error.ambiguous and not error.retryable
            else:
                raise AssertionError("Lost binding reply accepted")
        assert len(discarded) == 1
        phase("binding-cold-recovery")
        c.restart()
        with (
            OperatorAgentClient(n.url, **config) as sdk,
            patch.object(image_service.images.registry, "capture", side_effect=AssertionError("Cold acquisition")),
            patch.object(service.workloads.inventory, "from_context", side_effect=AssertionError("Cold rerender")),
        ):
            binding_query = {
                k: v
                for k, v in binding.items()
                if k not in {"imageBindings", "maximumImageBytes", "maximumBytes", "timeoutSeconds"}
            }
            bound = sdk.install_plan_image_binding(c.job, body=binding_query, **bindings)
            assert bound == discarded[0]
            assert sdk.prepare_install_plan_image_binding(c.job, body=binding, **bindings) == bound
        assert len(case.registry.requests) == reads
        assert not bound["continuationAuthorized"]
        assert bound["imageUseCount"] == workload["imageUseCount"]
        assert bound["imageCount"] == 1
        assert case.registry.reference not in json.dumps(bound)
        assert fixture.native.PRIVATE not in json.dumps(bound)
        assert len(list((n.engine.install_image_bindings / bound["artifactId"] / "work").iterdir())) == 1
        assert not result["continuationAuthorized"] and not workload["continuationAuthorized"]
        assert fixture.native.PRIVATE not in json.dumps([workload, result])
        assert str(n.root) not in json.dumps([workload, result])
        assert case.registry.reference not in json.dumps([workload, result])
        for root, artifact in (
            (n.engine.install_workload_inventories, workload["artifactId"]),
            (n.engine.install_registry_images, result["artifactId"]),
        ):
            assert len(list((root / artifact / "work").iterdir())) == 1
        case.unchanged()
        print("imageBindingSDKNativeLostReplyAndColdRecovery PASS", flush=True)
    finally:
        phase("fixture-cleanup")
        case.doCleanups()
finally:
    try:
        phase("fixture-class-cleanup")
        fixture.WorkloadAgentMTLSTests.tearDownClass()
    finally:
        faulthandler.cancel_dump_traceback_later()
