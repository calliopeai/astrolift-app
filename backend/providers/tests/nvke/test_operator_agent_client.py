from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient, verify_contract
from nvke.operator_contract import CATALOG, REVISION, canonical

SITE, INSTANCE, JOB = str(uuid4()), str(uuid4()), str(uuid4())
INTENT = "b" * 64
DIGEST, PRIVATE = "a" * 64, "PRIVATE_OPERATOR_INPUT"


def payload(site=SITE):
    return {
        "format": 1,
        "revision": REVISION,
        "siteId": site,
        "command": "generate.sitespec",
        "positionals": ["sdk-site"],
        "options": {"nodes": "input:nodes.csv"},
        "files": {"nodes.csv": "name,ip\ncp-1,192.0.2.10\n"},
    }


def receipt():
    return {
        "format": 2,
        "intent": INTENT,
        "revision": REVISION,
        "siteId": SITE,
        "instanceId": INSTANCE,
        "id": JOB,
        "command": "generate.sitespec",
        "state": "completed",
        "effectful": False,
        "artifacts": [],
        "cancellationRequested": False,
        "nativeExitCode": 0,
    }


def mock_client(data, *, status=200, checkpoint=None):
    def respond(request):
        assert request.headers["X-Astro-Nvke-Agent-Instance"] == INSTANCE
        return httpx.Response(status, json=data)

    return OperatorAgentClient(
        "https://agent.example",
        site_id=SITE,
        instance_id=INSTANCE,
        bundle_manifest_sha256=DIGEST,
        verify=ssl.create_default_context(),
        transport=httpx.MockTransport(respond),
        before_request=checkpoint,
    )


def test_shared_operator_contract_provenance():
    verify_contract()
    assert len(CATALOG) == 17


@pytest.mark.parametrize(
    "changed",
    [
        {"siteId": str(uuid4())},
        {"instanceId": str(uuid4())},
        {"id": str(uuid4())},
        {"revision": "b" * 40},
        {"intent": "c" * 64},
        {"command": "bootstrap.site"},
        {"state": "invented"},
        {"effectful": 0},
        {"nativeExitCode": False},
        {"argv": [PRIVATE]},
        {"artifacts": [{"name": "secret", "size": "-1", "sha256": DIGEST}]},
    ],
)
def test_foreign_or_unstructured_receipts_never_enter_private_results(changed):
    with mock_client({**receipt(), **changed}) as client, pytest.raises(NvkeError) as error:
        client.observe(JOB, command="generate.sitespec", intent=INTENT)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and PRIVATE not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://agent.example",
        "https://user:PRIVATE@agent.example",
        "https://agent.example/v1",
        "https://agent.example/?PRIVATE=1",
    ],
)
def test_explicit_https_base_endpoint_is_required(endpoint):
    with pytest.raises((NvkeError, ValueError)):
        OperatorAgentClient(endpoint, site_id=SITE, verify=ssl.create_default_context())


def test_transport_uncertainty_and_refusal_are_safe_and_distinct():
    def disconnect(_request):
        raise httpx.ReadError(PRIVATE)

    with OperatorAgentClient(
        "https://agent.example",
        site_id=SITE,
        instance_id=INSTANCE,
        bundle_manifest_sha256=DIGEST,
        verify=ssl.create_default_context(),
        transport=httpx.MockTransport(disconnect),
    ) as client:
        with pytest.raises(NvkeError) as error:
            client.submit(JOB, payload(), intent=INTENT)
        assert error.value.ambiguous and PRIVATE not in str(error.value)
    for peer, expected in [
        ("AGENT_BUSY", "OPERATOR_BUSY"),
        ("JOB_NOT_FOUND", "OPERATOR_JOB_NOT_FOUND"),
        (PRIVATE, "OPERATOR_REQUEST_REFUSED"),
    ]:
        with mock_client({"errorCode": peer}, status=503) as client, pytest.raises(NvkeError) as error:
            client.submit(JOB, payload(), intent=INTENT)
        assert error.value.code == expected and PRIVATE not in str(error.value)


def test_authority_is_rechecked_after_the_peer_read_before_receipt_exposure():
    calls = []

    def checkpoint():
        calls.append(True)
        if len(calls) == 2:
            raise NvkeError("AUTHORITY_REVOKED", "revoked")

    with mock_client(receipt(), checkpoint=checkpoint) as client, pytest.raises(NvkeError) as error:
        client.observe(JOB, command="generate.sitespec", intent=INTENT)
    assert error.value.code == "AUTHORITY_REVOKED" and len(calls) == 2


def write_certificates(root):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic operator SDK CA")])
    now = datetime.now(UTC)

    def build(subject, key, ca=False, server=False):
        value = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        )
        if not ca:
            from ipaddress import ip_address

            value = value.add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=False,
            )
            if server:
                value = value.add_extension(
                    x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False
                )
        return value.sign(ca_key, hashes.SHA256())

    (root / "ca.pem").write_bytes(build(ca_name, ca_key, ca=True).public_bytes(serialization.Encoding.PEM))
    for name in ("server", "client"):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = build(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]), key, server=name == "server")
        (root / (name + ".pem")).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        with os.fdopen(os.open(root / (name + ".key"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(
                key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
            )


@pytest.fixture
def real_operator_agent(tmp_path):
    yield from operator_agent(tmp_path)


def operator_agent(tmp_path, *, site_id=None):
    selected, selected_bundle = os.getenv("NVKE_TEST_AGENT_OPSCODE"), os.getenv("NVKE_TEST_AGENT_BUNDLE")
    if not selected or not selected_bundle:
        pytest.skip("select the explicit owned Opscode source and private native bundle")
    source, bundle = Path(selected).resolve(), Path(selected_bundle).resolve()
    revision = "aeb0e1b88abe2f1c51c4e16cd7cac34ecf346fee"
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
        "qualify_bootstrap.py",
        "tooling.py",
    ):
        relative = "kubernetes/nvke/" + name
        assert (source / relative).read_bytes() == subprocess.check_output(
            ["git", "show", revision + ":" + relative], cwd=source
        )
    root = tmp_path.resolve()
    write_certificates(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    digest = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    leaf = hashlib.sha256(ssl.PEM_cert_to_DER_cert((root / "client.pem").read_text())).hexdigest()
    site = site_id if site_id is not None else str(uuid4())
    command = [
        sys.executable,
        "-I",
        str(source / "kubernetes/nvke/operator_agent.py"),
        "--site-id",
        site,
        "--bundle",
        str(bundle),
        "--bundle-manifest-sha256",
        digest,
        "--state-dir",
        str(root / "state"),
        "--server-cert",
        str(root / "server.pem"),
        "--server-key",
        str(root / "server.key"),
        "--client-ca",
        str(root / "ca.pem"),
        "--client-cert-sha256",
        leaf,
        "--port",
        str(port),
    ]
    with (root / "agent.stdout").open("wb") as out, (root / "agent.stderr").open("wb") as err:
        process = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
    client = None
    try:
        deadline = time.monotonic() + 10
        identity_path = root / "state/identity.json"
        while not identity_path.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        assert process.poll() is None and identity_path.exists(), "owned test agent did not start"
        instance = json.loads(identity_path.read_bytes())["instanceId"]
        context = ssl.create_default_context(cafile=str(root / "ca.pem"))
        context.load_cert_chain(root / "client.pem", root / "client.key")
        client = OperatorAgentClient(
            f"https://127.0.0.1:{port}",
            site_id=site,
            instance_id=instance,
            bundle_manifest_sha256=digest,
            verify=context,
        )
        while time.monotonic() < deadline:
            try:
                client.identity()
                break
            except NvkeError:
                if process.poll() is not None:
                    raise
                time.sleep(0.01)
        else:
            pytest.fail("owned operator agent did not become ready")
        yield SimpleNamespace(client=client, site=site, instance=instance, digest=digest, root=root)
    finally:
        if client is not None:
            client.close()
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_real_tls_client_runs_native_utilities_and_downloads_guarded_artifacts(real_operator_agent, caplog):
    state = real_operator_agent
    job = str(uuid4())
    state.client.submit(job, payload(state.site), intent=INTENT)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        row = state.client.observe(job, command="generate.sitespec", intent=INTENT)
        if row["state"] == "completed":
            break
        time.sleep(0.02)
    assert row["state"] == "completed"
    chunk = state.client.artifact(job, command="generate.sitespec", name="stdout", intent=INTENT)
    assert b"sdk-site" in base64.b64decode(chunk["dataBase64"], validate=True)
    assert state.client.submit(job, payload(state.site), intent=INTENT)["id"] == job
    assert len(list((state.root / "state/workspace/runs").glob("*/started.json"))) == 1
    assert job not in caplog.text and "nodes.csv" not in caplog.text


def test_probe_clients_cannot_submit_and_wrong_instance_cannot_retarget(real_operator_agent):
    state = real_operator_agent
    instance = state.client.instance_id
    state.client.instance_id = None
    with pytest.raises(NvkeError) as probe:
        state.client.submit(str(uuid4()), payload(state.site), intent=INTENT)
    assert probe.value.code == "PRECONDITION"
    state.client.instance_id = instance
    state.client.instance_id = str(uuid4())
    with pytest.raises(NvkeError) as error:
        state.client.submit(str(uuid4()), payload(state.site), intent=INTENT)
    assert error.value.code == "SOURCE_CHANGED" and not list((state.root / "state/jobs").iterdir())


def test_real_agent_refuses_another_intent_on_the_same_job(real_operator_agent):
    state = real_operator_agent
    job = str(uuid4())
    state.client.submit(job, payload(state.site), intent=INTENT)
    with pytest.raises(NvkeError) as conflict:
        state.client.submit(job, payload(state.site), intent="c" * 64)
    assert conflict.value.code == "OPERATOR_JOB_CONFLICT"
    with pytest.raises(NvkeError) as observe:
        state.client.observe(job, command="generate.sitespec", intent="c" * 64)
    assert observe.value.code == "OPERATOR_JOB_CONFLICT"


def test_real_tls_stop_fences_original_intent_without_native_execution(real_operator_agent, caplog):
    state = real_operator_agent
    job = str(uuid4())
    result = state.client.stop_or_fence(job, command="generate.sitespec", intent=INTENT, effectful=False)
    assert result["state"] == "cancelled" and result["errorCode"] == "ORIGINAL_JOB_FENCED"
    assert state.client.observe(job, command="generate.sitespec", intent=INTENT) == result
    assert state.client.stop_or_fence(job, command="generate.sitespec", intent=INTENT, effectful=False) == result
    with pytest.raises(NvkeError) as delayed:
        state.client.submit(job, payload(state.site), intent=INTENT)
    assert delayed.value.code == "OPERATOR_JOB_FENCED" and not delayed.value.ambiguous
    with pytest.raises(NvkeError) as changed:
        state.client.stop_or_fence(job, command="generate.sitespec", intent="c" * 64, effectful=False)
    assert changed.value.code == "OPERATOR_JOB_CONFLICT"
    assert not list((state.root / "state/jobs").iterdir())
    assert not (state.root / "state/workspace/runs").exists()
    assert job not in caplog.text and INTENT not in caplog.text


@pytest.mark.parametrize(
    "capability",
    [
        None,
        {},
        {"format": True, "stopFence": True, "completeAdmissionHistory": True},
        {"format": 1, "stopFence": False, "completeAdmissionHistory": True},
        {"format": 1, "stopFence": True, "completeAdmissionHistory": 1},
        {"format": 2, "stopFence": True, "completeAdmissionHistory": True},
    ],
)
def test_stop_requires_explicit_current_capability_before_any_write(capability):
    calls = []

    def respond(request):
        calls.append(request.method)
        assert request.url.path == "/v1/identity"
        return httpx.Response(
            200,
            json={
                "format": 1,
                "jobFormat": 2,
                "revision": REVISION,
                "siteId": SITE,
                "instanceId": INSTANCE,
                "bundleManifestSha256": DIGEST,
                "commands": CATALOG,
                "jobControl": capability,
            },
        )

    with (
        OperatorAgentClient(
            "https://agent.example",
            site_id=SITE,
            instance_id=INSTANCE,
            bundle_manifest_sha256=DIGEST,
            verify=ssl.create_default_context(),
            transport=httpx.MockTransport(respond),
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.stop_or_fence(JOB, command="generate.sitespec", intent=INTENT, effectful=False)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED" and calls == ["GET"]


def install_review_payload():
    run = str(uuid4())
    value = {
        "source": {
            "format": 1,
            "revision": REVISION,
            "siteId": SITE,
            "instanceId": INSTANCE,
            "bundleManifestSha256": DIGEST,
            "id": JOB,
            "intent": INTENT,
            "command": "management.install",
            "nativeRunId": run,
        },
        "review": {
            "format": 1,
            "scope": "read-only-retained-partial-management-install-review",
            "nativeRevision": REVISION,
            "observedAt": "2026-11-08T21:00:00+00:00",
            "reviewerSha256": "c" * 64,
            "qualificationHelperSha256": "d" * 64,
            "bundle": {
                "format": 1,
                "revision": REVISION,
                "capiVersion": "v1.13.6",
                "binarySha256": "e" * 64,
                "sourceArchiveSha256": "f" * 64,
                "sourceTreeSha256": "a" * 64,
            },
            "originalAttempt": {
                "runId": run,
                "startedAt": "2026-11-08T20:22:39+00:00",
                "argumentsDigest": "a" * 64,
                "phase": "uncertain",
                "exitCode": 1,
            },
            "privateEvidenceSha256": {k: "e" * 64 for k in ("guard", "started", "finished", "stdout", "stderr")},
            "apiReady": True,
            "continuationAuthorized": False,
            "workloads": [
                {
                    "kind": "Deployment",
                    "namespace": "nktp",
                    "name": "lgtm",
                    "generation": 1,
                    "observedGeneration": 1,
                    "desiredReplicas": 1,
                    "readyReplicas": 0,
                    "updatedReplicas": 1,
                    "observation": "requires-review",
                }
            ],
            "limitations": [PRIVATE],
        },
    }
    value["reviewDigest"] = hashlib.sha256(canonical(value)).hexdigest()
    return value


def install_review_client(value, *, capability=None, replacement=False, original_status="uncertain"):
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET"
        if request.url.path == "/v1/identity":
            return httpx.Response(
                200,
                json={
                    "format": 1,
                    "jobFormat": 2,
                    "revision": REVISION,
                    "siteId": SITE,
                    "instanceId": str(uuid4()) if replacement and len(calls) > 1 else INSTANCE,
                    "bundleManifestSha256": DIGEST,
                    "commands": CATALOG,
                    "installReview": capability
                    if capability is not None
                    else {"format": 1, "readOnly": True, "continuation": False},
                },
            )
        assert request.headers["X-Astro-Nvke-Intent"] == INTENT
        if request.url.path.endswith("/install-review"):
            return httpx.Response(200, json=value)
        return httpx.Response(
            200,
            json={
                **receipt(),
                "command": "management.install",
                "state": original_status,
                "effectful": True,
                "nativeExitCode": 0 if original_status == "completed" else 1,
            },
        )

    return OperatorAgentClient(
        "https://agent.example",
        site_id=SITE,
        instance_id=INSTANCE,
        bundle_manifest_sha256=DIGEST,
        verify=ssl.create_default_context(),
        transport=httpx.MockTransport(respond),
    ), calls


def test_install_review_projects_bound_facts_without_private_diagnostics_or_writes():
    value = install_review_payload()
    client, calls = install_review_client(value)
    with client:
        result = client.install_review(JOB, intent=INTENT)
    assert result["source"] == value["source"] and result["reviewDigest"] == value["reviewDigest"]
    assert result["continuationAuthorized"] is False and result["originalAttempt"]["phase"] == "uncertain"
    assert result["workloads"][0]["observation"] == "requires-review"
    assert "limitations" not in result and "bundle" not in result and PRIVATE not in json.dumps(result)
    assert calls == [
        ("GET", "/v1/identity"),
        ("GET", "/v1/jobs/" + JOB),
        ("GET", "/v1/jobs/" + JOB + "/install-review"),
        ("GET", "/v1/identity"),
    ]


@pytest.mark.parametrize(
    "path,value",
    [
        (("source", "siteId"), str(uuid4())),
        (("source", "nativeRunId"), "../other"),
        (("source", "format"), True),
        (("source", "intent"), "f" * 64),
        (("review", "continuationAuthorized"), True),
        (("review", "apiReady"), 1),
        (("review", "originalAttempt", "exitCode"), 0),
        (("review", "originalAttempt", "phase"), "completed"),
        (("review", "originalAttempt", "runId"), str(uuid4())),
        (("review", "observedAt"), "2026-11-08T21:00:00"),
        (("review", "workloads", 0, "generation"), True),
        (("review", "workloads", 0, "observation"), "ready"),
        (("review", "workloads", 0, "name"), PRIVATE + "\n"),
        (("review", "workloads", 0, "readyReplicas"), 2**63),
        (("review", "bundle", "revision"), "b" * 40),
        (("review", "privateEvidenceSha256", "stderr"), PRIVATE),
        (("review", "reviewerSha256"), PRIVATE),
    ],
)
def test_install_review_refuses_changed_or_malformed_sources_without_echoing_private_text(path, value):
    data = install_review_payload()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    data["reviewDigest"] = hashlib.sha256(canonical({"source": data["source"], "review": data["review"]})).hexdigest()
    client, _calls = install_review_client(data)
    with client, pytest.raises(NvkeError) as failure:
        client.install_review(JOB, intent=INTENT)
    assert failure.value.code == "OPERATOR_INVALID_RESPONSE" and PRIVATE not in str(failure.value)


def test_install_review_requires_capability_original_failure_digest_and_stable_identity():
    for changed in ["capability", "successful", "digest", "unknown", "duplicate", "replacement"]:
        data = copy.deepcopy(install_review_payload())
        if changed == "digest":
            data["reviewDigest"] = "0" * 64
        if changed == "unknown":
            data["review"]["privateNativeOutput"] = PRIVATE
        if changed == "duplicate":
            data["review"]["workloads"] *= 2
        if changed in {"unknown", "duplicate"}:
            data["reviewDigest"] = hashlib.sha256(
                canonical({"source": data["source"], "review": data["review"]})
            ).hexdigest()
        client, calls = install_review_client(
            data,
            capability={} if changed == "capability" else None,
            replacement=changed == "replacement",
            original_status="completed" if changed == "successful" else "uncertain",
        )
        with client, pytest.raises(NvkeError) as failure:
            client.install_review(JOB, intent=INTENT)
        assert PRIVATE not in str(failure.value) and all(method == "GET" for method, _path in calls)
        if changed == "capability":
            assert len(calls) == 1 and failure.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
        if changed == "successful":
            assert len(calls) == 2 and failure.value.code == "PRECONDITION"


def test_real_tls_sdk_prepares_recovers_and_pages_sealed_plan_without_dispatch(real_operator_agent):
    source = Path(os.environ["NVKE_TEST_AGENT_OPSCODE"]).resolve()
    revision = "aeb0e1b88abe2f1c51c4e16cd7cac34ecf346fee"
    relative = "kubernetes/nvke/test_operator_agent.py"
    assert (source / relative).read_bytes() == subprocess.check_output(
        ["git", "show", revision + ":" + relative], cwd=source
    )
    driver = r"""
import ssl
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from nvke.operator_agent_client import OperatorAgentClient
from nvke.client import NvkeError
import test_operator_agent as fixture
fixture.NativeAgentTests.setUpClass()
case = fixture.NativeAgentTests()
try:
    case.setUp()
    try:
        job, intent, body = case.plan_fixture()
        original = (case.engine._job(job) / "state.json").read_bytes()
        guard = (case.engine.workspace / "management-install-state.json").read_bytes()
        runs = sorted(p.name for p in (case.engine.workspace / "runs").iterdir())
        context = ssl.create_default_context(cafile=str(case.certs / "ca.pem"))
        context.load_cert_chain(case.certs / "client.pem", case.certs / "client.key")
        with OperatorAgentClient(case.url, site_id=fixture.SITE, instance_id=case.engine.instance_id,
            bundle_manifest_sha256=case.bundle_digest, verify=context) as sdk:
            with patch.object(fixture.agent.management_plan_preparation.qualification,
                "kubectl", side_effect=case.plan_kubectl):
                prepared = sdk.prepare_install_plan(job, intent=intent, body=body)
            assert sdk.prepare_install_plan(job, intent=intent, body=body) == prepared
            assert sdk.install_plan(job, intent=intent, plan_id=body["planId"]) == prepared
            rows, cursor = [], None
            while True:
                page = sdk.install_plan_page(job, intent=intent, plan_id=body["planId"],
                    plan_digest=prepared["plan"]["planDigest"], view="items", limit=3, after=cursor)
                rows.extend(page["items"])
                cursor = page["nextCursor"]
                if cursor is None:
                    break
            assert len(rows) == prepared["itemCount"] == 11
            assert [r["ordinal"] for r in rows] == list(range(11))
            workloads = sdk.install_plan_page(job, intent=intent, plan_id=body["planId"],
                plan_digest=prepared["plan"]["planDigest"], view="workloads")
            assert workloads["items"][0]["generation"] == "9007199254740993"
            assert workloads["items"][0]["observation"] == "ready"
            seal = case.engine.install_plans.root / body["planId"] / "plan.json"
            seal.write_bytes(seal.read_bytes().replace(b'"apiReady":true', b'"apiReady":false'))
            try:
                sdk.install_plan(job, intent=intent, plan_id=body["planId"])
            except NvkeError:
                pass
            else:
                raise AssertionError("tampered sealed plan was exposed")
        assert (case.engine._job(job) / "state.json").read_bytes() == original
        assert (case.engine.workspace / "management-install-state.json").read_bytes() == guard
        assert sorted(p.name for p in (case.engine.workspace / "runs").iterdir()) == runs
        assert list(case.engine.install_plans.links.iterdir()) == []
    finally:
        case.tearDown()
finally:
    fixture.NativeAgentTests.tearDownClass()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", driver, str(Path(__file__).resolve().parents[2]), str(source / "kubernetes/nvke")],
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, "Owned mutual-TLS SDK plan qualification failed"
