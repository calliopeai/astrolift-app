# Task result artifacts

A run can attach evidence to its result: a JUnit report, coverage, a
conformance report or another named text file (#2488). The runner sends the
files inline with its terminal callback. The controller validates the whole
list, stores it in the same transaction as the result, and serves it to API
readers and the CLI.

## Sending artifacts

Add `artifacts` to the terminal callback (`status: "completed"` or `"failed"`)
posted to the task's callback URL with its task-scoped callback credential or
its assigned dispatcher credential:

```json
{
  "status": "completed",
  "result": "pr_opened",
  "artifacts": [
    {
      "name": "test-report.xml",
      "kind": "junit",
      "media_type": "application/xml",
      "content": "<testsuites>...</testsuites>",
      "sha256": "SHA-256 of the UTF-8 bytes of content, 64 lowercase hex characters"
    }
  ]
}
```

| Field | Rule |
| --- | --- |
| `name` | 1–128 ASCII letters, digits, `.`, `_` or `-`, starting with a letter or digit. Unique within the task; no path separators. |
| `kind` | `junit`, `coverage`, `conformance-json` or `other`. |
| `media_type` | `type/subtype` with RFC 6838 names, optionally followed by `;` and parameters in printable ASCII. At most 255 characters. |
| `content` | UTF-8 text without NUL characters. `conformance-json` content must parse as JSON, without `NaN` or `Infinity`. |
| `sha256` | The SHA-256 of the UTF-8 bytes of `content`, in lowercase hexadecimal. A mismatch is refused. |

All five fields are required and no others are accepted. A task holds at most
16 artifacts, each at most 512 KiB (524,288 UTF-8 bytes), 1 MiB (1,048,576
bytes) in total. The callback body keeps its 2 MiB limit, and JSON escaping of
the content counts against it. Binary files are not accepted.

Validation runs before anything is written. An artifact over a size limit is
refused with 413 and any other invalid entry with 400; either refuses the whole
callback, its result included, so the task stays running and the runner can
send a corrected callback. Artifacts accompany a terminal status only: a
non-empty list on a heartbeat is refused with 400. A terminal callback that is
refused for any other reason (cancellation pending, timeout passed, task
already terminal) stores no artifacts, and a later terminal callback is
refused with 409 like any late result, so a task records its artifacts once.

A callback that carries `artifacts` is acknowledged with `artifact_count`, the
number of artifacts the task holds. An empty list writes nothing and probes
support on any callback. Older controllers ignore the field and omit
`artifact_count`; runners must check it before reporting the evidence as
recorded. Task-scoped callback credentials are revoked when the task ends, so
probe before the terminal callback rather than retrying after it.

Artifact names, kinds and digests appear on every task read, and content to
anyone with `agent.read` on the task. Runners must not attach secrets.

## Reading artifacts

`AstroliftAgentTask.artifacts` lists the metadata on every task read
(`agentTask`, `agentTasks`, `agentTasksPage` and the other task lists), in the
order the run reported them. Task reads never load the content.

```graphql
query Task($task: ID!) {
  agentTask(id: $task) {
    status
    artifacts { name kind mediaType sizeBytes sha256 createdAt }
  }
}
```

`sizeBytes` and `sha256` are computed by the server over the stored UTF-8
content. One artifact's content is read by name:

```graphql
query Artifact($org: ID!, $task: ID!, $name: String!) {
  agentTaskArtifact(orgId: $org, taskId: $task, name: $name) {
    name kind mediaType sizeBytes sha256 content
  }
}
```

`agentTaskArtifact` requires `agent.read` on the task, with the same token
ceiling, owner-operation policy and organization visibility as
`agentTaskEvents`. A caller whose `agent.read` covers only some teams, projects
or apps is refused any task outside them with a permission error, as `agentTask`
is. A caller with `agent.read` across the organization gets null for a task the
organization does not have or a malformed task ID. Every caller gets null for an
unknown name. An `orgId` other than the caller's active organization is refused.

The completion webhook (`agent_task.finished`) does not carry artifacts; its
receiver reads them through the API. MCP `astrolift_get_task` does not list
them. Collecting files in the runner (astrolift-agents) and showing them in
`astro agent inspect` (astrolift-cli) are separate changes.

## Rollout

Migration `astrolift_agents.0052_agent_task_artifacts_2488` creates the artifact
table; apply it before deploying the web image. Older runners never send the
field and are unaffected. Artifacts are kept with their task; there is no
separate delete or retention path. Rolling back the code leaves the table and
its rows in place.
