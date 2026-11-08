# NVKE operator agent through Astro

Astro submits native tooling to a dedicated, site-bound operator agent. The web
server and Temporal worker never execute the native toolchain themselves. The
agent protocol and installation instructions live in
[`astrolift-opscode/kubernetes/nvke/OPERATOR-AGENT.md`](https://github.com/calliopeai/astrolift-opscode/blob/feature/nvke-2656-full-surface/kubernetes/nvke/OPERATOR-AGENT.md).

This is a local integration under development. Agent connection management is
wired into the site screen and locally qualified in Storybook and Chromium
against real GraphQL/Postgres/mTLS. The native command workbench and original-receipt recovery are also wired into
the site screen and locally browser-qualified for one actual native utility.
Private browser artifact downloads, reviewed job reconciliation UI, agent-driven
Linux bootstrap/install, production supply-chain checks and deployed acceptance
remain separate qualification work.

## Native tooling in the site screen

The Native tooling section exposes all 17 source-pinned commands and their
declared positional arguments, options and choices. Choose a command, edit its
complete private JSON envelope or import a UTF-8 JSON file, then review the site
and agent versions, persistent instance, bundle digest, command, exact request
SHA-256 and observation deadline. Infrastructure-changing requests require an
explicit acknowledgement; a declared `dry-run: true` is reviewed as a dry-run.
Native option validation still runs at the API and agent boundaries.

Supply file contents as UTF-8 strings in `files` and refer to them as
`input:NAME`. Outputs use `output:NAME`; retained artifacts use
`job:UUID:NAME`; directory inputs accept the declared pinned `source:` references.
Host file paths are refused. User-edited/imported text is submitted unchanged.
Private text is cleared when review opens and is excluded from browser recovery
storage and Apollo cache. Cancelling review discards that private snapshot.

After an unconfirmed submission, refresh and use **Look up original receipt**.
This reads the existing request identity without submitting another job, even
when the configured agent was subsequently removed or changed. If no receipt is
visible, the identity remains unresolved; any explicit resubmission must retain
the exact original text, versions, deadline and acknowledgement. The local
browser qualification deliberately drops the admission response, recovers the
original receipt, and observes exactly one native site-spec generation process
through real PostgreSQL/Temporal/mTLS. Other native command-family effects need
separate qualification.

## Connect an agent

The site screen’s operator-agent section exposes configuration, verification,
enable/disable and removal with reviewed site/authority versions and agent pins.
Replacement credentials are cleared from visible fields when review opens and
are never displayed in confirmation. Cancelling or changing caller/site scope
discards the draft. An unconfirmed write blocks further writes until refresh;
refresh reads the existing authority instead of repeating configuration. Private
NVKE HTTP requests refuse redirects and bypass the browser HTTP cache.


1. Start one agent for the existing Astro site GUID with the pinned native bundle.
   Keep its state directory and instance identity across restarts.
2. Issue its HTTPS server certificate and a dedicated mTLS client certificate.
   Configure the trusted client CA and exact client leaf fingerprint on the agent.
   Astro requires hostname and server-chain verification.
3. Call `configureNvkeOperatorAuthority` with the reviewed caller, site version,
   authority version (`0` for creation), HTTPS base URL, persistent instance GUID,
   bundle manifest SHA-256 and write-only CA/client certificate/key fields.
4. Refresh the returned site version, then call `verifyNvkeOperatorAuthority`.
   Verification compares the site, instance, bundle, native revision, job format
   and complete command contract. It launches no process and proves no
   infrastructure readiness.

Configuration, credential rotation, enable/disable and removal advance both the
site and authority versions and clear verification. Omitted credentials preserve
the sealed existing pair. Removal soft-deletes the authority. A replacement has a
new identity. Controller, datastore, metrics and native Temporal credentials are
never inferred for this connection.

## Submit a native command

`astroliftNvkeOperatorContract` exposes every declared command, positional input,
option type, fixed choice and required option. `requestNvkeOperatorJob` accepts
one bounded JSON string, preserving native numbers and file contents without
browser floating-point conversion. For example:

```json
{
  "format": 1,
  "revision": "92c39b8ad563ebe5ef705f781b3038b901aba10f",
  "siteId": "REVIEWED_SITE_GUID",
  "command": "generate.sitespec",
  "positionals": ["my-site"],
  "options": {"nodes": "input:nodes.csv"},
  "files": {"nodes.csv": "name,ip\ncp-1,192.0.2.10\n"}
}
```

Use declared `input:`, `output:`, retained `job:` or pinned `source:` artifact
references. Host paths, shell commands, arbitrary environment variables and
unbounded Helm options are refused. Native kubeconfigs must use inline
credentials; executable credential plugins, host-file references and TLS bypasses
are refused by the agent.

Admission binds the actor, API token, request UUID, exact request, reviewed site
and authority versions, acknowledgement and observation deadline. Preserve the
request UUID and these reviewed inputs when recovering an uncertain admission
reply. Changed inputs or a different token cannot adopt the original intent.
Requests and peer receipts are encrypted at rest. Temporal receives operation IDs
and generations only. Command documents, endpoint credentials and client keys are
redacted according to their GraphQL schema positions, including aliased variables.

Infrastructure-changing bootstrap, install and access-configuration commands
require explicit `acknowledgeInfrastructureChange`. Another unresolved effectful
intent blocks a new effectful job. An already successful bootstrap cannot be
submitted again through another request UUID. Process completion requires a
separate check of the resulting infrastructure.

## Observe, cancel and recover

Use existing operation metadata, private result and cancellation APIs. Original
caller, token, site, agent identity and credential authority are checked before
and after each agent exchange. After a submission may have reached the agent,
repeated attempts only read that original job UUID. A lost response never
justifies another native invocation. The agent compares an opaque keyed intent
digest on submission, observation, cancellation and artifact access.

Cancellation before dispatch prevents submission. After dispatch, the agent can
stop only its owned local process group; remote effects may survive and require
review. An unresolved result remains uncertain and fences further infrastructure
changes. Observation deadlines do not promise remote rollback.

An authorized current administrator can call `reconcileNvkeOperatorJob` with
reviewed current site/authority/operation versions after the original caller loses
access. This acquires the same operation lease as normal execution and reads the
original site, persistent instance, bundle and intent. It never submits a new job.
A matching finished receipt can settle the operation; authoritative absence on
that exact agent records `OPERATOR_NOT_ADMITTED`. A running or conflicting receipt
remains unresolved. Credential rotation is permitted for this explicitly reviewed
read path; changing the original agent identity is refused.

`astroliftNvkeOperatorArtifactText` returns a bounded, base64-encoded chunk from a
retained private artifact. Review the operation and current authority versions for
each request. Offsets and sizes are decimal strings. Verify the complete assembled
file against the receipt SHA-256, save credentials privately, and keep private
diagnostics out of issue comments and workflow history.


## Remote CLI

`astro nvke agent catalog` shows the full connected command contract. Use
`agent configure/get/verify/enable/disable/remove` for agent authority lifecycle.
`agent request --file PRIVATE.json --request-file NEW-RECOVERY.json` takes the
complete envelope with reviewed `--site`, `--site-version` and
`--authority-version`. Acknowledged infrastructure changes use
`--acknowledge-infrastructure-change`. `agent recover --request-file FILE` reads
the original receipt without another admission and needs no input file.

Use `nvke operation get/wait/cancel/result` for durable operation state and private
artifact metadata. `nvke agent reconcile OPERATION_GUID` and
`nvke agent artifact OPERATION_GUID --name ARTIFACT --output NEW-PRIVATE-FILE`
require current reviewed site/authority versions and operation `--version`.
Artifact downloads stream to an exclusive protected local file, check original
size/hash and retain private partial files on failure. `--max-bytes` defaults to
64 MiB and can be raised explicitly. The remote agent API refuses redirects.

The compiled local qualification covers CLI site creation, actual TLS agent
verification, one native generation run, deliberately lost admission reply,
original recovery without the input file, private output, read-only settlement
from an explicit uncertain fixture and revoked-token refusal. Other native
command families and infrastructure effects remain separate qualification work.
