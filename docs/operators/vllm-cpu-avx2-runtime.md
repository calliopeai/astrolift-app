# AVX2 CPU runtime candidate

This is a prepared manual build, not a published image or a certified cluster
runtime. It adds no cloud capacity and performs no deployment. The workflow
runs only by explicit dispatch; publication defaults to false. Publication,
when selected, happens after the runner's kernel and bounded inference checks.

The build uses upstream vLLM 0.15.1 commit
`1892993bc18e243e2c05841314c5e9c06a80c70d`, an archive checksum, and an
amd64 Ubuntu 22.04 base digest recorded in `scripts/vllm_cpu_runtime/source.json`.
The upstream Dockerfile changes pin that base, set one-thread unbound
CPU defaults, and use upstream’s supported `VLLM_VERSION_OVERRIDE=0.15.1+cpu`
for the checksum-verified release archive, which has no Git version metadata. Build arguments disable AVX512 and enable AVX2; AVX512 BF16,
VNNI and AMX BF16 are all disabled. CMake compile flags must contain `-mavx2`
and must not contain AVX512 or AMX flags. Upstream apt/Python dependency
resolution remains network-dependent; the eventual image digest, package list
and successful checks identify the produced artifact, not a reproducible-build
or signed-provenance claim.

The official [0.15.1 CPU documentation](https://docs.vllm.ai/en/v0.15.1/getting_started/installation/cpu/)
warns that the published x86 CPU image can fail on processors without advanced
AVX512 features. The exact upstream
[CMake AVX2 branch](https://github.com/vllm-project/vllm/blob/1892993bc18e243e2c05841314c5e9c06a80c70d/cmake/cpu_extension.cmake)
and [CPU dtype contract](https://github.com/vllm-project/vllm/blob/1892993bc18e243e2c05841314c5e9c06a80c70d/vllm/platforms/cpu.py)
support preparing an AVX2 float32 candidate. They do not prove compatibility
with a particular node or sufficient memory for a particular model.

The smoke checks the actual package is `0.15.1+cpu`, PyTorch is `2.10.0+cpu`,
and runs a float32 CPU kernel before starting a localhost-only server with
one CPU, 4 GiB memory, 1 GiB KV cache, a 256-token context and one sequence.
It loads an immutable public Qwen/Qwen2.5-0.5B-Instruct revision and requires
an actual bounded chat completion. Failure, including out of memory, prevents
publication. The smoke never supplies Hugging Face or cloud credentials.
A successful runner smoke is still not actual cluster readiness or inference.

Before installing a produced digest, inspect actual node CPU flags (AVX2 and
F16C), allocatable CPU/memory, current requests, image pull access and the
storage prerequisites. A nominal 8 GiB node does not have 8 GiB allocatable.
Use a selector identifying hardware actually checked by the operator. The
shared runtime admission must still remain fail closed without certification.

The current request admission hardcodes CPU `bfloat16`. This float32 candidate
therefore requires a separately reviewed backend runtime dtype choice before
use; do not override the launcher inside the image or weaken certification.
The small-context/concurrency smoke settings also need to be reproduced by the
reviewed deployment configuration. The new image's one-thread defaults do not
prove that a currently unbounded model request fits a small node. After a
future build, record its exact registry manifest digest and use that digest,
then require actual deployment readiness and inference observations.
