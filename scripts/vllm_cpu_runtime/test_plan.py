import hashlib
import importlib.util
import io
import time
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


class FakeLogs:
    """A `docker logs --follow` double that replays fixed lines."""

    lines = []
    commands = []

    def __init__(self, command, **_kwargs):
        FakeLogs.commands.append(command)
        self.stdout = io.BytesIO(b"".join(self.lines))

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


_follow = patch.object(smoke.subprocess, "Popen", FakeLogs)


def setUpModule():
    _follow.start()


def tearDownModule():
    _follow.stop()


MEMORY_STAT = (
    "anon 2952790016\n"
    "file 1207959552\n"
    "kernel 41943040\n"
    "kernel_stack 1048576\n"
    "pagetables 12582912\n"
    "sock 0\n"
    "shmem 33554432\n"
    "file_mapped 1048576000\n"
    "file_dirty 0\n"
    "file_writeback 0\n"
    "slab 20971520\n"
    "inactive_anon 2952790016\n"
    "active_anon 0\n"
    "inactive_file 905969664\n"
    "active_file 301989888\n"
    "workingset_refault_file 12345\n"
)
MEMORY_SAMPLE = {
    "current_bytes": 4202692608,
    "anon": 2952790016,
    "file": 1207959552,
    "shmem": 33554432,
    "file_mapped": 1048576000,
    "active_file": 301989888,
    "inactive_file": 905969664,
    "kernel": 41943040,
    "slab": 20971520,
}
VLLM_LOG = [
    b"(APIServer pid=1) INFO 10-26 06:14:35 [model.py:1561] Using max model len 256\n",
    b"(EngineCore_DP0 pid=14) INFO 10-26 06:14:53 [default_loader.py:291] Loading weights took 1.44 seconds\n",
    b"(EngineCore_DP0 pid=14) INFO 10-26 06:14:53 [kv_cache_utils.py:1307] GPU KV cache size: 43,648 tokens\n",
    b"(EngineCore_DP0 pid=14) INFO 10-26 06:14:53 [core.py:272] init engine (profile, create kv cache, warmup model) took 0.55 seconds\n",
    b"(APIServer pid=1) INFO 10-26 06:14:54 [serving.py:177] Warming up chat template processing...\n",
]

OOM_EVENTS = (
    "low 0\nhigh 0\nmax 41\noom 1\noom_kill 1\noom_group_kill 0\nsock_throttled 0\n"
)


def write_parent(
    parent, events=OOM_EVENTS, peak="4294967296\n", current="1052672\n", stat=None
):
    """Lay out the cgroup v2 files the Docker daemon's parent would hold."""
    parent.mkdir(parents=True, exist_ok=True)
    files = [
        ("memory.events", events),
        ("memory.peak", peak),
        ("memory.current", current),
    ]
    if stat is not None:
        files.append(("memory.stat", stat))
    for name, content in files:
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

    def test_refusal_before_a_server_starts_records_no_memory_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proof = root / "proof.json"
            with patch.object(smoke, "CGROUP_ROOT", root), patch.object(
                smoke,
                "inspect_target",
                side_effect=ValueError("Wrong candidate platform"),
            ), patch.object(
                smoke.subprocess,
                "check_output",
                side_effect=AssertionError("No container may start"),
            ), patch.object(smoke.subprocess, "run") as run:
                with self.assertRaises(SystemExit):
                    smoke.smoke("fixture", proof)
            receipt = json.loads(proof.read_text())
        self.assertEqual(receipt["failure_type"], "ValueError")
        self.assertNotIn("memory_evidence", receipt)
        run.assert_not_called()

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
        self.assertEqual(receipt["failure_type"], "OomKillDuringReadiness")
        self.assertEqual(receipt["outcome"], "oom_kill during readiness")
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
                ("--num-gpu-blocks-override", "3"),
            ):
                self.assertIn(pair, pairs)
        self.assertEqual(
            receipt["memory_evidence"], {"unavailable": "FileNotFoundError"}
        )
        self.assertEqual(receipt["kv_cache_blocks"], 3)


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


class MemorySampleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.parent = (
            Path(directory.name) / "astrolift_cpu_smoke_0123456789abcdef.slice"
        )

    def sample(self):
        sample = smoke.memory_sample(self.parent, time.perf_counter())
        sample.pop("elapsed_s", None)
        return sample

    def test_only_allowlisted_composition_fields_leave_the_parent(self):
        write_parent(self.parent, current="4202692608\n", stat=MEMORY_STAT)
        sample = smoke.memory_sample(self.parent, time.perf_counter() - 3)
        self.assertGreaterEqual(sample.pop("elapsed_s"), 3)
        self.assertEqual(sample, MEMORY_SAMPLE)
        text = json.dumps(sample)
        for leaked in ("pagetables", "workingset", "kernel_stack", self.root):
            self.assertNotIn(leaked, text)

    def test_kernel_without_the_kernel_field_still_samples(self):
        stat = "".join(
            line + "\n"
            for line in MEMORY_STAT.splitlines()
            if not line.startswith("kernel ")
        )
        write_parent(self.parent, current="4202692608\n", stat=stat)
        expected = dict(MEMORY_SAMPLE)
        del expected["kernel"]
        self.assertEqual(self.sample(), expected)

    def test_unreadable_or_unexpected_stat_records_only_the_reason(self):
        cases = {
            "missing stat": (None, "FileNotFoundError"),
            "missing anon": (
                MEMORY_STAT.replace("anon 2952790016\n", "", 1),
                "ValueError",
            ),
            "signed file": (
                MEMORY_STAT.replace("file 1207959552", "file -1"),
                "ValueError",
            ),
            "unbounded stat": (MEMORY_STAT + "x 0\n" * 8192, "ValueError"),
            "non-ascii stat": (MEMORY_STAT.encode() + b"\xff\n", "UnicodeDecodeError"),
        }
        for name, (stat, reason) in cases.items():
            with self.subTest(name):
                self.parent = Path(self.root) / name.replace(" ", "_")
                write_parent(self.parent, stat=stat)
                self.assertEqual(self.sample(), {"unavailable": reason})

    def test_oom_kills_reads_only_the_kill_counter(self):
        write_parent(self.parent)
        self.assertEqual(smoke.oom_kills(self.parent), 1)
        write_parent(self.parent, events="max 3\noom 0\noom_kill 0\n")
        self.assertEqual(smoke.oom_kills(self.parent), 0)
        self.assertIsNone(smoke.oom_kills(Path(self.root) / "missing"))
        write_parent(self.parent, events="oom_kill many\n")
        self.assertIsNone(smoke.oom_kills(self.parent))


class FollowPhasesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.parent = Path(directory.name) / "parent.slice"
        write_parent(self.parent, current="4202692608\n", stat=MEMORY_STAT)
        FakeLogs.commands = []
        self.addCleanup(setattr, FakeLogs, "lines", [])

    def follow(self, lines):
        FakeLogs.lines = lines
        samples = {}
        smoke.follow_phases(
            "owned-fixture-id", self.parent, time.perf_counter(), samples
        )()
        for sample in samples.values():
            sample.pop("elapsed_s")
        return samples

    def test_each_startup_point_is_sampled_once_in_log_order(self):
        noise = [b"Loading safetensors checkpoint shards: 100% Completed\r\n"]
        with patch.object(smoke, "memory_sample", wraps=smoke.memory_sample) as taken:
            samples = self.follow(noise + VLLM_LOG + VLLM_LOG)
        self.assertEqual(taken.call_count, 4)
        self.assertEqual(
            list(samples),
            ["before_engine_start", "weights_loaded", "kv_cache_allocated", "warmup"],
        )
        for sample in samples.values():
            self.assertEqual(sample, MEMORY_SAMPLE)
        self.assertEqual(
            FakeLogs.commands, [["docker", "logs", "--follow", "owned-fixture-id"]]
        )

    def test_a_point_missing_from_the_log_is_left_out(self):
        samples = self.follow([VLLM_LOG[1], VLLM_LOG[4]])
        self.assertEqual(list(samples), ["weights_loaded", "warmup"])

    def test_the_follower_stops_reading_at_its_bound(self):
        with patch.object(smoke, "LOG_FOLLOW_BOUND", 1024):
            samples = self.follow([b"x" * 100 + b"\n"] * 11 + VLLM_LOG)
        self.assertEqual(samples, {})

    def test_docker_unavailable_samples_nothing(self):
        samples = {}
        with patch.object(smoke.subprocess, "Popen", side_effect=FileNotFoundError):
            smoke.follow_phases("owned-fixture-id", self.parent, 0, samples)()
        self.assertEqual(samples, {})


CPU_BLOCK = (
    "processor\t: {n}\n"
    "vendor_id\t: GenuineIntel\n"
    "model name\t: Intel(R) Xeon(R) Platinum 8473C\n"
    "physical id\t: 0\n"
    "siblings\t: 4\n"
    "core id\t\t: {core}\n"
    "cpu cores\t: 2\n"
    "microcode\t: 0xffffffff\n"
    "flags\t\t: fpu sse2 avx2 avx512f avx512_vnni avx512_bf16 amx_bf16 {extra}\n"
)
MEMINFO = "MemTotal:       16369416 kB\nMemFree:         1000 kB\n"


def write_proc(proc, cpuinfo=None, meminfo=MEMINFO):
    proc.mkdir(parents=True, exist_ok=True)
    if cpuinfo is None:
        cpuinfo = "\n".join(
            CPU_BLOCK.format(n=n, core=n // 2, extra="amx_tile amx_int8 hypervisor")
            for n in range(4)
        )
    for name, content in (("cpuinfo", cpuinfo), ("meminfo", meminfo)):
        data = content if isinstance(content, bytes) else content.encode()
        (proc / name).write_bytes(data)


class HostEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_only_named_fields_and_allowlisted_flags_leave_the_host(self):
        write_proc(self.root)
        evidence = smoke.host_evidence(self.root)
        self.assertEqual(
            evidence,
            {
                "cpu_vendor": "GenuineIntel",
                "cpu_model": "Intel(R) Xeon(R) Platinum 8473C",
                "logical_cpus": 4,
                "physical_cores": 2,
                "isa_flags": {
                    "avx2": True,
                    "avx512f": True,
                    "avx512_bf16": True,
                    "avx512_vnni": True,
                    "amx_tile": True,
                    "amx_bf16": True,
                    "amx_int8": True,
                },
                "mem_total_bytes": 16369416 * 1024,
            },
        )
        text = json.dumps(evidence)
        for leaked in ("hypervisor", "microcode", "0xffffffff", "sse2", "MemFree"):
            self.assertNotIn(leaked, text)

    def test_a_flag_counts_only_when_every_cpu_lists_it(self):
        cpuinfo = "\n".join(
            CPU_BLOCK.format(n=n, core=n // 2, extra="amx_tile" if n else "")
            for n in range(4)
        )
        write_proc(self.root, cpuinfo=cpuinfo)
        flags = smoke.host_evidence(self.root)["isa_flags"]
        self.assertFalse(flags["amx_tile"])
        self.assertFalse(flags["amx_int8"])
        self.assertTrue(flags["amx_bf16"])

    def test_unreadable_or_unexpected_host_files_record_only_the_reason(self):
        one = CPU_BLOCK.format(n=0, core=0, extra="")
        cases = {
            "missing proc": (None, "FileNotFoundError"),
            "unbounded cpuinfo": (
                {"cpuinfo": one + "\n" + "x" * smoke.CPUINFO_READ_BOUND},
                "ValueError",
            ),
            "unbounded meminfo": (
                {"meminfo": MEMINFO + "y" * smoke.MEMINFO_READ_BOUND},
                "ValueError",
            ),
            "control character in model": (
                {"cpuinfo": one.replace("8473C", "8473C\x1b[2J")},
                "ValueError",
            ),
            "oversized model": (
                {"cpuinfo": one.replace("8473C", "A" * 97)},
                "ValueError",
            ),
            "odd vendor": (
                {"cpuinfo": one.replace("GenuineIntel", "Genuine/Intel")},
                "ValueError",
            ),
            "non-ascii cpuinfo": (
                {"cpuinfo": b"processor : 0\xff\n"},
                "UnicodeDecodeError",
            ),
            "no processors": ({"cpuinfo": "\n\n"}, "ValueError"),
            "no flags": ({"cpuinfo": one.split("flags")[0]}, "KeyError"),
            "no MemTotal": ({"meminfo": "MemFree: 1 kB\n"}, "ValueError"),
            "MemTotal in pages": ({"meminfo": "MemTotal: 5 pages\n"}, "ValueError"),
        }
        for name, (files, reason) in cases.items():
            with self.subTest(name):
                proc = self.root / name.replace(" ", "_")
                if files is not None:
                    write_proc(proc, **files)
                self.assertEqual(smoke.host_evidence(proc), {"unavailable": reason})

    def test_refused_smoke_still_records_the_host(self):
        write_proc(self.root / "proc")
        proof = self.root / "proof.json"
        with patch.object(smoke, "PROC_ROOT", self.root / "proc"), patch.object(
            smoke, "inspect_target", side_effect=ValueError("Wrong candidate platform")
        ), patch.object(smoke.subprocess, "run"):
            with self.assertRaises(SystemExit):
                smoke.smoke("fixture", proof)
        receipt = json.loads(proof.read_text())
        self.assertEqual(
            receipt["host"]["cpu_model"], "Intel(R) Xeon(R) Platinum 8473C"
        )
        self.assertEqual(receipt["failure_type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
