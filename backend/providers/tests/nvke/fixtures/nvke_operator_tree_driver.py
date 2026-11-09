import json
import os
import shutil
import ssl
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4

import httpx

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
import test_operator_agent as native

from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

storage = native.agent.management_plan_preparation.storage
native.NativeAgentTests.setUpClass()
case = native.NativeAgentTests()
try:
    case.setUp()
    try:
        job, intent, body = case.plan_fixture()
        body["overrides"] = {
            "options": {"config": "input:config.json"},
            "files": {"config.json": "{}", "secret.txt": "PRIVATE_TREE_SDK_CONTENT"},
        }
        original = storage._content(storage._scan(case.engine._job(job), 67108864, private=True))
        guard = (case.engine.workspace / "management-install-state.json").read_bytes()
        runs = storage._content(storage._scan(case.engine.workspace / "runs", 67108864, private=True))
        deployment = native.agent.management_plan_preparation._deployment

        def owned_deployment(engine, argv, inputs, destination, budget):
            provenance = deployment(engine, argv, inputs, destination, budget)
            (destination / "nvke-deploy.yaml").write_text(
                "items:\n"
                "  - {name: nvke-base-config, type: template}\n"
                "  - {name: spire-tpm-pubhashes, type: manifest}\n"
                "  - {name: nktp, type: helm}\n"
            )
            (destination / "images.txt").unlink(missing_ok=True)
            chart = destination / "owned-local-chart"
            chart.mkdir(mode=0o700)
            (chart / "templates").mkdir(mode=0o700)
            native.agent.private_write(chart / "Chart.yaml", b"apiVersion: v2\nname: nktp\nversion: 1.0.0\n")
            native.agent.private_write(
                chart / "templates/config.yaml", b"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: owned\n"
            )
            (destination / "nktp/release.yaml").write_text(
                "chart:\n  name: nktp\n  path: owned-local-chart\n  setFile:\n    - password="
                + str(inputs / "secret.txt")
                + "\nrelease:\n  name: nktp\n  namespace: nktp\nvalues:\n  - values.yaml\n"
            )
            return provenance

        context = ssl.create_default_context(cafile=str(case.certs / "ca.pem"))
        context.load_cert_chain(case.certs / "client.pem", case.certs / "client.key")
        config = dict(
            site_id=native.SITE,
            instance_id=case.engine.instance_id,
            bundle_manifest_sha256=case.bundle_digest,
            verify=context,
            timeout=300,
        )
        with OperatorAgentClient(case.url, **config) as sdk:
            with (
                patch.object(native.agent.management_plan_preparation, "_deployment", owned_deployment),
                patch.object(
                    native.agent.management_plan_preparation.qualification, "kubectl", side_effect=case.plan_kubectl
                ),
            ):
                parent = sdk.prepare_install_plan(job, intent=intent, body=body)
            plan_digest = parent["plan"]["planDigest"]
            plan_before = storage._content(
                storage._scan(case.engine.install_plans._plan(body["planId"]), 67108864, private=True)
            )
            chart_id = str(uuid4())
            original_which = shutil.which

            def selected_tool(name):
                return {
                    "helm": os.environ["NVKE_TEST_HELM4"],
                    "astro-nvke-chart-helper": os.environ["NVKE_TEST_CHART_HELPER"],
                }.get(name) or original_which(name)

            with patch.object(native.agent.management_plan_chart_service.shutil, "which", selected_tool):
                publication = sdk.prepare_install_plan_charts(
                    job,
                    intent=intent,
                    body={
                        "format": 1,
                        "artifactId": chart_id,
                        "planId": body["planId"],
                        "planDigest": plan_digest,
                        "maximumBytes": "16777216",
                        "maximumExpandedBytes": "16777216",
                    },
                )
            chart_before = storage._content(storage._scan(case.engine.install_chart_artifacts, 1073741824))
            tree_id = str(uuid4())
            selected = dict(
                intent=intent,
                plan_id=body["planId"],
                plan_digest=plan_digest,
                chart_artifact_id=chart_id,
                chart_artifact_digest=publication["artifactDigest"],
                artifact_id=tree_id,
            )
            try:
                sdk.install_plan_tree(job, **selected)
            except NvkeError as error:
                assert error.code == "OPERATOR_TREE_INCOMPLETE" and not error.ambiguous
            else:
                raise AssertionError("Missing tree must remain incomplete")
        requests = []

        class LostReply(httpx.HTTPTransport):
            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "POST" and request.url.path.endswith("/install-plan-tree"):
                    assert response.status_code == 200
                    requests.append(request.method)
                    response.read()
                    response.close()
                    raise httpx.ReadError("Deliberately discarded tree publication", request=request)
                return response

        private_request = {
            "format": 1,
            "artifactId": tree_id,
            "planId": body["planId"],
            "planDigest": plan_digest,
            "chartArtifactId": chart_id,
            "chartArtifactDigest": publication["artifactDigest"],
            "maximumBytes": "16777216",
        }
        with OperatorAgentClient(case.url, **config, transport=LostReply(verify=context)) as sdk:
            try:
                sdk.prepare_install_plan_tree(job, intent=intent, body=private_request)
            except NvkeError as error:
                assert error.ambiguous and not error.retryable
            else:
                raise AssertionError("A discarded reply must not trigger replay")
        assert requests == ["POST"]
        artifact = case.engine.install_execution_trees / tree_id
        assert (artifact / "index.json").is_file()
        attempts = sorted(path.name for path in (artifact / "work").iterdir())
        endpoint, port = case.url, case.server.server_port
        case.server.shutdown()
        case.server.server_close()
        case.thread.join(timeout=5)
        case.engine.close()
        case.engine = case.make_engine()
        case.start_server(port=port)
        assert case.url == endpoint and case.engine.instance_id == config["instance_id"]
        with OperatorAgentClient(case.url, **config) as sdk:
            with (
                patch.object(subprocess, "run", side_effect=AssertionError("Tree recovery must not spawn")),
                patch.object(shutil, "which", side_effect=AssertionError("Tree recovery must not select tools")),
            ):
                recovered = sdk.install_plan_tree(job, **selected)
                assert sdk.prepare_install_plan_tree(job, intent=intent, body=private_request) == recovered
            assert recovered["continuationAuthorized"] is False and recovered["fileCount"] > 0
            assert isinstance(recovered["bytes"], str) and int(recovered["bytes"]) >= int(publication["bytes"])
            assert "PRIVATE_TREE_SDK_CONTENT" not in json.dumps(recovered)
            assert str(case.engine.root) not in json.dumps(recovered)
            with patch.dict(os.environ, {"NVKECTL_TREE_SDK_SOURCE_DRIFT": "changed"}):
                try:
                    sdk.install_plan_tree(job, **selected)
                except NvkeError as error:
                    assert error.code == "SOURCE_CHANGED"
                else:
                    raise AssertionError("Changed source was accepted")
        assert sorted(path.name for path in (artifact / "work").iterdir()) == attempts
        assert storage._content(storage._scan(case.engine._job(job), 67108864, private=True)) == original
        assert storage._content(storage._scan(case.engine.workspace / "runs", 67108864, private=True)) == runs
        assert (case.engine.workspace / "management-install-state.json").read_bytes() == guard
        assert (
            storage._content(storage._scan(case.engine.install_plans._plan(body["planId"]), 67108864, private=True))
            == plan_before
        )
        assert storage._content(storage._scan(case.engine.install_chart_artifacts, 1073741824)) == chart_before
        assert list(case.engine.install_plans.links.iterdir()) == []
    finally:
        case.tearDown()
finally:
    native.NativeAgentTests.tearDownClass()
