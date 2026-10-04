"""Operator-declared, secret-free shared runtime settings and serving controls."""

import re
from enum import Enum

import strawberry


@strawberry.enum
class ModelRuntimeMode(Enum):
    CPU = "cpu"
    GPU = "gpu"


@strawberry.enum
class ModelRuntimeArchitecture(Enum):
    AMD64 = "amd64"
    ARM64 = "arm64"


@strawberry.enum
class ModelDtype(Enum):
    AUTO = "auto"
    FLOAT16 = "float16"
    BFLOAT16 = "bfloat16"
    FLOAT32 = "float32"


@strawberry.input
class ModelRuntimeSelectorInput:
    key: str
    value: str


@strawberry.type
class ModelRuntimeSelector:
    key: str
    value: str


@strawberry.input
class ModelRuntimeDeclarationInput:
    image: str
    version: str
    package_version: str
    architecture: ModelRuntimeArchitecture
    node_selector: list[ModelRuntimeSelectorInput]
    supported_dtypes: list[ModelDtype]
    default_dtype: ModelDtype
    default_max_model_len: int
    max_model_len_ceiling: int
    default_max_num_seqs: int
    max_num_seqs_ceiling: int
    cpu_request_ceiling: str
    memory_request_ceiling: str
    gpu_count_ceiling: int
    hardware_certified: bool
    hardware_attested: bool = False
    hardware_evidence: str | None = None


@strawberry.type
class ModelRuntimeDeclaration:
    image: str
    version: str
    package_version: str
    architecture: ModelRuntimeArchitecture
    node_selector: list[ModelRuntimeSelector]
    supported_dtypes: list[ModelDtype]
    default_dtype: ModelDtype
    default_max_model_len: int
    max_model_len_ceiling: int
    default_max_num_seqs: int
    max_num_seqs_ceiling: int
    cpu_request_ceiling: str
    memory_request_ceiling: str
    gpu_count_ceiling: int
    hardware_certified: bool
    hardware_evidence: str | None


def declaration_config(value, mode):
    """An explicit operator attestation is evidence recorded, never a hardware probe."""
    from k8s_native.managed.shared_model_runtime import shared_runtime

    evidence = value.hardware_evidence
    if evidence is not None:
        if (
            not isinstance(evidence, str)
            or not 1 <= len(evidence.strip()) <= 2048
            or any(ord(c) < 32 for c in evidence)
        ):
            raise ValueError("Hardware evidence must be a bounded visible reference, without credentials.")
        evidence = evidence.strip()
    if type(value.hardware_certified) is not bool or type(value.hardware_attested) is not bool:
        raise ValueError("Hardware certification and attestation must be explicit booleans.")
    if value.hardware_certified and (not value.hardware_attested or evidence is None):
        raise ValueError(
            "Hardware certification requires an explicit operator attestation and evidence reference."
        )
    if (
        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}", value.image)
        or "://" in value.image
    ):
        raise ValueError(
            "Runtime setup requires an OCI image pinned by SHA-256 digest, without URL credentials."
        )
    selectors = {entry.key: entry.value for entry in value.node_selector}
    if len(selectors) != len(value.node_selector):
        raise ValueError("Runtime selectors must have unique keys.")
    dtypes = [entry.value for entry in value.supported_dtypes]
    if (
        not dtypes
        or len(dtypes) > 4
        or len(set(dtypes)) != len(dtypes)
        or value.default_dtype.value not in dtypes
    ):
        raise ValueError("Declare unique supported dtypes and a supported default dtype.")
    if any(
        type(getattr(value, key)) is not int
        for key in (
            "default_max_model_len",
            "max_model_len_ceiling",
            "default_max_num_seqs",
            "max_num_seqs_ceiling",
        )
    ):
        raise ValueError("Runtime context and concurrency limits must be integers.")
    if not 256 <= value.default_max_model_len <= value.max_model_len_ceiling <= 131072:
        raise ValueError("Runtime context defaults and ceiling must be within 256–131072 tokens.")
    if not 1 <= value.default_max_num_seqs <= value.max_num_seqs_ceiling <= 4096:
        raise ValueError("Runtime concurrency defaults and ceiling must be within 1–4096 sequences.")
    if (
        type(value.gpu_count_ceiling) is not int
        or not 0 <= value.gpu_count_ceiling <= 16
        or (mode == "cpu") != (value.gpu_count_ceiling == 0)
    ):
        raise ValueError("CPU runtimes require a zero GPU ceiling; GPU runtimes require a ceiling of 1–16.")
    declaration = {
        "image": value.image,
        "version": value.version,
        "package_version": value.package_version,
        "architecture": value.architecture.value,
        "node_selector": selectors,
        "hardware_certified": value.hardware_certified,
        "hardware_evidence": evidence,
        "supported_dtypes": dtypes,
        "default_dtype": value.default_dtype.value,
        "default_max_model_len": value.default_max_model_len,
        "max_model_len_ceiling": value.max_model_len_ceiling,
        "default_max_num_seqs": value.default_max_num_seqs,
        "max_num_seqs_ceiling": value.max_num_seqs_ceiling,
        "cpu_request_ceiling": value.cpu_request_ceiling,
        "memory_request_ceiling": value.memory_request_ceiling,
        "gpu_count_ceiling": value.gpu_count_ceiling,
    }
    # Validate the supported runtime syntax without representing this as a probe
    # or changing the caller's certification decision.
    shared_runtime(
        {mode: {**declaration, "hardware_certified": True}},
        {
            "compute_mode": mode,
            "cpu": value.cpu_request_ceiling,
            "memory": value.memory_request_ceiling,
            "gpu": 0 if mode == "cpu" else 1,
            "cpu_kv_cache_gib": 1,
            "model_revision": "a" * 40,
            "dtype": value.default_dtype.value,
            "runtime_controls_revision": 1,
            "max_model_len": value.default_max_model_len,
            "max_num_seqs": value.default_max_num_seqs,
        },
        "python",
    )
    return declaration


def declaration_to_type(config):
    """Whitelist only declared runtime fields; never expose provider/auth JSON."""
    return ModelRuntimeDeclaration(
        image=config["image"],
        version=config["version"],
        package_version=config["package_version"],
        architecture=ModelRuntimeArchitecture(config["architecture"]),
        node_selector=[
            ModelRuntimeSelector(key=key, value=value) for key, value in config["node_selector"].items()
        ],
        supported_dtypes=[ModelDtype(value) for value in config["supported_dtypes"]],
        default_dtype=ModelDtype(config["default_dtype"]),
        default_max_model_len=config["default_max_model_len"],
        max_model_len_ceiling=config["max_model_len_ceiling"],
        default_max_num_seqs=config["default_max_num_seqs"],
        max_num_seqs_ceiling=config["max_num_seqs_ceiling"],
        cpu_request_ceiling=config["cpu_request_ceiling"],
        memory_request_ceiling=config["memory_request_ceiling"],
        gpu_count_ceiling=config["gpu_count_ceiling"],
        hardware_certified=config.get("hardware_certified") is True,
        hardware_evidence=config.get("hardware_evidence"),
    )
