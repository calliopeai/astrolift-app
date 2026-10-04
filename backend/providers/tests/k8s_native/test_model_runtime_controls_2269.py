"""Exact float32 CPU flags and declared request bounds before credentials/writes."""

from dataclasses import replace

import pytest

from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver
from k8s_native.managed.shared_model_runtime import shared_runtime

from .test_shared_model_runtime import declaration, object_of, setup


def configured():
    runtimes = declaration()
    runtimes["cpu"].update(
        package_version="0.15.1+cpu",
        supported_dtypes=["float32"],
        default_dtype="float32",
        default_max_model_len=256,
        max_model_len_ceiling=512,
        default_max_num_seqs=1,
        max_num_seqs_ceiling=2,
        cpu_request_ceiling="2",
        memory_request_ceiling="4Gi",
        gpu_count_ceiling=0,
    )
    return runtimes


def bounded(spec):
    return replace(
        spec,
        config={
            **spec.config,
            "cpu": "1",
            "memory": "4Gi",
            "cpu_kv_cache_gib": 1,
            "dtype": "float32",
            "max_model_len": 256,
            "max_num_seqs": 1,
            "runtime_controls_revision": 1,
        },
    )


def test_declared_avx2_float32_manifest_uses_exact_reviewed_serving_flags():
    _, cluster, secrets, spec = setup()
    driver = VLLMDriver(
        config=VLLMConfig(cluster_driver=cluster, secrets_backend=secrets, shared_runtimes=configured())
    )
    result = driver.provision(bounded(spec))
    assert result.ok, result.message
    args = object_of(cluster, "apps/v1/Deployment")["spec"]["template"]["spec"]["containers"][0]["args"]
    for flag, value in (
        ("--dtype", "float32"),
        ("--max-model-len", "256"),
        ("--max-num-seqs", "1"),
        ("--runner", "generate"),
    ):
        assert args[args.index(flag) + 1] == value


@pytest.mark.parametrize(
    "changes",
    [
        {"dtype": "bfloat16"},
        {"max_model_len": 1024},
        {"max_num_seqs": 3},
        {"max_model_len": True},
        {"cpu": "3"},
        {"memory": "8Gi"},
        {"runtime_controls_revision": 2},
    ],
)
def test_undeclared_or_excessive_controls_refuse_before_any_secret_or_kubernetes_write(changes):
    _, cluster, secrets, spec = setup()
    spec = bounded(spec)
    driver = VLLMDriver(
        config=VLLMConfig(cluster_driver=cluster, secrets_backend=secrets, shared_runtimes=configured())
    )
    result = driver.provision(replace(spec, config={**spec.config, **changes}))
    assert not result.ok
    assert not secrets.writes and not cluster.writes


def test_legacy_declaration_preserves_omitted_control_contract_but_cannot_claim_new_controls():
    _, _, _, spec = setup()
    assert shared_runtime(declaration(), spec.config, "python").mode == "cpu"
    with pytest.raises(ValueError, match="not declared"):
        shared_runtime(declaration(), bounded(spec).config, "python")
