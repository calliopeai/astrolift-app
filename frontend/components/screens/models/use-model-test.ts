"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";

import { TEST_MODEL_ENDPOINT } from "@/graphql/services/services.mutations";

type ReplyStatus = "succeeded" | "failed" | "timed_out";

// TEST_MODEL_ENDPOINT lives in services.mutations.ts, which interpolates a
// plain field-list constant elsewhere in the file and so is excluded from
// codegen wholesale (see codegen.ts) -- same reason useModelReplicas and
// useDeployModel hand-declare their own result shape.
interface TestModelEndpointResult {
  testModelEndpoint: {
    ok: boolean;
    errors?: { message: string }[] | null;
    data?: {
      status: ReplyStatus;
      reply: string;
      latencyMs: number | null;
      promptTokens: number | null;
      completionTokens: number | null;
      totalTokens: number | null;
      error: string;
    } | null;
  };
}

export type ModelTestOutcome =
  | { kind: "error"; message: string }
  | {
      kind: "reply";
      status: ReplyStatus;
      reply: string;
      latencyMs: number | null;
      promptTokens: number | null;
      completionTokens: number | null;
      totalTokens: number | null;
      error: string;
    };

/**
 * Sends one bounded prompt to a hosted model through the cluster's
 * keep-alive agent. The data half of ModelTestDialogView.
 */
export function useModelTest({ id, name }: { id: string; name: string }) {
  const [outcome, setOutcome] = React.useState<ModelTestOutcome | null>(null);
  const [test, { loading }] = useMutation<TestModelEndpointResult>(TEST_MODEL_ENDPOINT);

  async function send(prompt: string) {
    setOutcome(null);
    const { data } = await test({ variables: { input: { managedServiceId: id, prompt } } });
    const result = data?.testModelEndpoint;
    if (!result?.ok) {
      setOutcome({ kind: "error", message: result?.errors?.[0]?.message ?? "Test failed" });
      return;
    }
    if (result.data) {
      setOutcome({ kind: "reply", ...result.data });
    }
  }

  return { name, loading, outcome, send, reset: () => setOutcome(null) };
}
