import type { SharedModelPromptPanelProps } from "./SharedModelPromptPanel";
export const sharedModelPromptProps: SharedModelPromptPanelProps = {
  identity: {
    organizationId: "org",
    id: "shared-model",
    version: 5,
    clusterId: "cluster-one",
    providerId: "provider-one",
  },
  readiness: {
    data: {
      state: "READY",
      eligible: true,
      maxPromptChars: 4000,
      maxOutputTokens: 128,
      promptsPerMinute: 6,
      maxWaitSeconds: 60,
    },
    loading: false,
    error: null,
  },
  onCheck: () => {},
  onRun: async () => ({
    ok: true,
    reply: "A bounded model reply.",
    latencyMs: 450,
    totalTokens: 40,
  }),
};
