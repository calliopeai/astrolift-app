import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("prepare")
smoke = load("smoke")


def archive(
    name,
    content=b"FROM ubuntu:22.04 AS base-common\nRUN VLLM_TARGET_DEVICE=cpu python3 setup.py bdist_wheel\n",
    kind=None,
):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as target:
        member = tarfile.TarInfo(name)
        if kind:
            member.type = kind
            member.linkname = "../../outside"
        else:
            member.size = len(content)
        target.addfile(member, io.BytesIO(content) if not kind else None)
    return buffer.getvalue()


class PreparedSourceTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / "source.json").read_text())
        self.root = "vllm-" + self.spec["upstream_revision"]

    def run_prepare(self, raw):
        self.spec["archive_sha256"] = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            return prepare.prepare(raw, Path(directory) / "new", self.spec)

    def test_pins_base_and_unbound_one_thread_defaults(self):
        raw = archive(self.root + "/docker/Dockerfile.cpu")
        self.spec["archive_sha256"] = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = prepare.prepare(raw, Path(directory) / "new", self.spec)
            text = (root / "docker/Dockerfile.cpu").read_text()
            self.assertIn(self.spec["ubuntu_amd64_image"], text)
            self.assertNotIn("FROM ubuntu:22.04", text)
            self.assertIn("VLLM_CPU_OMP_THREADS_BIND=nobind OMP_NUM_THREADS=1", text)
            self.assertIn(self.spec["upstream_revision"], text)
            self.assertIn(
                "VLLM_VERSION_OVERRIDE=0.15.1+cpu VLLM_TARGET_DEVICE=cpu", text
            )

    def test_wrong_archive_hash_refuses_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "new"
            with self.assertRaisesRegex(ValueError, "digest"):
                prepare.prepare(b"wrong bytes", destination, self.spec)
            self.assertFalse(destination.exists())

    def test_traversal_refused(self):
        with self.assertRaisesRegex(ValueError, "archive member"):
            self.run_prepare(archive(self.root + "/../outside"))

    def test_symlink_refused(self):
        with self.assertRaisesRegex(ValueError, "archive member"):
            self.run_prepare(archive(self.root + "/bad", kind=tarfile.SYMTYPE))

    def test_changed_upstream_dockerfile_refused(self):
        with self.assertRaisesRegex(ValueError, "base changed"):
            self.run_prepare(
                archive(self.root + "/docker/Dockerfile.cpu", b"FROM other\n")
            )

    def test_all_advanced_isa_flags_disabled(self):
        flags = self.spec["build_args"]
        self.assertEqual(flags["VLLM_CPU_AVX2"], "true")
        self.assertEqual(flags["VLLM_CPU_DISABLE_AVX512"], "true")
        for key in ("AVX512", "AVX512BF16", "AVX512VNNI", "AMXBF16"):
            self.assertEqual(flags["VLLM_CPU_" + key], "false")


class SmokeControlTests(unittest.TestCase):
    def test_wrong_platform_refused(self):
        data = [{"Architecture": "arm64", "Os": "linux"}]
        with patch.object(
            smoke.subprocess, "check_output", return_value=json.dumps(data).encode()
        ):
            with self.assertRaisesRegex(ValueError, "platform"):
                smoke.inspect_target("fixture")

    def test_failure_to_start_records_refusal_and_removes_owned_container(self):
        running = [
            {
                "State": {"Running": False, "ExitCode": 137, "OOMKilled": True},
                "NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": "12345"}]}},
            }
        ]
        responses = [
            b'{"vllm":"0.15.1+cpu"}\n',
            b"owned-fixture-id",
            json.dumps(running).encode(),
            json.dumps(running).encode(),
        ]
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / "proof.json"
            with patch.object(
                smoke, "inspect_target", return_value="fixture-image"
            ), patch.object(
                smoke.subprocess, "check_output", side_effect=responses
            ), patch.object(smoke.subprocess, "run") as remove:
                with self.assertRaises(SystemExit):
                    smoke.smoke("fixture", proof)
            receipt = json.loads(proof.read_text())
            self.assertFalse(receipt["passed"])
            self.assertTrue(receipt["oom_killed"])
            self.assertEqual(receipt["container_exit"], 137)
            self.assertFalse(receipt["live_cluster_certified"])
            self.assertEqual(proof.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                remove.call_args.args[0],
                ["docker", "rm", "--force", "owned-fixture-id"],
            )


if __name__ == "__main__":
    unittest.main()
