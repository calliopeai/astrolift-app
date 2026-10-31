"""Remote-runner smoke only; localhost server and public immutable model source."""

import argparse
import json
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


PACKAGE_CHECK = """import importlib.metadata, json, torch
import vllm._C
assert importlib.metadata.version("vllm") == "0.15.1+cpu"
assert importlib.metadata.version("torch") == "2.10.0+cpu"
x = torch.arange(-16, 16, dtype=torch.float32).reshape(2, 16) / 8
out = torch.empty((2, 8), dtype=torch.float32)
torch.ops._C.silu_and_mul(out, x)
assert torch.isfinite(out).all()
assert torch.allclose(out, torch.nn.functional.silu(x[:, :8]) * x[:, 8:], rtol=1e-5, atol=1e-6)
print(json.dumps({"vllm": importlib.metadata.version("vllm"), "torch": importlib.metadata.version("torch"), "float32_cpu_kernel": True, "packages": sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())}))"""


def inspect_target(image):
    data = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
    if data["Architecture"] != "amd64" or data["Os"] != "linux":
        raise ValueError("Wrong candidate platform")
    labels = data["Config"]["Labels"]
    expected = {
        "ai.vllm.build.cpu-disable-avx512": "true",
        "ai.vllm.build.cpu-avx2": "true",
        "ai.vllm.build.cpu-avx512": "false",
        "ai.vllm.build.cpu-avx512bf16": "false",
        "ai.vllm.build.cpu-avx512vnni": "false",
        "ai.vllm.build.cpu-amxbf16": "false",
    }
    if any(labels.get(key) != value for key, value in expected.items()):
        raise ValueError("Candidate ISA labels disagree with reviewed build")
    return data["Id"]


def post_prompt(port, model):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hello."}],
        "max_tokens": 8,
        "temperature": 0,
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read(1024 * 1024))
    if not result.get("choices") or not isinstance(
        result["choices"][0].get("message", {}).get("content"), str
    ):
        raise ValueError("Missing model completion")
    return {"completion_received": True, "usage": result.get("usage")}


LOG_TAIL_LINES = 200


def keep_container_log(container, path):
    """Keep a bounded tail of a failed candidate's own startup output.

    The server runs a public model with no credentials, so its log carries no
    secrets; the tail is what distinguishes a slow load from a stuck engine.
    """
    result = subprocess.run(
        ["docker", "logs", "--tail", str(LOG_TAIL_LINES), container],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    if isinstance(result.stdout, bytes):
        path.write_bytes(result.stdout)
        path.chmod(0o600)


CGROUP_ROOT = Path("/sys/fs/cgroup")
CGROUP_READ_BOUND = 4096
MEMORY_EVENTS = ("max", "oom", "oom_kill", "oom_group_kill")


def memory_parent():
    """Name a cgroup parent for one candidate, fresh for each run.

    Docker deletes a container's cgroup when the container stops, and an OOM
    kill's counters go with it. The parent outlives the container and counts
    hierarchically, so the counters survive the stop. It sets no limit of its
    own and is left empty on the host afterwards.
    """
    return f"astrolift_cpu_smoke_{secrets.token_hex(8)}.slice"


def read_bounded(path, bound):
    with path.open("rb") as source:
        data = source.read(bound + 1)
    if len(data) > bound:
        raise ValueError("file exceeds bound")
    return data.decode("ascii")


def read_cgroup(path):
    return read_bounded(path, CGROUP_READ_BOUND)


def cgroup_counter(text):
    value = text.strip()
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise ValueError("Unexpected cgroup counter")
    return int(value)


def memory_evidence(parent):
    """Whitelisted integer counters of the candidate's cgroup parent.

    Read once, at the end of the smoke and before the container is removed:
    after a completion or a timeout, current is the running candidate's use;
    after a stop, only what its exited processes left charged. `oom` counts
    the container reaching its own limit; `oom_kill` counts any OOM killer.
    Values only, never paths or identifiers.
    """
    try:
        events = {}
        for line in read_cgroup(parent / "memory.events").splitlines():
            name, _, value = line.partition(" ")
            if name in MEMORY_EVENTS:
                events[name] = cgroup_counter(value)
        if any(name not in events for name in ("max", "oom", "oom_kill")):
            raise ValueError("Incomplete cgroup memory events")
        return {
            "peak_bytes": cgroup_counter(read_cgroup(parent / "memory.peak")),
            "current_bytes": cgroup_counter(read_cgroup(parent / "memory.current")),
            "events": events,
        }
    except (OSError, ValueError) as exc:
        return {"unavailable": type(exc).__name__}


MEMORY_STAT_READ_BOUND = 16384
MEMORY_STAT_FIELDS = (
    "anon",
    "file",
    "shmem",
    "file_mapped",
    "active_file",
    "inactive_file",
    "kernel",
    "slab",
)
# Startup points, matched in the candidate's own log in this order. The first
# line matching each one triggers a sample; "ready" is the first healthy poll.
MEMORY_PHASES = (
    ("before_engine_start", re.compile(rb"Using max model len")),
    ("weights_loaded", re.compile(rb"Loading weights took")),
    ("kv_cache_allocated", re.compile(rb"init engine \(profile, create kv cache")),
    ("warmup", re.compile(rb"Warming up chat template processing")),
)
LOG_LINE_BOUND = 4096
LOG_FOLLOW_BOUND = 8 * 1024 * 1024


def memory_sample(parent, started):
    """Composition of the parent's charge at one point, integers only.

    `file` is page cache and includes `shmem`; the kernel reclaims
    `inactive_file` first and `active_file` after it, never `anon` (there is
    no swap). `kernel` is absent on kernels older than 5.18.
    """
    try:
        stat = {}
        for line in read_bounded(
            parent / "memory.stat", MEMORY_STAT_READ_BOUND
        ).splitlines():
            name, _, value = line.partition(" ")
            if name in MEMORY_STAT_FIELDS:
                stat[name] = cgroup_counter(value)
        if any(name not in stat for name in MEMORY_STAT_FIELDS if name != "kernel"):
            raise ValueError("Incomplete cgroup memory stat")
        return {
            "elapsed_s": round(time.perf_counter() - started, 1),
            "current_bytes": cgroup_counter(read_cgroup(parent / "memory.current")),
            **stat,
        }
    except (OSError, ValueError) as exc:
        return {"unavailable": type(exc).__name__}


def oom_kills(parent):
    """The parent's oom_kill count, or None where it cannot be read."""
    try:
        for line in read_cgroup(parent / "memory.events").splitlines():
            name, _, value = line.partition(" ")
            if name == "oom_kill":
                return cgroup_counter(value)
    except (OSError, ValueError):
        pass
    return None


def follow_phases(container, parent, started, samples):
    """Sample memory at each startup point as its log line appears.

    Follows the candidate's log in a thread; reads lines of at most 4 KiB and
    8 MiB in all, keeps no log text, and stops once every point is sampled.
    Returns a function that stops the follower.
    """
    try:
        follower = subprocess.Popen(
            ["docker", "logs", "--follow", container],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError:
        return lambda: None

    def read():
        pending = list(MEMORY_PHASES)
        consumed = 0
        try:
            while pending and consumed < LOG_FOLLOW_BOUND:
                line = follower.stdout.readline(LOG_LINE_BOUND)
                if not line:
                    break
                consumed += len(line)
                for index, (name, marker) in enumerate(pending):
                    if marker.search(line):
                        samples[name] = memory_sample(parent, started)
                        del pending[: index + 1]
                        break
        except (OSError, ValueError):
            pass
        finally:
            follower.kill()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def stop():
        follower.kill()
        reader.join(10)
        follower.stdout.close()
        follower.wait(10)

    return stop


class OomKillDuringReadiness(Exception):
    pass


PROC_ROOT = Path("/proc")
CPUINFO_READ_BOUND = 256 * 1024
MEMINFO_READ_BOUND = 8192
ISA_FLAGS = (
    "avx2",
    "avx512f",
    "avx512_bf16",
    "avx512_vnni",
    "amx_tile",
    "amx_bf16",
    "amx_int8",
)


def host_text(value, pattern):
    value = " ".join(value.split())
    if not re.fullmatch(pattern, value):
        raise ValueError("Unexpected host value")
    return value


def host_evidence(proc=None):
    """CPU model, vendor, counts, an allowlist of ISA flags and memory total.

    Read from /proc/cpuinfo and /proc/meminfo with a bound on each. Only the
    named fields leave the host, each matched against a narrow pattern; a flag
    counts as present only when every logical CPU lists it.
    """
    proc = proc or PROC_ROOT
    try:
        blocks = [
            dict(
                (key.strip(), value.strip())
                for key, _, value in (
                    line.partition(":") for line in block.splitlines() if ":" in line
                )
            )
            for block in read_bounded(proc / "cpuinfo", CPUINFO_READ_BOUND).split(
                "\n\n"
            )
            if block.strip()
        ]
        blocks = [block for block in blocks if "processor" in block]
        if not blocks:
            raise ValueError("No processors listed")
        first = blocks[0]
        flags = set(first["flags"].split())
        for block in blocks[1:]:
            flags &= set(block.get("flags", "").split())
        cores = {
            block.get("physical id", "0"): cgroup_counter(block["cpu cores"])
            for block in blocks
            if "cpu cores" in block
        }
        evidence = {
            "cpu_vendor": host_text(first["vendor_id"], r"[A-Za-z0-9]{1,32}"),
            "cpu_model": host_text(first["model name"], r"[ -~]{1,96}"),
            "logical_cpus": len(blocks),
            "physical_cores": sum(cores.values()) if cores else None,
            "isa_flags": {flag: flag in flags for flag in ISA_FLAGS},
        }
        for line in read_bounded(proc / "meminfo", MEMINFO_READ_BOUND).splitlines():
            name, _, value = line.partition(":")
            if name == "MemTotal":
                number, _, unit = value.strip().partition(" ")
                if unit != "kB":
                    raise ValueError("Unexpected MemTotal unit")
                evidence["mem_total_bytes"] = cgroup_counter(number) * 1024
                break
        else:
            raise ValueError("No MemTotal")
        return evidence
    except (OSError, ValueError, KeyError) as exc:
        return {"unavailable": type(exc).__name__}


# Two 128-token blocks hold the 256-token context of the one sequence, and
# vLLM keeps one more as its null block. VLLM_CPU_KVCACHE_SPACE stays the
# 1 GiB ceiling vLLM checks the context against; this sets what is allocated.
KV_CACHE_BLOCKS = 3


def smoke(image, proof):
    spec = json.loads(Path(__file__).with_name("source.json").read_text())
    receipt = {
        "source": spec["upstream_revision"],
        "model_revision": spec["model_revision"],
        "passed": False,
        "dtype": "float32",
        "cpu_limit": "1",
        "memory_limit": "4GiB",
        "kv_cache_gib": 1,
        "kv_cache_blocks": KV_CACHE_BLOCKS,
        "live_cluster_certified": False,
        "host": host_evidence(),
    }
    container = None
    parent = memory_parent()
    phases = {}
    stop_following = None
    try:
        receipt["image_id"] = inspect_target(image)
        output = subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--entrypoint",
                "python",
                image,
                "-c",
                PACKAGE_CHECK,
            ],
            timeout=120,
        )
        receipt["package_check"] = json.loads(output.splitlines()[-1])
        container = (
            subprocess.check_output(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--pull",
                    "never",
                    "--cgroup-parent",
                    parent,
                    "--cpus",
                    "1",
                    "--memory",
                    "4g",
                    "--memory-swap",
                    "4g",
                    "--shm-size",
                    "1g",
                    "--publish",
                    "127.0.0.1::8000",
                    "--env",
                    "VLLM_CPU_KVCACHE_SPACE=1",
                    "--env",
                    "VLLM_USE_RUST_FRONTEND=0",
                    image,
                    spec["model"],
                    "--revision",
                    spec["model_revision"],
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                    "--dtype",
                    "float32",
                    "--max-model-len",
                    "256",
                    "--max-num-seqs",
                    "1",
                    "--max-num-batched-tokens",
                    "256",
                    "--num-gpu-blocks-override",
                    str(KV_CACHE_BLOCKS),
                    "--enforce-eager",
                ],
                timeout=30,
            )
            .decode()
            .strip()
        )
        started = time.perf_counter()
        stop_following = follow_phases(container, CGROUP_ROOT / parent, started, phases)
        data = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
        port = int(data["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"])
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            data = json.loads(
                subprocess.check_output(["docker", "inspect", container])
            )[0]
            if not data["State"]["Running"]:
                receipt["container_exit"] = data["State"]["ExitCode"]
                receipt["oom_killed"] = data["State"]["OOMKilled"]
            # The parent is fresh, so any OOM kill is this candidate's, and an
            # OOM-killed engine never becomes ready behind a live API server.
            if oom_kills(CGROUP_ROOT / parent):
                receipt["outcome"] = "oom_kill during readiness"
                raise OomKillDuringReadiness("Candidate OOM-killed before readiness")
            if not data["State"]["Running"]:
                raise ValueError("Candidate stopped before readiness")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=5
                ) as response:
                    if response.status == 200:
                        phases["ready"] = memory_sample(CGROUP_ROOT / parent, started)
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(2)
        else:
            raise TimeoutError("Candidate readiness timed out")
        receipt.update(post_prompt(port, spec["model"]))
        receipt["passed"] = True
    except Exception as exc:
        receipt["failure_type"] = type(exc).__name__
        raise SystemExit(
            "CPU runtime candidate smoke failed; see sanitized proof"
        ) from None
    finally:
        if stop_following:
            stop_following()
        if container:
            receipt["memory_evidence"] = memory_evidence(CGROUP_ROOT / parent)
            receipt["memory_phases"] = dict(phases)
        if container and not receipt["passed"]:
            keep_container_log(container, proof.with_name("runtime-container.log"))
        if container:
            subprocess.run(
                ["docker", "rm", "--force", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        proof.write_text(json.dumps(receipt, indent=2) + "\n")
        proof.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.image, args.proof)


if __name__ == "__main__":
    main()
