from __future__ import annotations

import base64
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
from nvke.operator_contract import CATALOG, REVISION

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
    selected, selected_bundle = os.getenv("NVKE_TEST_AGENT_OPSCODE"), os.getenv("NVKE_TEST_AGENT_BUNDLE")
    if not selected or not selected_bundle:
        pytest.skip("select the explicit owned Opscode source and private native bundle")
    source, bundle = Path(selected).resolve(), Path(selected_bundle).resolve()
    revision = "f102cef0d001cdfded497ac8f423cd15c84b5eff"
    for name in ("operator_agent.py", "operator_contract.py"):
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
    site = str(uuid4())
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
