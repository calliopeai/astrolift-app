"use client";

import { notFound } from "next/navigation";
import type {
  GitOpsCommit,
  WorkflowActivity,
  WorkflowRunSummary,
} from "@/components/observability";
import { PlaygroundObservabilityScreen } from "@/components/screens/playground/PlaygroundObservabilityScreen";

import { isRouteEnabled } from "@/lib/route-flags";

const DEMO_RUN: WorkflowRunSummary = {
  workflowId: "deploy-app:acme/eng/api:abc12",
  runId: "01934f7c-deef-7e29-aafd-dac74e7ad913",
  workflowKind: "DeployAppWorkflow",
  status: "failed",
  startedAt: "2026-05-09T18:42:11.123Z",
  endedAt: "2026-05-09T18:43:02.987Z",
  errorMessage:
    "Activity ApplyManifest failed after 3 attempts (last error: 503 from cluster api-server)",
  errorStack: `Workflow execution failed
  at ApplyManifest:attempt-3
  at DeployAppWorkflow:execute (workflows/deploy_app.py:142)`,
  temporalUiUrl:
    "http://localhost:8233/namespaces/default/workflows/deploy-app:acme%2Feng%2Fapi:abc12/01934f7c-deef-7e29-aafd-dac74e7ad913",
};

const DEMO_ACTIVITIES: WorkflowActivity[] = [
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
    name: "RenderManifest",
    status: "succeeded",
    durationMs: 1230,
    attempts: [
      {
        attemptNumber: 1,
        startedAt: "2026-05-09T18:42:11.700Z",
        endedAt: "2026-05-09T18:42:12.930Z",
        status: "succeeded",
      },
    ],
  },
  {
    id: "act-3",
    name: "ResolveSecrets",
    status: "succeeded",
    durationMs: 320,
    attempts: [
      {
        attemptNumber: 1,
        startedAt: "2026-05-09T18:42:13.000Z",
        endedAt: "2026-05-09T18:42:13.320Z",
        status: "succeeded",
      },
    ],
  },
  {
    id: "act-4",
    name: "ApplyManifest",
    status: "failed",
    durationMs: 49500,
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
      {
        attemptNumber: 3,
        startedAt: "2026-05-09T18:42:57.000Z",
        endedAt: "2026-05-09T18:43:02.987Z",
        status: "failed",
        errorMessage: "503 from cluster api-server",
        errorStack: `KubeApiError: 503 Service Unavailable
  at ApplyManifest.run (activities/k8s.py:88)
  at boilerworks.temporal.run (boilerworks/temporal/runtime.py:212)`,
      },
    ],
  },
  {
    id: "act-5",
    name: "WaitForRollout",
    status: "pending",
    attempts: [],
  },
];

const DEMO_COMMITS: GitOpsCommit[] = [
  {
    hash: "9f3a82e51c4d6b8e7f1c0d3a9b2e4f5c8d7a1b6e",
    author: "astrolift-bot",
    authorEmail: "bot@astrolift.dev",
    message: "deploy(acme/eng/api): roll v2026.05.09-1842 to staging",
    occurredAt: "2026-05-09T18:42:10.000Z",
    diffUrl: "https://github.example/acme/astrolift-config/commit/9f3a82e",
    sourceCommitUrl: "https://github.example/acme/api/commit/abc1234",
    workflowRunId: "01934f7c-deef-7e29-aafd-dac74e7ad913",
  },
  {
    hash: "7d2e91c4b5a3f8e6d2c1b9a8f7e6d5c4b3a2e1d0",
    author: "astrolift-bot",
    authorEmail: "bot@astrolift.dev",
    message: "deploy(acme/eng/api): roll v2026.05.09-1610 to staging",
    occurredAt: "2026-05-09T16:10:42.000Z",
    diffUrl: "https://github.example/acme/astrolift-config/commit/7d2e91c",
    sourceCommitUrl: "https://github.example/acme/api/commit/def5678",
    workflowRunId: "01934e9a-12cd-7c01-991f-aabbccddeeff",
  },
  {
    hash: "5c1d80b3a496e7d5c4b3a2e1d0c9b8a7f6e5d4c3",
    author: "ops@example.com",
    message: "config(acme/eng/api): bump cpu_request to 250m",
    occurredAt: "2026-05-08T14:22:18.000Z",
    diffUrl: "https://github.example/acme/astrolift-config/commit/5c1d80b",
  },
];

export default function ObservabilityPlaygroundPage() {
  if (!isRouteEnabled("/playground")) notFound();

  return (
    <PlaygroundObservabilityScreen
      run={DEMO_RUN}
      activities={DEMO_ACTIVITIES}
      commits={DEMO_COMMITS}
      onCancel={() => alert("Cancel run")}
      onRetryActivity={(id) => alert(`Retry activity ${id}`)}
    />
  );
}
