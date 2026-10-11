"""Remote-runner smoke only; localhost server and public immutable model source."""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


PACKAGE_CHECK = """import importlib.metadata, json, torch
import vllm._C
from vllm import _custom_ops as ops
assert importlib.metadata.version("vllm") == "0.15.1+cpu"
assert importlib.metadata.version("torch") == "2.10.0+cpu"
x = torch.ones((2, 16), dtype=torch.float32)
out = torch.empty((2, 8), dtype=torch.float32)
ops.silu_and_mul(out, x)
assert torch.isfinite(out).all()
assert torch.allclose(torch.nn.functional.linear(x, x), torch.full((2, 2), 16.0))
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
        "live_cluster_certified": False,
    }
    container = None
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
                    "--enforce-eager",
                ],
                timeout=30,
            )
            .decode()
            .strip()
        )
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
                raise ValueError("Candidate stopped before readiness")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=5
                ) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
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
