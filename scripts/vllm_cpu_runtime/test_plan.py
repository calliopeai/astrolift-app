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

OOM_EVENTS = (
    "low 0\nhigh 0\nmax 41\noom 1\noom_kill 1\noom_group_kill 0\nsock_throttled 0\n"
)


def write_parent(parent, events=OOM_EVENTS, peak="4294967296\n", current="1052672\n"):
    """Lay out the cgroup v2 files the Docker daemon's parent would hold."""
    parent.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("memory.events", events),
        ("memory.peak", peak),
        ("memory.current", current),
    ):
        data = content if isinstance(content, bytes) else content.encode()
        (parent / name).write_bytes(data)


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
            self.assertEqual(
                remove.call_args_list[0].args[0],
                ["docker", "logs", "--tail", "200", "owned-fixture-id"],
            )

    def stopped_docker(self, commands, cgroup_root=None):
        stopped = [
            {
                "State": {"Running": False, "ExitCode": 1, "OOMKilled": True},
                "NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": "12345"}]}},
            }
        ]

        def output(command, **_kwargs):
            commands.append(command)
            if command[:3] == ["docker", "run", "--rm"]:
                return b'{"vllm":"0.15.1+cpu"}\n'
            if command[:3] == ["docker", "run", "--detach"]:
                if cgroup_root:
                    # The daemon creates the parent; the container's own
                    # cgroup under it is gone once the candidate stops.
                    write_parent(
                        cgroup_root / command[command.index("--cgroup-parent") + 1]
                    )
                return b"owned-fixture-id"
            if command[:2] == ["docker", "inspect"]:
                return json.dumps(stopped).encode()
            raise AssertionError("Unexpected subprocess in isolated smoke proof")

        return output

    def test_oom_stop_keeps_the_parent_counters_after_the_container_is_gone(self):
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proof = root / "proof.json"
            with patch.object(smoke, "CGROUP_ROOT", root), patch.object(
                smoke, "inspect_target", return_value="fixture-image"
            ), patch.object(
                smoke.subprocess,
                "check_output",
                side_effect=self.stopped_docker(commands, cgroup_root=root),
            ), patch.object(smoke.subprocess, "run"):
                with self.assertRaises(SystemExit):
                    smoke.smoke("fixture", proof)
            receipt = json.loads(proof.read_text())
        self.assertFalse(receipt["passed"])
        self.assertEqual(receipt["failure_type"], "ValueError")
        self.assertTrue(receipt["oom_killed"])
        self.assertEqual(receipt["container_exit"], 1)
        self.assertEqual(
            receipt["memory_evidence"],
            {
                "peak_bytes": 4294967296,
                "current_bytes": 1052672,
                "events": {"max": 41, "oom": 1, "oom_kill": 1, "oom_group_kill": 0},
            },
        )

    def test_each_run_starts_its_server_under_a_fresh_parent_with_unchanged_limits(
        self,
    ):
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(smoke, "CGROUP_ROOT", root), patch.object(
                smoke, "inspect_target", return_value="fixture-image"
            ), patch.object(
                smoke.subprocess,
                "check_output",
                side_effect=self.stopped_docker(commands),
            ), patch.object(smoke.subprocess, "run"):
                for _ in range(2):
                    with self.assertRaises(SystemExit):
                        smoke.smoke("fixture", root / "proof.json")
            receipt = json.loads((root / "proof.json").read_text())
        servers = [c for c in commands if c[:3] == ["docker", "run", "--detach"]]
        checks = [c for c in commands if c[:3] == ["docker", "run", "--rm"]]
        self.assertEqual((len(servers), len(checks)), (2, 2))
        parents = [c[c.index("--cgroup-parent") + 1] for c in servers]
        for parent in parents:
            self.assertRegex(parent, r"^astrolift_cpu_smoke_[0-9a-f]{16}\.slice$")
        self.assertNotEqual(parents[0], parents[1])
        for check in checks:
            self.assertNotIn("--cgroup-parent", check)
        for server in servers:
            pairs = list(zip(server, server[1:]))
            for pair in (
                ("--cpus", "1"),
                ("--memory", "4g"),
                ("--memory-swap", "4g"),
                ("--shm-size", "1g"),
                ("--env", "VLLM_CPU_KVCACHE_SPACE=1"),
                ("--dtype", "float32"),
                ("--max-model-len", "256"),
                ("--max-num-seqs", "1"),
                ("--max-num-batched-tokens", "256"),
            ):
                self.assertIn(pair, pairs)
        self.assertEqual(
            receipt["memory_evidence"], {"unavailable": "FileNotFoundError"}
        )


class MemoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.parent = (
            Path(directory.name) / "astrolift_cpu_smoke_0123456789abcdef.slice"
        )

    def test_only_whitelisted_integer_counters_leave_the_parent(self):
        write_parent(self.parent)
        evidence = smoke.memory_evidence(self.parent)
        self.assertEqual(
            evidence,
            {
                "peak_bytes": 4294967296,
                "current_bytes": 1052672,
                "events": {"max": 41, "oom": 1, "oom_kill": 1, "oom_group_kill": 0},
            },
        )
        self.assertNotIn(self.root, json.dumps(evidence))

    def test_kernel_without_group_kill_counter_still_reports(self):
        write_parent(self.parent, events="low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n")
        self.assertEqual(
            smoke.memory_evidence(self.parent)["events"],
            {"max": 0, "oom": 0, "oom_kill": 0},
        )

    def test_unreadable_or_unexpected_counters_record_only_the_reason(self):
        cases = {
            "missing parent": (None, "FileNotFoundError"),
            "unlimited peak": ({"peak": "max\n"}, "ValueError"),
            "signed current": ({"current": "-5\n"}, "ValueError"),
            "oversized counter": ({"peak": "1" * 21 + "\n"}, "ValueError"),
            "missing oom_kill": ({"events": "max 0\noom 0\n"}, "ValueError"),
            "unbounded events": ({"events": OOM_EVENTS + "x 0\n" * 2048}, "ValueError"),
            "non-ascii counter": ({"current": b"\xff\n"}, "UnicodeDecodeError"),
        }
        for name, (files, reason) in cases.items():
            with self.subTest(name):
                parent = Path(self.root) / name.replace(" ", "_")
                if files is not None:
                    write_parent(parent, **files)
                self.assertEqual(smoke.memory_evidence(parent), {"unavailable": reason})


if __name__ == "__main__":
    unittest.main()
