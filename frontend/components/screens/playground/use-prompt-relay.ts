"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { CombinedGraphQLErrors } from "@apollo/client";
import type {
  ModelPromptReadinessQuery,
  ModelPromptReadinessQueryVariables,
  PlaygroundPromptMutation,
  PlaygroundPromptMutationVariables,
} from "@/graphql/__generated__/operations";
import { MODEL_PROMPT_READINESS } from "@/graphql/playground/playground.queries";
import { PLAYGROUND_PROMPT } from "@/graphql/playground/playground.mutations";
import type { PromptError, PromptOutcome, ReadinessState } from "./playground.types";

type Snapshot = {
  key: string;
  state: ReadinessState;
  data: ModelPromptReadinessQuery["astroliftModelPromptReadiness"];
};
const failure = (error: PromptError): PromptOutcome => ({ ok: false, reply: "", error });
const permissionDenied = (error: unknown) =>
  CombinedGraphQLErrors.is(error) &&
  error.errors.some((e) => e.extensions?.code === "PERMISSION_DENIED");
const codeError = (code: string): PromptError =>
  (
    ({
      PERMISSION_DENIED: "refused",
      NOT_FOUND: "unavailable",
      PRECONDITION: "unavailable",
      VALIDATION: "invalid",
      RATE_LIMITED: "rateLimited",
      CONFLICT: "conflict",
    }) as Record<string, PromptError>
  )[code] ?? "failed";

/** One shared admission/rate budget for chat and batch, bound to the current identity/endpoint. */
export function usePromptRelay(scope: string, userId: string, endpointId: string) {
  const client = useApolloClient();
  const key = scope && endpointId ? `${scope}:${endpointId}` : "";
  const [epoch, setEpoch] = useState({ key, version: 0 });
  if (epoch.key !== key) setEpoch({ key, version: epoch.version + 1 });
  const context = useRef({ key, version: 0, mounted: true });
  const [attempt, setAttempt] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: "", state: "unselected", data: null });
  const [pending, setPending] = useState("");
  const busy = useRef(false);
  const budget = useRef<{ user: string; at: number[] }>({ user: userId, at: [] });
  const active =
    snapshot.key === key
      ? snapshot
      : { key, state: key ? ("loading" as const) : ("unselected" as const), data: null };
  const latest = useRef({ key, version: 0, data: active.data });
  useLayoutEffect(() => {
    context.current = { ...context.current, key, version: epoch.version };
    latest.current = { key, version: epoch.version, data: active.data };
  }, [key, epoch.version, active.data]);
  useEffect(() => {
    context.current.mounted = true;
    return () => {
      context.current.mounted = false;
      context.current.version += 1;
    };
  }, []);
  useEffect(() => {
    if (!key) return;
    const version = context.current.version;
    let cancelled = false;
    void client
      .query<ModelPromptReadinessQuery, ModelPromptReadinessQueryVariables>({
        query: MODEL_PROMPT_READINESS,
        variables: { id: endpointId },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      })
      .then((result) => {
        if (
          cancelled ||
          !context.current.mounted ||
          context.current.version !== version ||
          context.current.key !== key
        )
          return;
        const data = result.data?.astroliftModelPromptReadiness;
        const validLimits =
          data &&
          [
            data.maxPromptChars,
            data.maxOutputTokens,
            data.promptsPerMinute,
            data.maxWaitSeconds,
          ].every(Number.isInteger) &&
          data.maxPromptChars > 0 &&
          data.maxPromptChars <= 4000 &&
          data.maxOutputTokens > 0 &&
          data.maxOutputTokens <= 128 &&
          data.promptsPerMinute > 0 &&
          data.promptsPerMinute <= 6 &&
          data.maxWaitSeconds >= 20 &&
          data.maxWaitSeconds <= 60;
        if (data && !validLimits) {
          setSnapshot({ key, state: "transport", data: null });
          return;
        }
        setSnapshot({ key, state: data?.state ?? "missing", data: data ?? null });
      })
      .catch((error) => {
        if (
          cancelled ||
          !context.current.mounted ||
          context.current.version !== version ||
          context.current.key !== key
        )
          return;
        setSnapshot({ key, state: permissionDenied(error) ? "refused" : "transport", data: null });
      });
    return () => {
      cancelled = true;
    };
  }, [client, key, endpointId, attempt]);
  const invoke = async (prompt: string): Promise<PromptOutcome> => {
    const current = latest.current;
    const authority = context.current;
    if (
      authority.version !== epoch.version ||
      !authority.mounted ||
      !key ||
      authority.key !== key ||
      current.key !== key ||
      current.version !== authority.version ||
      !current.data?.eligible ||
      current.data.state !== "READY"
    )
      return failure("unavailable");
    const text = prompt.trim();
    if (!text || text.length > Math.min(4000, current.data.maxPromptChars))
      return failure("invalid");
    if (busy.current) return failure("conflict");
    const now = Date.now();
    if (budget.current.user !== userId) budget.current = { user: userId, at: [] };
    budget.current.at = budget.current.at.filter((at) => now - at < 60000);
    if (budget.current.at.length >= Math.min(6, current.data.promptsPerMinute))
      return failure("rateLimited");
    budget.current.at.push(now);
    busy.current = true;
    setPending(key);
    const version = authority.version;
    try {
      const result = await client.mutate<
        PlaygroundPromptMutation,
        PlaygroundPromptMutationVariables
      >({
        mutation: PLAYGROUND_PROMPT,
        variables: { input: { managedServiceId: endpointId, prompt: text } },
      });
      if (
        !context.current.mounted ||
        context.current.key !== key ||
        context.current.version !== version
      )
        return failure("unavailable");
      const envelope = result.data?.testModelEndpoint;
      if (!envelope?.ok)
        return failure(envelope?.errors[0]?.code ? codeError(envelope.errors[0].code) : "failed");
      const data = envelope.data;
      if (!data) return failure("failed");
      if (data.status === "timed_out") return failure("timedOut");
      if (
        data.status !== "succeeded" ||
        typeof data.reply !== "string" ||
        !data.reply.trim() ||
        data.reply.length > 8000
      )
        return failure("failed");
      const observation = (value: number | null | undefined) =>
        typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e9
          ? value
          : null;
      return {
        ok: true,
        reply: data.reply,
        latencyMs: observation(data.latencyMs),
        totalTokens: observation(data.totalTokens),
      };
    } catch (error) {
      return failure(permissionDenied(error) ? "refused" : "transport");
    } finally {
      busy.current = false;
      if (context.current.mounted) setPending("");
    }
  };
  return {
    readiness: active.state,
    maxPromptChars: Math.min(4000, active.data?.maxPromptChars ?? 4000),
    maxOutputTokens: active.data?.maxOutputTokens ?? 128,
    maxWaitSeconds: active.data?.maxWaitSeconds ?? 60,
    canSend: !!key && active.state === "READY" && !!active.data?.eligible && !pending,
    loading: !!key && pending === key,
    invoke,
    retry: () => {
      latest.current.data = null;
      setSnapshot({ key, state: "loading", data: null });
      setAttempt((a) => a + 1);
    },
    contextKey: key,
  };
}
