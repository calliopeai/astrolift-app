"use client";
import type { NvkeOperatorInstallPlanImmutablePanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanImmutablePanel";
import {
  installPlanImmutableRequest,
  installPlanImmutableFormSchema,
  validateInstallPlanImmutableArtifact,
  type NvkeImmutableMode,
} from "@/lib/nvke/operator-install-plan-immutable";
import {
  useNvkeInstallPlanArtifact,
  type InstallPlanArtifactArgs,
  type InstallPlanArtifactEnvelope,
} from "./use-nvke-install-plan-artifact";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import type {
  PrepareNvkeInstallPlanImagePayloadMutation,
  PrepareNvkeInstallPlanImagePayloadMutationVariables,
  ReviewNvkeInstallPlanImagePayloadMutation,
  ReviewNvkeInstallPlanImagePayloadMutationVariables,
  RetryNvkeInstallPlanImagePayloadMutation,
  PrepareNvkeInstallPlanImmutableExecutionTreeMutation,
  PrepareNvkeInstallPlanImmutableExecutionTreeMutationVariables,
  ReviewNvkeInstallPlanImmutableExecutionTreeMutation,
  ReviewNvkeInstallPlanImmutableExecutionTreeMutationVariables,
  RetryNvkeInstallPlanImmutableExecutionTreeMutation,
} from "@/graphql/__generated__/operations";
import {
  PREPARE_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD,
  REVIEW_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD,
  RETRY_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD,
  PREPARE_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION,
  REVIEW_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION,
  RETRY_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION,
} from "./nvke-install-plan-immutable.mutations";

export const useNvkeInstallPlanImmutable = (
  args: InstallPlanArtifactArgs & { mode: NvkeImmutableMode }
): NvkeOperatorInstallPlanImmutablePanelProps | null => {
  const mode = args.mode;
  const result = useNvkeInstallPlanArtifact(args, {
    mode,
    parse: (values) => installPlanImmutableFormSchema(mode).parse(values),
    request: (values, ...parents) => installPlanImmutableRequest(values, mode, ...parents),
    validate: (value, parents, artifactId, limits) =>
      validateInstallPlanImmutableArtifact(value, mode, ...parents, artifactId, limits),
    mutate: async (client, action, input, artifactId, body, organizationId) => {
      const options = {
        fetchPolicy: "no-cache" as const,
        context: { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: organizationId },
      };
      let envelope: InstallPlanArtifactEnvelope;
      if (action === "prepare" && mode === "payload") {
        const response = await client.mutate<
          PrepareNvkeInstallPlanImagePayloadMutation,
          PrepareNvkeInstallPlanImagePayloadMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD,
          variables: { input: { ...input, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.prepareNvkeOperatorInstallPlanImagePayload;
      } else if (action === "prepare" && mode === "execution") {
        const response = await client.mutate<
          PrepareNvkeInstallPlanImmutableExecutionTreeMutation,
          PrepareNvkeInstallPlanImmutableExecutionTreeMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION,
          variables: { input: { ...input, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.prepareNvkeOperatorInstallPlanImmutableExecutionTree;
      } else if (action === "review" && mode === "payload") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanImagePayloadMutation,
          ReviewNvkeInstallPlanImagePayloadMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.reviewNvkeOperatorInstallPlanImagePayload;
      } else if (action === "review" && mode === "execution") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanImmutableExecutionTreeMutation,
          ReviewNvkeInstallPlanImmutableExecutionTreeMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.reviewNvkeOperatorInstallPlanImmutableExecutionTree;
      } else if (action === "retry" && mode === "payload") {
        const response = await client.mutate<
          RetryNvkeInstallPlanImagePayloadMutation,
          ReviewNvkeInstallPlanImagePayloadMutationVariables
        >({
          mutation: RETRY_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.recoverNvkeOperatorInstallPlanImagePayloadPreparation;
      } else if (action === "retry" && mode === "execution") {
        const response = await client.mutate<
          RetryNvkeInstallPlanImmutableExecutionTreeMutation,
          ReviewNvkeInstallPlanImmutableExecutionTreeMutationVariables
        >({
          mutation: RETRY_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.recoverNvkeOperatorInstallPlanImmutableExecutionTreePreparation;
      }

      return envelope;
    },
  });
  return result ? { ...result, mode } : null;
};
