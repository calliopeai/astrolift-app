import type { PlaygroundBatchProps } from "./PlaygroundBatch";
import type { PlaygroundHistorySession } from "./PlaygroundHistoryScreen";
import type { PlaygroundObservabilityScreenProps } from "./PlaygroundObservabilityScreen";
import type { PlaygroundScreenProps } from "./PlaygroundScreen";
import type { PlaygroundStarredPrompt } from "./PlaygroundStarredScreen";

/** Hand-typed fixtures for the playground screens. */

const noop = () => {};
const asyncNoop = async () => {};

export const LONG =
  "Summarise the attached incident review for the platform-team-shared-production-workloads-us-west-2 cluster, including every contributing factor, the timeline of operator actions, and a deliberately long list of follow-ups that keeps going";

export const PLAYGROUND: Omit<PlaygroundScreenProps, "batch"> = {
  models: ["Genesis", "Explorer", "Quantum"],
  tab: "chat",
  setTab: noop,
  title: "Zero-shot notes",
  setTitle: noop,
  messages: [
    { role: "user", content: "Explain the concept of zero-shot prompting." },
    {
      role: "assistant",
      content:
        "Zero-shot prompting means asking a model to perform a task without giving it any examples. The model relies solely on its pre-trained knowledge to generate a response.",
    },
    { role: "user", content: "Give me one example." },
    {
      role: "assistant",
      content: '[Genesis] This is a simulated response to: "Give me one example."',
    },
  ],
  prompt: "",
  setPrompt: noop,
  model: "Genesis",
  setModel: noop,
  loading: false,
  savedSessions: [
    {
      id: "s-1",
      title: "Zero-shot notes",
      model: "Genesis",
      updatedAt: "2026-09-27T14:05:00Z",
      starred: true,
      messageCount: 4,
    },
    {
      id: "s-2",
      title: "SQL for top customers",
      model: "Explorer",
      updatedAt: "2026-09-26T09:12:00Z",
      starred: false,
      messageCount: 2,
    },
    {
      id: "s-3",
      title: "Microservices pros and cons",
      model: "Quantum",
      updatedAt: "2026-09-25T17:40:00Z",
      starred: false,
      messageCount: 6,
    },
  ],
  activeSavedId: "s-1",
  onSend: noop,
  onSave: noop,
  onShare: asyncNoop,
  onLoad: noop,
  onNew: noop,
  onDelete: noop,
  onStar: noop,
};

export const BATCH: PlaygroundBatchProps = {
  input: '{"prompt": "What is 2+2?"}\n{"prompt": "Name three primary colours"}',
  setInput: noop,
  inputs: ['{"prompt": "What is 2+2?"}', '{"prompt": "Name three primary colours"}'],
  running: false,
  results: [
    {
      input: '{"prompt": "What is 2+2?"}',
      output: "[Genesis] simulated response for input length 25",
      ok: true,
    },
    {
      input: '{"prompt": "Name three primary colours"}',
      output: "[Genesis] simulated response for input length 39",
      ok: true,
    },
  ],
  onRun: asyncNoop,
  onExportCsv: noop,
  onExportJsonl: noop,
  onCopyJson: asyncNoop,
};

export const HISTORY: PlaygroundHistorySession[] = [
  {
    date: "2026-02-23",
    prompt: "Explain zero-shot prompting in simple terms",
    model: "Genesis",
    tokens: 312,
    status: "completed",
  },
  {
    date: "2026-02-22",
    prompt: "Generate SQL to find top 10 customers by revenue",
    model: "Explorer",
    tokens: 287,
    status: "completed",
  },
  {
    date: "2026-02-18",
    prompt: "Review this code for security vulnerabilities",
    model: "Quantum",
    tokens: 932,
    status: "error",
  },
];

export const STARRED: PlaygroundStarredPrompt[] = [
  {
    title: "Unit test generator",
    prompt:
      "Write a comprehensive unit test suite for the following TypeScript function. Cover edge cases, null inputs, and boundary conditions.",
    model: "Explorer",
    date: "2026-02-20",
  },
  {
    title: "SQL optimiser",
    prompt: "Analyse this SQL query and suggest optimisations for performance.",
    model: "Quantum",
    date: "2026-02-18",
  },
  {
    title: "Email drafter",
    prompt: "Draft a professional email to announce a new product feature.",
    model: "Genesis",
    date: "2026-02-15",
  },
];

export const OBSERVABILITY: PlaygroundObservabilityScreenProps = {
  run: {
    workflowId: "deploy-app:acme/eng/api:abc12",
    runId: "01934f7c-deef-7e29-aafd-dac74e7ad913",
    workflowKind: "DeployAppWorkflow",
    status: "failed",
    startedAt: "2026-05-09T18:42:11.123Z",
    endedAt: "2026-05-09T18:43:02.987Z",
    errorMessage:
      "Activity ApplyManifest failed after 2 attempts (last error: 503 from cluster api-server)",
  },
  activities: [
    {
      id: "act-1",
      name: "FetchManifest",
      status: "succeeded",
      durationMs: 412,
      attempts: [
        {
          attemptNumber: 1,
          startedAt: "2026-05-09T18:42:11.200Z",
          endedAt: "2026-05-09T18:42:11.612Z",
          status: "succeeded",
        },
      ],
    },
    {
      id: "act-2",
      name: "ApplyManifest",
      status: "failed",
      durationMs: 34000,
      attempts: [
        {
          attemptNumber: 1,
          startedAt: "2026-05-09T18:42:13.500Z",
          endedAt: "2026-05-09T18:42:30.500Z",
          status: "failed",
          errorMessage: "503 from cluster api-server",
        },
        {
          attemptNumber: 2,
          startedAt: "2026-05-09T18:42:35.000Z",
          endedAt: "2026-05-09T18:42:52.000Z",
          status: "failed",
          errorMessage: "503 from cluster api-server",
        },
      ],
    },
    { id: "act-3", name: "WaitForRollout", status: "pending", attempts: [] },
  ],
  commits: [
    {
      hash: "9f3a82e51c4d6b8e7f1c0d3a9b2e4f5c8d7a1b6e",
      author: "astrolift-bot",
      authorEmail: "bot@astrolift.dev",
      message: "deploy(acme/eng/api): roll v2026.05.09-1842 to staging",
      occurredAt: "2026-05-09T18:42:10.000Z",
      diffUrl: "https://github.example/acme/astrolift-config/commit/9f3a82e",
      workflowRunId: "01934f7c-deef-7e29-aafd-dac74e7ad913",
    },
    {
      hash: "5c1d80b3a496e7d5c4b3a2e1d0c9b8a7f6e5d4c3",
      author: "ops@example.com",
      message: "config(acme/eng/api): bump cpu_request to 250m",
      occurredAt: "2026-05-08T14:22:18.000Z",
    },
  ],
  onCancel: noop,
  onRetryActivity: noop,
};
