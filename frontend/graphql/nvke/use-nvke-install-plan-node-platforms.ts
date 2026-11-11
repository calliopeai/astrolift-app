"use client";
import type { NvkeOperatorInstallPlanNodePlatformsPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanNodePlatformsPanel";
import {
  installPlanNodePlatformsFormSchema,
  installPlanNodePlatformsRequest,
  validateInstallPlanNodePlatforms,
} from "@/lib/nvke/operator-install-plan-node-platforms";
import type {
  PrepareNvkeInstallPlanNodePlatformsMutation,
  PrepareNvkeInstallPlanNodePlatformsMutationVariables,
  ReviewNvkeInstallPlanNodePlatformsMutation,
  ReviewNvkeInstallPlanNodePlatformsMutationVariables,
  RetryNvkeInstallPlanNodePlatformsMutation,
} from "@/graphql/__generated__/operations";
import {
  useNvkeInstallPlanArtifact,
  type InstallPlanArtifactArgs,
} from "./use-nvke-install-plan-artifact";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import {
  PREPARE_NVKE_INSTALL_PLAN_NODE_PLATFORMS,
  REVIEW_NVKE_INSTALL_PLAN_NODE_PLATFORMS,
  RETRY_NVKE_INSTALL_PLAN_NODE_PLATFORMS,
} from "./nvke-install-plan-node-platforms.mutations";

export const useNvkeInstallPlanNodePlatforms = (
  args: InstallPlanArtifactArgs
): NvkeOperatorInstallPlanNodePlatformsPanelProps | null =>
  useNvkeInstallPlanArtifact(args, {
    mode: "platform",
    parse: (value) => installPlanNodePlatformsFormSchema.parse(value),
    request: installPlanNodePlatformsRequest,
    validate: (value, parents, artifactId, limits) =>
      validateInstallPlanNodePlatforms(value, ...parents, artifactId, limits),
    mutate: async (client, action, input, artifactId, body, organizationId) => {
      const options = {
        fetchPolicy: "no-cache" as const,
        context: { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: organizationId },
      };
      if (action === "prepare") {
        const response = await client.mutate<
          PrepareNvkeInstallPlanNodePlatformsMutation,
          PrepareNvkeInstallPlanNodePlatformsMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_NODE_PLATFORMS,
          variables: { input: { ...input, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        return response.data?.prepareNvkeOperatorInstallPlanNodePlatforms;
      }
      if (action === "review") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanNodePlatformsMutation,
          ReviewNvkeInstallPlanNodePlatformsMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_NODE_PLATFORMS,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        return response.data?.reviewNvkeOperatorInstallPlanNodePlatforms;
      }
      const response = await client.mutate<
        RetryNvkeInstallPlanNodePlatformsMutation,
        ReviewNvkeInstallPlanNodePlatformsMutationVariables
      >({
        mutation: RETRY_NVKE_INSTALL_PLAN_NODE_PLATFORMS,
        variables: { input: { ...input, artifactId } },
        ...options,
      });
      if (response.error) throw new Error("UNCONFIRMED");
      return response.data?.recoverNvkeOperatorInstallPlanNodePlatformsPreparation;
    },
  });
