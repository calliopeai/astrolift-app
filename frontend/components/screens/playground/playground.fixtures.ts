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
  models: [
    {
      id: "019abcde-1111-7000-8000-000000000001",
      name: "Qwen workspace",
      variant: "vllm",
      registeredAppSlug: "support",
      environmentName: "production",
    },
  ],
  model: "019abcde-1111-7000-8000-000000000001",
  modelName: "Qwen workspace",
  setModel: noop,
  search: "",
  setSearch: noop,
  page: 1,
  totalCount: 1,
  setPage: noop,
  catalogLoading: false,
  catalogError: false,
  onCatalogRetry: noop,
  readiness: "READY",
  onReadinessRetry: noop,
  maxPromptChars: 4000,
  maxOutputTokens: 128,
  maxWaitSeconds: 40,
  canSend: true,
  error: null,
  tab: "chat",
  setTab: noop,
  title: "Arithmetic example",
  setTitle: noop,
  messages: [
    { role: "user", content: "What is 2+2?" },
    { role: "assistant", content: "4", totalTokens: 12, latencyMs: 850 },
  ],
  prompt: "",
  setPrompt: noop,
  loading: false,
  savedSessions: [
    {
      id: "019abcde-1111-7000-8000-000000000002",
      title: "Arithmetic example",
      model: "019abcde-1111-7000-8000-000000000001",
      modelName: "Qwen workspace",
      updatedAt: "2026-09-27T14:05:00Z",
      starred: true,
      messageCount: 2,
    },
  ],
  activeSavedId: "019abcde-1111-7000-8000-000000000002",
  onSend: noop,
  onSave: noop,
  onShare: asyncNoop,
  onLoad: noop,
  onNew: noop,
  onDelete: noop,
  onStar: noop,
};
export const BATCH: PlaygroundBatchProps = {
  input: "What is 2+2?\nName three primary colours",
  setInput: noop,
  inputs: ["What is 2+2?", "Name three primary colours"],
  running: false,
  cancelled: false,
  canRun: true,
  invalid: false,
  results: [
    { input: "What is 2+2?", output: "4", ok: true },
    { input: "Name three primary colours", output: "Red, green, blue", ok: true },
  ],
  onRun: asyncNoop,
  onCancel: noop,
  onExportCsv: noop,
  onExportJsonl: noop,
  onCopyJson: asyncNoop,
};

export const HISTORY: PlaygroundHistorySession[] = [
  {
    schema: 2,
    id: "019abcde-1111-7000-8000-000000000002",
    title: "Arithmetic example",
    model: "019abcde-1111-7000-8000-000000000001",
    modelName: "Qwen workspace",
    messages: [
      { role: "user", content: "What is 2+2?" },
      { role: "assistant", content: "4", totalTokens: 12, latencyMs: 850 },
    ],
    createdAt: "2026-09-27T14:05:00Z",
    updatedAt: "2026-09-27T14:05:00Z",
    starred: true,
  },
];
export const STARRED: PlaygroundStarredPrompt[] = HISTORY;

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
