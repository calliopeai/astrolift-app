# Model catalogue and observations (#2214)

`astroliftHuggingFaceModels` reads the public Hub API on the server. It accepts
search, author, pipelineTag, library, license, gated and sortBy filters. Supported
sorts are downloads, likes, lastModified, createdAt and trendingScore. `first` is
1–30; `after` is a signed, caller/filter-bound cursor valid for 20 minutes. One
request fetches one page. The fixed HTTPS origin, no redirects, five-second
timeout and 1 MiB response limit apply to search and detail. No caller URL,
browser API key, saved Hub token, model file download or remote code execution is
part of this read.

`astroliftHuggingFaceModel(repoId, revision)` resolves a repository revision to
its actual immutable SHA. A missing SHA or mismatching requested commit fails
closed. Search metadata may omit SHA; resolve detail before deployment. The
catalogue exposes safe upstream task, architecture, licence and gated metadata,
not raw cards/configuration. Licence metadata is not legal approval, and gating
does not prove accepted terms or download access. Compatibility is UNKNOWN;
architecture/task tags do not prove support, CPU/GPU placement or hardware fit.

AVAILABLE, NO_DATA, RATE_LIMITED and UNAVAILABLE distinguish observations from
transport failure. Missing metadata is nullable, never filled with invented
counts. Downloads and likes are public Hub counts, not serving usage. Retry
delays only come from an actual bounded numeric upstream Retry-After header.
An observation timestamp is the read attempt time, not an upstream freshness
guarantee. Reads require a live authenticated account; bearer sessions retain
their actual live organization membership and credential validity. Catalogue
access grants no model/cluster/subscription authority.

Primary contracts: [Hub API client](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api),
[official search/pagination implementation](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/hf_api.py),
[gated repositories](https://huggingface.co/docs/hub/models-gated).
