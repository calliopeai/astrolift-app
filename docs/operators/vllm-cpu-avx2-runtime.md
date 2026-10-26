# AVX2 CPU runtime candidate

This is a prepared manual build, not a published image or a certified cluster
runtime. It adds no cloud capacity and performs no deployment. Pull requests
that change the build inputs run the build and smoke checks. Registry publication
requires an explicit workflow dispatch with publication enabled, which defaults
to false, and happens after the runner's kernel and bounded inference checks.

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
and checks the registered float32 CPU SwiGLU kernel against PyTorch's numerical
reference before starting a localhost-only server with
one CPU, 4 GiB memory, 1 GiB KV cache, a 256-token context and one sequence.
It loads an immutable public Qwen/Qwen2.5-0.5B-Instruct revision and requires
an actual bounded chat completion. Failure, including out of memory, prevents
publication. The smoke never supplies Hugging Face or cloud credentials.
A successful runner smoke is still not actual cluster readiness or inference.

The server runs under a cgroup parent of its own, fresh for each run, which
sets no limit and outlives the container. At the end of the smoke, before the
container is removed, the proof's `memory_evidence` records the parent's
`peak_bytes` (`memory.peak`), `current_bytes` (`memory.current`) and the
`max`, `oom`, `oom_kill` and `oom_group_kill` counts from `memory.events`, as
integers only. `oom` counts the container reaching its own 4 GiB limit, where
the parent's peak sits at or a few KiB above 4 GiB; an `oom_kill` without an
`oom` is the host's OOM killer. `max` counts reclaim at the limit, so a passing
run with `max` above zero reached 4 GiB and stayed up by reclaiming. Both byte
values include page cache, and after a stop `current_bytes` is only what the
exited processes left charged. A host without readable cgroup v2 files records
`{"unavailable": "<exception type>"}`; the smoke's result never depends on the
evidence.

The proof's `memory_phases` shows what fills the 4 GiB. It samples the
parent's `memory.current` and the `anon`, `file`, `shmem`, `file_mapped`,
`active_file`, `inactive_file`, `kernel` and `slab` values of `memory.stat` at
`before_engine_start`, `weights_loaded`, `kv_cache_allocated` and `warmup`, each
taken when the candidate's own log line for that point first appears, and at
`ready`, the first healthy poll. The log is read through `docker logs
--follow` in lines of at most 4 KiB and 8 MiB in all, and no log text is kept.
Each sample carries `elapsed_s` since the server started. `file` is page cache
and includes `shmem`; without swap, `anon` cannot be reclaimed, while the
kernel reclaims `inactive_file` first and then `active_file`, so a run whose
peak sits at 4 GiB may still be mostly reclaimable cache. A point whose line
never appears is left out, and an unreadable file records
`{"unavailable": "<exception type>"}`.

During readiness, every poll also reads the parent's `oom_kill` count. The
parent is fresh, so any kill is this candidate's, and vLLM does not restart an
engine the OOM killer took; the API server can stay up without ever serving
`/health`. A rise stops the smoke at that poll with `failure_type`
`OomKillDuringReadiness` and `outcome` `oom_kill during readiness` instead of
waiting out the 900 s deadline. Where the count cannot be read, readiness
waits as before.

Every proof also records `host`, read once at the start of the smoke from
`/proc/cpuinfo` and `/proc/meminfo` with a bound on each: `cpu_vendor`,
`cpu_model`, `logical_cpus`, `physical_cores`, `mem_total_bytes` and
`isa_flags`, which says for each of `avx2`, `avx512f`, `avx512_bf16`,
`avx512_vnni`, `amx_tile`, `amx_bf16` and `amx_int8` whether every logical
CPU lists it. No other flag or field leaves the host; the vendor and model must
match narrow printable patterns, or the proof records
`{"unavailable": "<exception type>"}`. The runner's region is not visible to
the smoke; it is in the job log's set-up step.

Before installing a produced digest, inspect actual node CPU flags (AVX2 and
F16C), allocatable CPU/memory, current requests, image pull access and the
storage prerequisites. A nominal 8 GiB node does not have 8 GiB allocatable.
Use a selector identifying hardware actually checked by the operator. The
shared runtime admission must still remain fail closed without certification.

Typed runtime admission now checks an installation Super-admin's declaration
of supported data types and bounded context/concurrency values. An AVX2 float32
candidate must be declared with `FLOAT32` support and the actual checked hardware
selector before use. The small test preset requests float32, a 256-token context
and one sequence; admission binds those values, the immutable source and the
current cluster/provider. Saving the declaration does not build or certify the
image, and its one-thread defaults do not prove model fit on a small node.

After a future build, record and declare its exact registry manifest digest.
Verify the selected nodes, image access and capacity before deployment, then
require actual deployment readiness and inference observations. Keep launcher
arguments and certification checks intact.
