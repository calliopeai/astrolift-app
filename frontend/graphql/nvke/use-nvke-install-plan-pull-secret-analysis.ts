"use client";
import type { NvkeOperatorInstallPlanPullAuthorityPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullAuthorityPanel";
import type { NvkeInstallPlanPullAuthority } from "@/lib/nvke/operator-install-plan-pull-authority";
import type { NvkeOperatorInstallPlanPullSecretAnalysisPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullSecretAnalysisPanel";
import {
  installPlanPullSecretAnalysisFormSchema,
  installPlanPullSecretAnalysisRequest,
  validateInstallPlanPullSecretAnalysis,
  installPlanPullSecretAnalysisSource,
} from "@/lib/nvke/operator-install-plan-pull-secret-analysis";
import type {
  PrepareNvkeInstallPlanPullSecretAnalysisMutation,
  PrepareNvkeInstallPlanPullSecretAnalysisMutationVariables,
  ReviewNvkeInstallPlanPullSecretAnalysisMutation,
  ReviewNvkeInstallPlanPullSecretAnalysisMutationVariables,
  RetryNvkeInstallPlanPullSecretAnalysisMutation,
} from "@/graphql/__generated__/operations";
import {
  useNvkeInstallPlanArtifact,
  type InstallPlanArtifactArgs,
} from "./use-nvke-install-plan-artifact";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import {
  PREPARE_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS,
  REVIEW_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS,
  RETRY_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS,
} from "./nvke-install-plan-pull-secret-analysis.mutations";

export const useNvkeInstallPlanPullSecretAnalysis = (
  args: InstallPlanArtifactArgs & {
    pullAuthority: NvkeOperatorInstallPlanPullAuthorityPanelProps | null;
  }
): NvkeOperatorInstallPlanPullSecretAnalysisPanelProps | null => {
  const parent = args.pullAuthority;
  let observed: NvkeInstallPlanPullAuthority | null = null;
  if (
    parent?.canReview &&
    !parent.loading &&
    !parent.error &&
    parent.artifacts &&
    args.source?.plan &&
    args.source.charts &&
    args.source.artifacts &&
    args.workload?.artifacts &&
    args.imageBinding?.artifacts
  ) {
    try {
      observed = installPlanPullSecretAnalysisSource(
        args.source.plan,
        args.source.charts,
        args.source.artifacts,
        args.workload.artifacts,
        args.imageBinding.artifacts,
        parent.artifacts
      ).pullAuthority;
    } catch {
      /* A changed pull observation cannot select a private interpretation. */
    }
  }
  const pull = observed;
  const parentInput = pull
    ? {
        pullAuthorityId: pull.id,
        expectedPullAuthorityVersion: pull.version,
        pullAuthorityDigest: pull.authorityDigest,
      }
    : null;
  const result = useNvkeInstallPlanArtifact(args, {
    mode: "pull-secret-analysis",
    extraSource: {
      ready: !!pull,
      pending: parent?.pending ?? false,
      epoch: JSON.stringify([
        parent?.scopeKey,
        parent?.artifacts,
        parent?.canReview,
        parent?.loading,
        parent?.error,
        parent?.pending,
      ]),
      binding: pull
        ? {
            pullAuthorityId: pull.id,
            pullAuthorityVersion: pull.version,
            pullAuthorityDigest: pull.authorityDigest,
          }
        : null,
    },
    parse: (value) => installPlanPullSecretAnalysisFormSchema.parse(value),
    request: (form, ...parents) => {
      if (!pull) throw new Error("SOURCE_CHANGED");
      return installPlanPullSecretAnalysisRequest(form, ...parents, pull);
    },
    validate: (value, parents, artifactId, limits) => {
      if (!pull) throw new Error("SOURCE_CHANGED");
      return validateInstallPlanPullSecretAnalysis(value, ...parents, pull, artifactId, limits);
    },
    mutate: async (client, action, input, artifactId, body, organizationId) => {
      if (!parentInput) throw new Error("SOURCE_CHANGED");
      const options = {
        fetchPolicy: "no-cache" as const,
        context: { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: organizationId },
      };
      if (action === "prepare") {
        const response = await client.mutate<
          PrepareNvkeInstallPlanPullSecretAnalysisMutation,
          PrepareNvkeInstallPlanPullSecretAnalysisMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS,
          variables: { input: { ...input, ...parentInput, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        return response.data?.prepareNvkeOperatorInstallPlanPullSecretAnalysis;
      }
      if (action === "review") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanPullSecretAnalysisMutation,
          ReviewNvkeInstallPlanPullSecretAnalysisMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS,
          variables: { input: { ...input, ...parentInput, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        return response.data?.reviewNvkeOperatorInstallPlanPullSecretAnalysis;
      }
      const response = await client.mutate<
        RetryNvkeInstallPlanPullSecretAnalysisMutation,
        ReviewNvkeInstallPlanPullSecretAnalysisMutationVariables
      >({
        mutation: RETRY_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS,
        variables: { input: { ...input, ...parentInput, artifactId } },
        ...options,
      });
      if (response.error) throw new Error("UNCONFIRMED");
      return response.data?.recoverNvkeOperatorInstallPlanPullSecretAnalysisPreparation;
    },
  });
  return result
    ? {
        ...result,
        pullAuthority: result.canReview ? pull : null,
        loading: result.loading || !!parent?.loading,
        error: result.error || parent?.error || null,
      }
    : null;
};
