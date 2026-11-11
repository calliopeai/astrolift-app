"use client";

import { useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import type { NvkeOperatorInstallPlanPullRuntimePanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullRuntimePanel";
import type { NvkeOperatorInstallPlanPullAuthorityPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullAuthorityPanel";
import type { NvkeOperatorInstallPlanPullSecretAnalysisPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullSecretAnalysisPanel";
import type { NvkeOperatorInstallPlanNodePlatformsPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanNodePlatformsPanel";
import { installPlanMatches } from "@/lib/nvke/operator-install-plan";
import {
  installPlanPullRuntimeSource,
  validateInstallPlanPullRuntime,
  type NvkeInstallPlanPullRuntime,
} from "@/lib/nvke/operator-install-plan-pull-runtime";
import { withInstallPlanLock } from "@/lib/nvke/install-plan-recovery";
import type {
  ReviewNvkeInstallPlanPullRuntimeMutation,
  ReviewNvkeInstallPlanPullRuntimeMutationVariables,
} from "@/graphql/__generated__/operations";
import type { InstallPlanArtifactArgs } from "./use-nvke-install-plan-artifact";
import { useNvkeEpoch } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import { REVIEW_NVKE_INSTALL_PLAN_PULL_RUNTIME } from "./nvke-install-plan-pull-runtime.mutations";

type Args = InstallPlanArtifactArgs & {
  pullAuthority: NvkeOperatorInstallPlanPullAuthorityPanelProps | null;
  analysis: NvkeOperatorInstallPlanPullSecretAnalysisPanelProps | null;
  nodePlatforms: NvkeOperatorInstallPlanNodePlatformsPanelProps | null;
};
type State = {
  key: string;
  correspondence: NvkeInstallPlanPullRuntime | null;
  error: string | null;
};

export const useNvkeInstallPlanPullRuntime = (
  args: Args
): NvkeOperatorInstallPlanPullRuntimePanelProps | null => {
  const client = useApolloClient();
  const { context, source } = args;
  const parents = [
    source,
    args.workload,
    args.imageBinding,
    args.pullAuthority,
    args.analysis,
    args.nodePlatforms,
  ];
  const parentPending = args.pending || parents.some((parent) => parent?.pending);
  const loading =
    context.loading || context.access.loading || parents.some((parent) => parent?.loading);
  let selected: ReturnType<typeof installPlanPullRuntimeSource> | null = null;
  const authority = source?.authority;
  if (
    source &&
    source.canReview &&
    context.scope &&
    context.scopeKey &&
    context.access.read &&
    context.access.admin &&
    !loading &&
    !context.failed &&
    parents.every((parent) => parent?.canReview && !parent.error) &&
    source.plan &&
    source.charts &&
    source.artifacts &&
    args.workload?.artifacts &&
    args.imageBinding?.artifacts &&
    args.pullAuthority?.artifacts &&
    args.analysis?.artifacts &&
    args.nodePlatforms?.artifacts &&
    source.operation.operationKey === "tooling.management.install" &&
    source.operation.status === "UNCERTAIN" &&
    source.operation.siteId === source.site.id &&
    authority &&
    authority.siteId === source.site.id &&
    authority.siteVersion === source.site.version &&
    authority.isEnabled &&
    authority.deletedAt === null &&
    authority.hasCredentials &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    authority.verifiedAt &&
    Number.isFinite(Date.parse(authority.verifiedAt))
  ) {
    try {
      const candidate = installPlanPullRuntimeSource(
        source.plan,
        source.charts,
        source.artifacts,
        args.workload.artifacts,
        args.imageBinding.artifacts,
        args.pullAuthority.artifacts,
        args.analysis.artifacts,
        args.nodePlatforms.artifacts
      );
      if (
        installPlanMatches(candidate.plan, {
          siteId: source.site.id,
          siteVersion: source.site.version,
          operationId: source.operation.id,
          operationVersion: source.operation.version,
          authorityId: authority.id,
          authorityVersion: authority.version,
          instanceId: authority.instanceId,
          bundleManifestSha256: authority.bundleManifestSha256,
          nativeRevision: source.site.upstreamRevision,
        })
      )
        selected = candidate;
    } catch {
      // A read must bind the same complete source as both selected captures.
    }
  }
  const { key, isCurrent: current } = useNvkeEpoch(
    JSON.stringify([
      context.scope,
      context.scopeKey,
      context.access,
      context.loading,
      context.failed,
      parents.map(
        (parent) =>
          parent && [
            parent.scopeKey,
            parent.canReview,
            parent.loading,
            parent.error,
            parent.pending,
            parent.artifacts,
          ]
      ),
      source?.site,
      source?.operation,
      source?.authority,
      selected,
      args.pending,
    ])
  );
  const [state, setState] = useState<State | null>(null);
  const [inFlight, setInFlight] = useState(false);
  const busy = useRef(false);
  const visible = selected && state?.key === key ? state : null;
  const onReview = async () => {
    if (
      !selected ||
      !context.scope ||
      !source ||
      !current() ||
      busy.current ||
      parentPending ||
      loading
    )
      return false;
    const captured = selected,
      scope = { ...context.scope };
    busy.current = true;
    setInFlight(true);
    setState({ key, correspondence: null, error: null });
    try {
      return await withInstallPlanLock(scope, captured.plan.operationId, async () => {
        if (!current()) return false;
        const input: ReviewNvkeInstallPlanPullRuntimeMutationVariables["input"] = {
          operationId: captured.plan.operationId,
          expectedActorId: scope.actorId,
          expectedSiteVersion: captured.plan.siteVersion,
          expectedAuthorityVersion: captured.plan.authorityVersion,
          expectedOperationVersion: captured.plan.operationVersion,
          planId: captured.plan.id,
          expectedPlanVersion: captured.plan.version,
          planDigest: captured.plan.plan.planDigest,
          chartArtifactId: captured.charts.id,
          expectedChartArtifactVersion: captured.charts.version,
          chartArtifactDigest: captured.charts.artifactDigest,
          treeId: captured.tree.id,
          expectedTreeVersion: captured.tree.version,
          treeDigest: captured.tree.treeDigest,
          workloadId: captured.workload.id,
          expectedWorkloadVersion: captured.workload.version,
          workloadDigest: captured.workload.workloadDigest,
          bindingId: captured.binding.id,
          expectedBindingVersion: captured.binding.version,
          bindingDigest: captured.binding.bindingDigest,
          pullAuthorityId: captured.pullAuthority.id,
          expectedPullAuthorityVersion: captured.pullAuthority.version,
          pullAuthorityDigest: captured.pullAuthority.authorityDigest,
          analysisId: captured.analysis.id,
          expectedAnalysisVersion: captured.analysis.version,
          analysisDigest: captured.analysis.authorityDigest,
          nodePlatformId: captured.nodePlatforms.id,
          expectedNodePlatformVersion: captured.nodePlatforms.version,
          nodePlatformDigest: captured.nodePlatforms.platformDigest,
        };
        const response = await client.mutate<
          ReviewNvkeInstallPlanPullRuntimeMutation,
          ReviewNvkeInstallPlanPullRuntimeMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_PULL_RUNTIME,
          variables: { input },
          fetchPolicy: "no-cache",
          context: { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: scope.organizationId },
        });
        if (!current()) return false;
        const envelope = response.data?.reviewNvkeOperatorInstallPlanPullRuntime;
        if (response.error || !envelope?.ok || !envelope.data) throw new Error("UNCONFIRMED");
        const correspondence = validateInstallPlanPullRuntime(envelope.data, captured);
        if (!current()) return false;
        setState({ key, correspondence, error: null });
        return true;
      });
    } catch {
      if (current()) setState({ key, correspondence: null, error: "UNCONFIRMED" });
      return false;
    } finally {
      busy.current = false;
      setInFlight(false);
    }
  };
  if (
    !source ||
    source.operation.operationKey !== "tooling.management.install" ||
    source.operation.status !== "UNCERTAIN"
  )
    return null;
  return {
    scopeKey: key,
    site: source.site,
    operation: source.operation,
    authority: source.authority,
    canReview: !!selected,
    pending: !!parentPending || inFlight,
    loading,
    error:
      visible?.error ??
      (context.failed || parents.some((parent) => parent?.error) ? "SOURCE_UNAVAILABLE" : null),
    plan: selected?.plan ?? null,
    charts: selected?.charts ?? null,
    tree: selected?.tree ?? null,
    workload: selected?.workload ?? null,
    binding: selected?.binding ?? null,
    pullAuthority: selected?.pullAuthority ?? null,
    analysis: selected?.analysis ?? null,
    nodePlatforms: selected?.nodePlatforms ?? null,
    correspondence: parentPending || inFlight ? null : (visible?.correspondence ?? null),
    onReview,
  };
};
