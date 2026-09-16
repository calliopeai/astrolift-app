# Replayable agent task events

The task callback accepts an optional `events` array. This is an additive
transport for public response text, terminal output and input attention;
legacy `partial` heartbeats remain unchanged. It does not automatically
interpret a CLI's stdout as assistant text.

Send events to the callback URL issued for the task, using its task-scoped
callback credential (or its assigned dispatcher credential):

```json
{
  "status": "running",
  "events": [
    {"sequence": 1, "turn_id": "turn-1", "message_id": "answer-1", "kind": "assistant_delta", "text": "Hello"},
    {"sequence": 2, "turn_id": "turn-1", "message_id": "answer-1", "kind": "assistant_delta", "text": " world"}
  ]
}
```

The acknowledgement includes `event_sequence`, the highest committed sequence
for that task. An empty array probes support without writing task history;
older controllers omit the acknowledgement field. Producers must check this
field before treating output as recorded.

Sequences start at one and remain contiguous across turns. A batch can overlap
already-recorded history: identical entries are acknowledged without rewriting,
then its unseen suffix is appended. Reusing a sequence with different content
or skipping a sequence returns 409 without committing any part of the callback.
The producer retains an unacknowledged batch and retries it verbatim after a
connection loss. A terminal result may carry the final batch; events and result
commit together. Task-scoped credentials are revoked at termination, so producers
should flush and acknowledge outstanding output before their terminal callback
when they need to retry a lost acknowledgement.

| Kind | Meaning |
| --- | --- |
| `assistant_delta` | Append public assistant text to the named message. |
| `terminal_delta` | Append terminal output; never infer an assistant response from it. |
| `message_end` | The named output message is complete. |
| `input_required` | The named input request is waiting for a user. |
| `approval_required` | The named approval request is waiting for a user. |
| `input_resolved` | Clear the named request only. |

Turn and message identifiers contain 1–64 ASCII letters, digits, underscores,
dots, colons or hyphens and start with a letter or digit. All five fields are
required. `text` is UTF-8, at most 16 KiB per event. A callback contains at most
64 events and remains subject to the existing 2 MiB callback-body limit. Each
task accepts at most 100,000 events and 16 MiB of event text. Capacity overflow
is explicit (413 for text capacity); it never silently drops history. Empty
text is valid for end/resolution events. Producers must not emit private
reasoning or secrets as public assistant content.

Read the feed under the selected organization's normal API authentication:

```graphql
query TaskEvents($org: ID!, $task: ID!, $after: Int!) {
  agentTaskEvents(orgId: $org, taskId: $task, after: $after, limit: 100) {
    sequence turnId messageId kind text createdAt
  }
}
```

Reads require `agent.read` on the task and the caller's active organization.
They return events strictly after the cursor, in sequence order, capped at
100 per request. Advance the cursor only after storing and applying the whole
received page. Reconnect with that saved cursor. `AgentTask.eventSequence`
exposes the recorded high-water mark; finished task events remain readable.
An invalid cursor or a hole in retained history returns an explicit error.
A task outside the active organization cannot expose its events.

This change supplies controller ingestion and replay. Runner emission,
CLI/API client integration, native rendering and deployed end-to-end
acceptance must be connected before claiming live conversation support.
