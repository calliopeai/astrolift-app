"""Run an explicitly owned loopback admission server; never use ambient kubeconfig."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import signal
import socket
import ssl
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def authority(root):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Owned NVKE admission CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(key, hashes.SHA256())
    )
    (root / "ca.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    for label, subject, usage in (
        (
            "server",
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]),
            [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH],
        ),
        (
            "etcd-server",
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "owned-etcd")]),
            [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH, x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH],
        ),
        (
            "admin",
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "owned-admin"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "system:masters"),
                ]
            ),
            [x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH],
        ),
    ):
        private = ec.generate_private_key(ec.SECP256R1())
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(name)
            .public_key(private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.ExtendedKeyUsage(usage), False)
        )
        if label != "admin":
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
            )
        (root / (label + ".pem")).write_bytes(
            builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
        )
        (root / (label + ".key")).write_bytes(
            private.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (root / "service-account.key").write_bytes(
        signer.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--lifetime-seconds", type=int, default=7200)
    args = parser.parse_args()
    if not 60 <= args.lifetime_seconds <= 14400 or not args.root.is_absolute():
        parser.error("Choose a new absolute private root and a lifetime between 60 and 14400 seconds")
    os.umask(0o077)
    report = json.loads(args.build_report.read_text())
    assert report["exitCode"] == 0, "Native asset build must have completed successfully"
    assets = Path(report["assetRoot"])
    manifest = json.loads((assets / "manifest.json").read_text())
    assert manifest["platform"] == "darwin/arm64" and manifest["kubernetesVersion"] == "v1.36.4"
    assert manifest["etcdVersion"] == "v3.6.8"
    assert manifest["sourceMainSha256"] == {
        "kube-apiserver": "8471f518c26f09b7670521522ecd7599bc5317a773f7b779608d3dfd22ced0ec",
        "etcd": "824bce45d763aaa6227e4b7557cbd222a7e039fe23db2d9a85ef98167eaef4e3",
    }
    for name in ("kube-apiserver", "etcd"):
        assert digest(assets / name) == manifest["binaries"][name]["sha256"]
        assert digest(assets / "src" / name / "main.go") == manifest["sourceMainSha256"][name]
    for name, field in (("go.mod", "goModSha256"), ("go.sum", "goSumSha256")):
        assert digest(assets / name) == manifest[field]
    assert (
        subprocess.check_output([str(assets / "kube-apiserver"), "--version"], text=True).strip()
        == "Kubernetes v1.36.4"
    )
    contract_path = Path(__file__).resolve().parents[3] / "nvke/upstream/contract.json"
    contract = json.loads(contract_path.read_text())
    crds = contract["custom_resources"]
    assert len(crds) == 6 and len({crd["metadata"]["name"] for crd in crds}) == 6
    assert all(crd["spec"]["group"] == "internal.nvke.nvidia.com" for crd in crds)
    args.root.mkdir(mode=0o700)
    root = args.root.resolve(strict=True)
    authority(root)
    api_port, etcd_port, peer_port = port(), port(), port()
    assert len({api_port, etcd_port, peer_port}) == 3
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    processes, logs = {}, []

    def spawn(name, arguments):
        log = (root / (name + ".log")).open("xb")
        logs.append(log)
        env = {"PATH": os.defpath, "HOME": str(root), "GOMAXPROCS": "2", "GOMEMLIMIT": "512MiB"}
        processes[name] = subprocess.Popen(
            [str(assets / name), *arguments], env=env, stdout=log, stderr=subprocess.STDOUT
        )
        (root / "processes.json").write_text(json.dumps({key: child.pid for key, child in processes.items()}))

    context = ssl.create_default_context(cafile=str(root / "ca.pem"))
    context.load_cert_chain(str(root / "admin.pem"), str(root / "admin.key"))

    def wait(client, path):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not stopped.is_set():
            assert all(child.poll() is None for child in processes.values()), (
                "Owned component exited; inspect private logs"
            )
            try:
                if client.get(path).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            stopped.wait(0.25)
        raise TimeoutError("Owned admission readiness deadline")

    client_url, peer_url = f"https://127.0.0.1:{etcd_port}", f"http://127.0.0.1:{peer_port}"
    try:
        spawn(
            "etcd",
            [
                "--name=owned",
                f"--data-dir={root / 'etcd'}",
                f"--listen-client-urls={client_url}",
                f"--advertise-client-urls={client_url}",
                f"--listen-peer-urls={peer_url}",
                f"--initial-advertise-peer-urls={peer_url}",
                f"--initial-cluster=owned={peer_url}",
                f"--cert-file={root / 'etcd-server.pem'}",
                f"--key-file={root / 'etcd-server.key'}",
                f"--trusted-ca-file={root / 'ca.pem'}",
                "--client-cert-auth=true",
                "--quota-backend-bytes=134217728",
                "--log-level=error",
            ],
        )
        with httpx.Client(base_url=client_url, verify=context, trust_env=False, timeout=2) as etcd:
            wait(etcd, "/health")
        endpoint = f"https://127.0.0.1:{api_port}"
        audit_policy = root / "audit-policy.json"
        audit_policy.write_text(
            json.dumps(
                {
                    "apiVersion": "audit.k8s.io/v1",
                    "kind": "Policy",
                    "omitStages": ["RequestReceived"],
                    "rules": [{"level": "Metadata"}],
                }
            )
        )
        spawn(
            "kube-apiserver",
            [
                "--bind-address=127.0.0.1",
                f"--secure-port={api_port}",
                f"--etcd-servers={client_url}",
                f"--etcd-cafile={root / 'ca.pem'}",
                f"--etcd-certfile={root / 'admin.pem'}",
                f"--etcd-keyfile={root / 'admin.key'}",
                f"--tls-cert-file={root / 'server.pem'}",
                f"--tls-private-key-file={root / 'server.key'}",
                f"--client-ca-file={root / 'ca.pem'}",
                "--service-account-issuer=https://owned.invalid",
                f"--service-account-signing-key-file={root / 'service-account.key'}",
                f"--service-account-key-file={root / 'service-account.key'}",
                "--service-cluster-ip-range=10.96.0.0/24",
                "--authorization-mode=Node,RBAC",
                "--anonymous-auth=false",
                "--allow-privileged=false",
                "--profiling=false",
                f"--audit-policy-file={audit_policy}",
                f"--audit-log-path={root / 'audit.log'}",
                "--audit-log-maxsize=5",
                "--audit-log-maxbackup=1",
                "--audit-log-mode=blocking",
                "--v=0",
            ],
        )
        with httpx.Client(base_url=endpoint, verify=context, trust_env=False, timeout=15) as api:
            wait(api, "/readyz")
            assert api.get("/version").json()["gitVersion"] == "v1.36.4"
            route = "/apis/apiextensions.k8s.io/v1/customresourcedefinitions"
            for crd in crds:
                api.post(route, json=crd).raise_for_status()
            deadline = time.monotonic() + 120
            while True:
                established = [
                    any(
                        condition["type"] == "Established" and condition["status"] == "True"
                        for condition in api.get(route + "/" + crd["metadata"]["name"])
                        .json()
                        .get("status", {})
                        .get("conditions", [])
                    )
                    for crd in crds
                ]
                if all(established):
                    break
                assert time.monotonic() < deadline and not stopped.is_set(), "CRD establishment deadline"
                stopped.wait(0.25)
            api.post("/api/v1/namespaces", json={"metadata": {"name": "org-owned"}}).raise_for_status()
            # Established does not imply the newly served resource's storage cache is ready.
            for crd in crds:
                for version in crd["spec"]["versions"]:
                    if version["served"]:
                        wait(
                            api,
                            "/apis/"
                            + crd["spec"]["group"]
                            + "/"
                            + version["name"]
                            + "/"
                            + crd["spec"]["names"]["plural"]
                            + "?limit=1",
                        )

        def encode(name):
            return base64.b64encode((root / name).read_bytes()).decode()

        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {"name": "owned", "cluster": {"server": endpoint, "certificate-authority-data": encode("ca.pem")}}
            ],
            "users": [
                {
                    "name": "owned",
                    "user": {"client-certificate-data": encode("admin.pem"), "client-key-data": encode("admin.key")},
                }
            ],
            "contexts": [{"name": "astro-nvke-local", "context": {"cluster": "owned", "user": "owned"}}],
            "current-context": "astro-nvke-local",
        }
        (root / "kubeconfig.json").write_text(json.dumps(kubeconfig))
        (root / "ready.json").write_text(
            json.dumps(
                {
                    "endpoint": endpoint,
                    "kubeconfig": str(root / "kubeconfig.json"),
                    "crds": [crd["metadata"]["name"] for crd in crds],
                    "contractRevision": contract["revision"],
                    "contractSha256": digest(contract_path),
                    "buildReportSha256": digest(args.build_report),
                }
            )
        )
        deadline = time.monotonic() + args.lifetime_seconds
        while not stopped.wait(1):
            assert all(child.poll() is None for child in processes.values()), "Owned component exited"
            if time.monotonic() >= deadline:
                break
    finally:
        for child in reversed(list(processes.values())):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for log in logs:
            log.close()
        (root / "stopped.json").write_text(json.dumps({name: child.returncode for name, child in processes.items()}))


if __name__ == "__main__":
    main()
