"use client";
import { useLayoutEffect, useRef } from "react";
import { useQuery, useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  GET_SHARED_MODEL_PROMPT_READINESS,
  TEST_SHARED_MODEL_ENDPOINT,
} from "@/graphql/models/shared-prompt.queries";
import type {
  ClusterModelFieldsFragment,
  GetSharedModelPromptReadinessQuery,
  GetSharedModelPromptReadinessQueryVariables,
  TestSharedModelEndpointMutation,
  TestSharedModelEndpointMutationVariables,
} from "@/graphql/__generated__/operations";
import type { SharedModelPromptPanelProps } from "./SharedModelPromptPanel";
import { validSharedPromptLimits, type SharedPromptResult } from "./shared-model-prompt";
export function useSharedModelPrompt(
  model: ClusterModelFieldsFragment
): SharedModelPromptPanelProps {
  const t = useTranslations("models.shared.prompt");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const skipped = orgLoading || Boolean(orgError) || org?.id !== model.organizationId;
  const variables = {
    id: model.id,
    expectedClusterId: model.clusterId,
    expectedProviderId: model.providerId,
    expectedVersion: model.version,
  };
  const query = useQuery<
    GetSharedModelPromptReadinessQuery,
    GetSharedModelPromptReadinessQueryVariables
  >(GET_SHARED_MODEL_PROMPT_READINESS, {
    variables,
    skip: skipped,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const [run] = useMutation<
    TestSharedModelEndpointMutation,
    TestSharedModelEndpointMutationVariables
  >(TEST_SHARED_MODEL_ENDPOINT, { fetchPolicy: "no-cache" });
  const data = skipped ? null : (query.data?.astroliftSharedModelPromptReadiness ?? null);
  const invalid = data && !validSharedPromptLimits(data);
  const readiness = {
    data: invalid ? null : data,
    loading: orgLoading || (!skipped && query.loading),
    error:
      orgError?.message ??
      (!orgLoading && org?.id !== model.organizationId
        ? t("changed")
        : invalid
          ? t("failed")
          : skipped
            ? null
            : (query.error?.message ?? null)),
  };
  const key = JSON.stringify([model.organizationId, variables, readiness]);
  const lifecycle = useRef(0);
  const latest = useRef({ key, readiness, skipped }),
    mounted = useRef(true),
    busy = useRef(false);
  useLayoutEffect(() => {
    latest.current = { key, readiness, skipped };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current += 1;
    };
  }, []);
  const failure = (message: string, code: string | null = null): SharedPromptResult => ({
    ok: false,
    message,
    code,
  });
  return {
    identity: {
      organizationId: model.organizationId,
      id: model.id,
      version: model.version,
      clusterId: model.clusterId,
      providerId: model.providerId,
    },
    readiness,
    onCheck: () => {
      if (!skipped) void query.refetch().catch(() => {});
    },
    onRun: async (prompt) => {
      const current = latest.current,
        text = prompt.trim();
      if (
        !mounted.current ||
        current.key !== key ||
        current.skipped ||
        current.readiness.loading ||
        current.readiness.error ||
        !current.readiness.data?.eligible ||
        current.readiness.data.state !== "READY" ||
        !validSharedPromptLimits(current.readiness.data)
      )
        return failure(t("changed"));
      if (!text || text.length > current.readiness.data.maxPromptChars)
        return failure(t("invalid"), "VALIDATION");
      if (busy.current) return failure(t("failed"), "CONFLICT");
      const lifecycleRevision = lifecycle.current;
      busy.current = true;
      try {
        const result = await run({
          variables: {
            input: {
              managedServiceId: model.id,
              expectedClusterId: model.clusterId,
              expectedProviderId: model.providerId,
              expectedVersion: model.version,
              prompt: text,
            },
          },
        });
        if (
          !mounted.current ||
          lifecycle.current !== lifecycleRevision ||
          latest.current.key !== key
        )
          return failure(t("changed"));
        const envelope = result.data?.testSharedModelEndpoint;
        if (envelope?.ok !== true || !Array.isArray(envelope.errors) || envelope.errors.length) {
          const error = envelope?.errors?.[0];
          return {
            ok: false,
            message: error?.message ?? t("failed"),
            code: error?.code ?? null,
            currentVersion: error?.currentVersion ?? null,
            requestedVersion: error?.requestedVersion ?? null,
          };
        }
        const reply = envelope.data;
        if (reply?.status === "timed_out") return failure(t("timedOut"), "TIMED_OUT");
        if (
          reply?.status !== "succeeded" ||
          typeof reply.reply !== "string" ||
          !reply.reply.trim() ||
          reply.reply.length > 8000
        )
          return failure(reply?.error?.trim() ? reply.error : t("unsupportedReply"));
        const observation = (value: number | null | undefined) =>
          typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e9
            ? value
            : null;
        return {
          ok: true,
          reply: reply.reply,
          latencyMs: observation(reply.latencyMs),
          totalTokens: observation(reply.totalTokens),
        };
      } catch (error) {
        return failure(error instanceof Error ? error.message : t("failed"));
      } finally {
        busy.current = false;
      }
    },
  };
}
