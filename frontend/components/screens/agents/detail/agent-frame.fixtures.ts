import type { AgentFrameProps } from "./AgentFrame";
import { AGENT, LONG, LONG_AGENT } from "./agent-detail-shell.fixtures";

/** Hand-typed fixtures for the agent frame: the header data and its callbacks. */

export const FRAME: Omit<AgentFrameProps, "children"> = {
  slug: AGENT.slug,
  pathname: `/agents/${AGENT.slug}`,
  agent: AGENT,
  loading: false,
  error: null,
  onRetry: () => {},
  model: "managed model",
  clusterSlug: "conflict-astrolift",
  failedRun: null,
  canRun: true,
  dispatching: false,
  onRun: async () => true,
  onCopyId: () => {},
};

export const FAILING_FRAME: Omit<AgentFrameProps, "children"> = {
  ...FRAME,
  agent: { ...AGENT, lastRunStatus: "failed" },
  failedRun: {
    id: "7e11c4a2-9b3f-4d6e-8a1c-2f5b7d9e0a13",
    reason:
      "Spawn failed: ImagePullBackOff for 123456789012.dkr.ecr.us-west-2.amazonaws.com/research-scout@sha256:9f3c2a1b7e4d5c6b7a8f9e0d1c2b3a4f5e6d7c8b9a0f1e2d3c4b5a6f7e8d9c0b; arn:aws:iam::123456789012:role/astrolift-agent-runtime-conflict-astrolift-production-us-west-2-with-a-deliberately-long-suffix-for-overflow",
  },
};

export const LONG_FRAME: Omit<AgentFrameProps, "children"> = {
  ...FRAME,
  slug: LONG,
  pathname: `/agents/${LONG}/configuration`,
  agent: {
    ...LONG_AGENT,
    sourceUrl: `https://github.com/acme/${LONG}/tree/main/and/an/unbroken/path/that/keeps/going/without/spaces`,
  },
  clusterSlug: `cluster-${LONG}`,
};
