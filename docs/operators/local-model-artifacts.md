# Local model file import and delivery

The #2266 source path imports files into the existing install object store. It
creates an organization-owned local artifact, verifies immutable object versions,
and prepares private delivery for a separately admitted cluster model. It does
not create pipeline artifacts, buckets, storage classes, CPU/GPU pools or public
model endpoints. Byte verification is not model compatibility, memory fit,
inference success or runtime health. Serving remains pending until the existing
model readiness checks observe the actual runtime.

## Operator prerequisites

The install must provide `AWS_STORAGE_BUCKET_NAME`, an S3 region through
`AWS_S3_REGION_NAME`, `AWS_REGION` or `AWS_DEFAULT_REGION` (otherwise the existing
install default is `us-east-1`), and the existing backend AWS credential chain.
An optional `AWS_S3_ENDPOINT_URL` must use HTTPS. The bucket must report versioning
`Enabled` and all four S3 Block Public Access settings enabled. A missing API,
public bucket, suspended versioning or inaccessible storage refuses import. The
implementation does not change bucket configuration or substitute a per-organization
legacy blob store. Objects use server-derived immutable organization/artifact/file
GUID keys under `astrolift/model-artifacts/`.

The backend needs versioning/public-access-block metadata reads, checksum-enabled
object HEAD, PutObject and version-specific GetObject on its import prefix.
Server-side encryption is AES256, or the existing `AWS_S3_OBJECT_PARAMETERS`
`SSEKMSKeyId` selects KMS. KMS checksum reads require the applicable KMS grants.
Browser upload additionally needs operator-configured CORS for the product origin,
PUT, and the returned checksum/encryption headers. No bucket/IAM/CORS changes are
performed by these mutations. Do not use a public static-file bucket.

The cluster must have a storage class/PVC capability and an explicitly certified
CPU or GPU Python vLLM 0.15.1 runtime. Local delivery additionally requires its
image pinned by SHA-256 digest. The existing runtime hardware and resource admission
still applies; CPU/GPU selection does not prove that the model fits. The same
certified runtime image executes the standard-library downloader init container.
No caller-supplied downloader image, shell, archive extraction, pickle weights,
Python model code or remote-code loader is admitted.

## Product/API sequence

Every source operation requires a fresh active installation platform operator
(Django superuser), current `org.update` and `cluster.update` authority in the
selected organization, and the existing bearer ceiling. A bearer must retain its
`admin` scope and live selected-org membership. Ordinary organization owner grants
do not authorize imports. A waited-on artifact lock rechecks this admission before
verification writes; source storage observations remain separate from model health.

1. `beginLocalModelArtifact` accepts the organization GUID, display name and an
   immutable file manifest. Each entry has a flat filename, SHA-256 and decimal
   string `sizeBytes`, avoiding GraphQL's 32-bit integer limit.
2. `authorizeLocalModelUploads` requires artifact GUID and `expectedVersion`.
   It returns explicit private 900-second PUT URLs and required headers. Upload
   each file body directly; preserve browser streaming and compute hashes in bounded
   chunks. The API does not receive multi-gigabyte file bodies.
3. `finalizeLocalModelArtifact` rechecks every file's exact length, full-object
   SHA-256 and non-null S3 VersionId. Only a complete manifest becomes `verified`.
   A partial/failed check retains `uploading`; retry the same artifact/version.
   Verified artifacts cannot issue new write grants. Successful repeated finalization
   preserves the same verified identity/version.
4. Model hosting uses `localArtifactId` and `expectedArtifactVersion` as an
   alternative to Hugging Face repository/revision/connection input. The shared
   model's public config holds only source kind, artifact GUID/version/digest and
   stable served-model identity. Download URLs never enter persisted control config
   or Temporal activity arguments/results.

Supported files are flat safetensors weights, optional safetensors index JSON,
`config.json`, tokenizer JSON/model, generation/tokenizer/special-token config,
`vocab.json`, `merges.txt` and `chat_template.jinja`. A manifest requires config,
a tokenizer and weights. Limits are 256 files, 5,000,000,000 bytes per file
(single PUT; no multipart), 64 MiB per JSON file and 200 GiB total. This is a
supported file set, not arbitrary-directory or arbitrary-model compatibility.

`astroliftLocalModelArtifactsPage` exposes admin-scoped metadata with server
cursors. It omits storage keys, version receipts, URLs and credentials. The private
upload authorization response is a capability; do not log it or save it in workflow
history. Previously issued URLs expire according to their original grant; this
path does not claim immediate URL revocation or automatic abandoned-object deletion.

## Browser transfer privacy

Keep upload URLs and their required headers inside the transfer controller. Display
file progress and safe errors; keep private grants out of rendered text, links,
attributes, log messages and copied diagnostics. Replay's network-event filtering
does not scrub arbitrary DOM attributes.

Browser telemetry excludes signed transfer URLs from error events, breadcrumbs and
custom replay network records. Matching spans retain identifiers and timing with
private descriptions, attributes and links removed. Replay masks inputs/text and
disables network body capture. SDK tests and an owned Chromium fixture exercise
successful, rejected and interrupted PUT requests, inspect decoded replay payloads,
and preserve ordinary requests/events. This proves the configured telemetry path;
deployment and live storage acceptance remain separate checks.

## Delivery and lifecycle

The guarded worker generates version-specific GET URLs only after current source
and owner admission. Each URL generation rechecks the original artifact version
and install storage source. The provider separately binds the private plan to the
exact organization/cluster/service placement and admitted artifact/version/digest.
Foreign delivery Secrets are refused before credential or Kubernetes writes.

A service-owned Kubernetes Secret contains the short-lived private plan; a trusted
runtime ConfigMap contains only fixed downloader and launcher sources. Downloads
are bounded, reject redirects, stream checksum verification and publish the complete
SHA directory atomically. Retry rehashes cached bytes and revalidates configuration;
conflicting cached data and remote-code configurations refuse. No signed URL enters
argv, ConfigMaps, public metadata or downloader error output.

The vLLM container mounts `/models/<manifest SHA>` read-only, serves the stable
`local-<artifact GUID>` model ID, uses the safetensors loader and disables Hugging
Face network access. Its existing independent operator/subscriber key snapshot and
readiness policy remain in effect. The init container alone mounts the PVC writable.
The private delivery Secret remains available for pending/retried init; later
reconciliation refreshes expired grants. Deprovision removes only a matching
service-owned delivery Secret and retains the weight PVC unless explicit data
removal is requested. This is not an external object-store deletion guarantee.

Native tests use an owned HTTPS S3 protocol server, actual boto3 signing, private
Postgres and the actual standalone downloader. Recording Kubernetes tests prove
placement, manifests and refusals. These are not live cloud storage, Kubernetes
hydration or vLLM inference acceptance.

References: [S3 PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html),
[S3 HeadObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html),
[vLLM 0.15.1 serve](https://docs.vllm.ai/en/v0.15.1/cli/serve/).
