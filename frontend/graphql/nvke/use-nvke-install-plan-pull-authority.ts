"use client";
import type { NvkeOperatorInstallPlanPullAuthorityPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullAuthorityPanel";
import {
  installPlanPullAuthorityFormSchema,
  installPlanPullAuthorityRequest,
  validateInstallPlanPullAuthority,
} from "@/lib/nvke/operator-install-plan-pull-authority";
import type {
  PrepareNvkeInstallPlanPullAuthorityMutation,
  PrepareNvkeInstallPlanPullAuthorityMutationVariables,
  ReviewNvkeInstallPlanPullAuthorityMutation,
  ReviewNvkeInstallPlanPullAuthorityMutationVariables,
  RetryNvkeInstallPlanPullAuthorityMutation,
} from "@/graphql/__generated__/operations";
import {
  useNvkeInstallPlanArtifact,
  type InstallPlanArtifactArgs,
} from "./use-nvke-install-plan-artifact";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import {
  PREPARE_NVKE_INSTALL_PLAN_PULL_AUTHORITY,
  REVIEW_NVKE_INSTALL_PLAN_PULL_AUTHORITY,
  RETRY_NVKE_INSTALL_PLAN_PULL_AUTHORITY,
} from "./nvke-install-plan-pull-authority.mutations";

export const useNvkeInstallPlanPullAuthority = (
  args: InstallPlanArtifactArgs
): NvkeOperatorInstallPlanPullAuthorityPanelProps | null =>
  useNvkeInstallPlanArtifact(args, {
    mode: "pull-authority",
    parse: (value) => installPlanPullAuthorityFormSchema.parse(value),
    request: installPlanPullAuthorityRequest,
    validate: (value, parents, artifactId, limits) =>
      validateInstallPlanPullAuthority(value, ...parents, artifactId, limits),
    mutate: async (client, action, input, artifactId, body, organizationId) => {
      const options = {
        fetchPolicy: "no-cache" as const,
        context: { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: organizationId },
      };
      if (action === "prepare") {
        const response = await client.mutate<
          PrepareNvkeInstallPlanPullAuthorityMutation,
          PrepareNvkeInstallPlanPullAuthorityMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_PULL_AUTHORITY,
          variables: { input: { ...input, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        return response.data?.prepareNvkeOperatorInstallPlanPullAuthority;
      }
      if (action === "review") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanPullAuthorityMutation,
          ReviewNvkeInstallPlanPullAuthorityMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_PULL_AUTHORITY,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        return response.data?.reviewNvkeOperatorInstallPlanPullAuthority;
      }
      const response = await client.mutate<
        RetryNvkeInstallPlanPullAuthorityMutation,
        ReviewNvkeInstallPlanPullAuthorityMutationVariables
      >({
        mutation: RETRY_NVKE_INSTALL_PLAN_PULL_AUTHORITY,
        variables: { input: { ...input, artifactId } },
        ...options,
      });
      if (response.error) throw new Error("UNCONFIRMED");
      return response.data?.recoverNvkeOperatorInstallPlanPullAuthorityPreparation;
    },
  });
