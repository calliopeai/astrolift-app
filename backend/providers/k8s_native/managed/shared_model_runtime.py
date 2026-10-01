"""Explicit, pinned Python vLLM runtime admission and secret-free startup argv."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

SUPPORTED_VERSION = "0.15.1"
AUTH_REVISION = "astrolift.io/model-auth-revision"
RUNTIME_PATH = "/opt/astrolift/shared-model"
KEYS_PATH = "/var/run/astrolift/model-auth"
_PINNED_IMAGE = re.compile(r"[^\s]+(?:@sha256:[0-9a-f]{64}|:v?0\.15\.1(?:-[a-z0-9_.-]+)?)\Z")
_LABEL_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?\Z")
_DNS_SEGMENT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_CPU = re.compile(r"([0-9]+(?:\.[0-9]{1,3})?)(m?)\Z", re.ASCII)
_MEMORY = re.compile(r"([0-9]+(?:\.[0-9]{1,3})?)(Ki|Mi|Gi|Ti)?\Z", re.ASCII)


def _resource_requests(cfg):
    cpu, memory = cfg.get("cpu"), cfg.get("memory")
    cpu_match = _CPU.fullmatch(cpu) if isinstance(cpu, str) and len(cpu) <= 32 else None
    memory_match = _MEMORY.fullmatch(memory) if isinstance(memory, str) and len(memory) <= 32 else None
    if cpu_match is None or memory_match is None:
        raise ValueError("Shared models require valid explicit CPU and memory quantities.")
    cores = Decimal(cpu_match[1]) / (1000 if cpu_match[2] else 1)
    memory_bytes = (
        Decimal(memory_match[1]) * {None: 1, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}[memory_match[2]]
    )
    if not 0 < cores <= 256 or not 0 < memory_bytes <= 1024**4:
        raise ValueError("Shared model requests must be positive and bounded to 256 CPUs and 1 TiB memory.")
    return memory_bytes


@dataclass(frozen=True)
class SharedRuntime:
    image: str
    mode: str
    architecture: str
    node_selector: dict[str, str]
    package_version: str


def shared_runtime(runtimes: dict[str, Any], cfg: dict[str, Any], frontend: str) -> SharedRuntime:
    """Operator-certified scheduling capability, never a GPU/CPU inference probe."""
    mode = cfg.get("compute_mode")
    if mode not in ("cpu", "gpu"):
        raise ValueError("Shared models require explicit CPU or GPU compute mode.")
    if not isinstance(runtimes, dict):
        raise ValueError("Shared model runtime declarations are invalid.")
    declaration = runtimes.get(mode)
    if not isinstance(declaration, dict) or declaration.get("hardware_certified") is not True:
        raise ValueError("Shared model runtime hardware admission is not configured for this cluster.")
    if frontend != "python" or declaration.get("version") != SUPPORTED_VERSION:
        raise ValueError("Shared models require the supported pinned vLLM 0.15.1 Python frontend.")
    package_version = declaration.get("package_version", "0.15.1+cpu" if mode == "cpu" else SUPPORTED_VERSION)
    if (mode == "cpu" and package_version != "0.15.1+cpu") or (
        mode == "gpu"
        and package_version != SUPPORTED_VERSION
        and (not isinstance(package_version, str) or not re.fullmatch(r"0\.15\.1\+cu[0-9]{3}", package_version))
    ):
        raise ValueError("Shared runtime package version must match its supported CPU or CUDA release build.")
    if cfg.get("task", "generate") != "generate":
        raise ValueError("The pinned shared model runtime currently supports generation tasks only.")
    image = declaration.get("image")
    if not isinstance(image, str) or not _PINNED_IMAGE.fullmatch(image) or len(image) > 512:
        raise ValueError("Shared models require a pinned image for the declared supported runtime.")
    arch = declaration.get("architecture")
    selectors = declaration.get("node_selector")
    if arch not in ("amd64", "arm64") or not isinstance(selectors, dict) or not 1 <= len(selectors) <= 16:
        raise ValueError("Shared models require architecture and certified hardware node selectors.")
    if not any(key != "kubernetes.io/arch" for key in selectors):
        raise ValueError("Architecture alone does not certify model runtime hardware compatibility.")
    for key, value in selectors.items():
        if not isinstance(key, str) or len(key) > 317 or not _LABEL_NAME.fullmatch(key.rsplit("/", 1)[-1]):
            raise ValueError("Shared model hardware selector key is invalid.")
        if "/" in key:
            prefix, _ = key.rsplit("/", 1)
            if not prefix or len(prefix) > 253 or any(not _DNS_SEGMENT.fullmatch(part) for part in prefix.split(".")):
                raise ValueError("Shared model hardware selector key prefix is invalid.")
        if not isinstance(value, str) or not _LABEL_NAME.fullmatch(value):
            raise ValueError("Shared model hardware selector value is invalid.")
    if selectors.get("kubernetes.io/arch", arch) != arch:
        raise ValueError("Shared model hardware selector architecture disagrees with its runtime.")
    memory_bytes = _resource_requests(cfg)
    gpu = cfg.get("gpu")
    if mode == "gpu" and (not isinstance(gpu, int) or isinstance(gpu, bool) or gpu < 1):
        raise ValueError("GPU runtime requires a positive GPU request.")
    if mode == "cpu":
        if gpu != 0 or cfg.get("tensor_parallel_size", 1) != 1 or cfg.get("mig_profile") or cfg.get("gpu_type"):
            raise ValueError("CPU runtime cannot request GPU placement or GPU tensor parallelism.")
        cache = cfg.get("cpu_kv_cache_gib")
        if not isinstance(cache, int) or isinstance(cache, bool) or not 1 <= cache <= 1024:
            raise ValueError("CPU runtime requires bounded explicit KV-cache space in GiB.")
        if memory_bytes <= cache * 1024**3:
            raise ValueError("CPU memory request must exceed its KV-cache space; model-fit remains unverified.")
    revision = cfg.get("model_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Shared models require an immutable 40-hex model revision.")
    return SharedRuntime(image, mode, arch, {**selectors, "kubernetes.io/arch": arch}, package_version)


# Kept as source rather than executed in the control plane. The ConfigMap runs
# in the pinned runtime, and only non-secret model flags enter process argv.
LAUNCHER = r'''"""Launch only the declared vLLM Python frontend with a fixed auth snapshot."""
import importlib.metadata
import os
import re
import sys

from astrolift_shared_model_auth import load_key_snapshot

class SecretValue(str):
    def __repr__(self):
        return "'<redacted>'"

class SecretKeys(list):
    def __repr__(self):
        return "[<redacted>]"

def main():
    mode = os.environ.get("ASTROLIFT_MODEL_COMPUTE_MODE")
    expected = os.environ.get("ASTROLIFT_MODEL_RUNTIME_PACKAGE_VERSION", "")
    supported = mode == "cpu" and expected == "0.15.1+cpu" or mode == "gpu" and (
        expected == "0.15.1" or re.fullmatch(r"0\.15\.1\+cu[0-9]{3}", expected))
    if not supported or importlib.metadata.version("vllm") != expected:
        raise RuntimeError("Unsupported shared model runtime version.")
    if len(sys.argv) > 128 or any(len(arg) > 512 for arg in sys.argv):
        raise RuntimeError("Shared model startup arguments exceed bounds.")
    snapshot = load_key_snapshot(expected_revision=int(os.environ["ASTROLIFT_MODEL_AUTH_REVISION"]))
    os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
    from vllm.entrypoints.openai.cli_args import make_arg_parser, validate_parsed_serve_args
    from vllm.entrypoints.openai.api_server import run_server
    from vllm.utils.argparse_utils import FlexibleArgumentParser
    import uvloop
    parser = make_arg_parser(FlexibleArgumentParser())
    args = parser.parse_args(sys.argv[1:])
    args.api_key = SecretKeys(SecretValue(key) for key in (snapshot.operator_key, *snapshot.subscription_keys))
    args.middleware = ["astrolift_shared_model_auth.SharedModelAuth"]
    validate_parsed_serve_args(args)
    uvloop.run(run_server(args))

if __name__ == "__main__":
    main()
'''
