"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import type { NvkeOperatorInstallPlanPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPanel";
import type { NvkeOperatorInstallPlanChartsPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanChartsPanel";
import type {
  PrepareNvkeInstallPlanChartsMutation,
  PrepareNvkeInstallPlanChartsMutationVariables,
  ReviewNvkeInstallPlanChartsMutation,
  ReviewNvkeInstallPlanChartsMutationVariables,
  RetryNvkeInstallPlanChartsMutation,
} from "@/graphql/__generated__/operations";
import { installPlanSchema, installPlanMatches } from "@/lib/nvke/operator-install-plan";
import {
  installPlanChartsRequest,
  validateInstallPlanCharts,
  type NvkeInstallPlanCharts,
  type NvkeInstallPlanChartsForm,
} from "@/lib/nvke/operator-install-plan-charts";
import {
  readSelectedInstallPlanCharts,
  selectInstallPlanCharts,
  readInstallPlanChartsJournal,
  saveInstallPlanChartsJournal,
  clearSelectedInstallPlanCharts,
  type NvkeInstallPlanChartsJournalSource,
} from "@/lib/nvke/install-plan-charts-recovery";
import { withInstallPlanLock } from "@/lib/nvke/install-plan-recovery";
import { nativeTextDigest, validNvkeGuid, type RecoveryStorage } from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import {
  PREPARE_NVKE_INSTALL_PLAN_CHARTS,
  REVIEW_NVKE_INSTALL_PLAN_CHARTS,
  RETRY_NVKE_INSTALL_PLAN_CHARTS,
} from "./nvke-install-plan-charts.mutations";

type State = {
  key: string;
  artifactId: string | null;
  artifacts: NvkeInstallPlanCharts | null;
  hasJournal: boolean;
  allowOriginalPreparation: boolean;
  error: string | null;
};

export const useNvkeInstallPlanCharts = (args: {
  context: ReturnType<typeof useNvkeContext>;
  source: NvkeOperatorInstallPlanPanelProps | null;
  pending: boolean;
}): NvkeOperatorInstallPlanChartsPanelProps | null => {
  const { context, source } = args;
  const client = useApolloClient(),
    t = useTranslations("nvke");
  const authority = source?.authority;
  const parsed = installPlanSchema.safeParse(source?.plan);
  const parent = parsed.success ? parsed.data : null;
  const ready = !!(
    source?.canReview &&
    source.scopeKey &&
    source.operation.operationKey === "tooling.management.install" &&
    source.operation.status === "UNCERTAIN" &&
    source.operation.siteId === source.site.id &&
    context.scope &&
    context.scopeKey &&
    context.access.admin &&
    context.access.read &&
    !context.access.loading &&
    !context.loading &&
    !context.failed &&
    authority &&
    authority.siteId === source.site.id &&
    authority.siteVersion === source.site.version &&
    authority.isEnabled &&
    authority.deletedAt === null &&
    authority.hasCredentials &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    authority.verifiedAt &&
    Number.isFinite(Date.parse(authority.verifiedAt)) &&
    parent &&
    installPlanMatches(parent, {
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
  );
  const sourceText =
    ready && context.scope && source && authority && parent
      ? JSON.stringify({
          ...context.scope,
          siteId: source.site.id,
          siteVersion: source.site.version,
          operationId: source.operation.id,
          operationVersion: source.operation.version,
          authorityId: authority.id,
          authorityVersion: authority.version,
          instanceId: authority.instanceId,
          bundleManifestSha256: authority.bundleManifestSha256,
          nativeRevision: source.site.upstreamRevision,
          planId: parent.id,
          planVersion: parent.version,
          planDigest: parent.plan.planDigest,
        })
      : "";
  const binding = useMemo(
    () => (sourceText ? (JSON.parse(sourceText) as NvkeInstallPlanChartsJournalSource) : null),
    [sourceText]
  );
  const [selectionEpoch, setSelectionEpoch] = useState(0);
  const { key, isCurrent: current } = useNvkeEpoch(
    JSON.stringify([
      sourceText,
      context.scopeKey,
      source?.scopeKey,
      source?.plan,
      ready,
      selectionEpoch,
    ])
  );
  const [state, setState] = useState<State | null>(null);
  const [inFlight, setInFlight] = useState(false);
  const busy = useRef(false);
  const visible = ready && state?.key === key ? state : null;

  useEffect(() => {
    if (!binding) return;
    let active = true;
    Promise.resolve().then(() => {
      let artifactId: string | null = null,
        hasJournal = false,
        error: string | null = null;
      try {
        artifactId = readSelectedInstallPlanCharts(
          window.localStorage,
          binding,
          binding.operationId,
          binding.planId
        );
        hasJournal =
          !!artifactId && !!readInstallPlanChartsJournal(window.localStorage, binding, artifactId);
      } catch {
        error = t("storageRequired");
      }
      if (active)
        setState({
          key,
          artifactId,
          artifacts: null,
          hasJournal,
          error,
          allowOriginalPreparation: false,
        });
    });
    return () => {
      active = false;
    };
  }, [binding, key, t]);

  const run = async (
    action: "prepare" | "review" | "retry",
    artifactId: string,
    values?: NvkeInstallPlanChartsForm
  ) => {
    if (
      !ready ||
      !binding ||
      !parent ||
      !visible ||
      !current() ||
      busy.current ||
      args.pending ||
      source?.pending ||
      source?.loading ||
      source?.error ||
      !validNvkeGuid(artifactId) ||
      artifactId === binding.operationId ||
      artifactId === binding.planId ||
      (visible.artifactId && visible.artifactId !== artifactId)
    )
      return false;
    if (action === "prepare" && visible.artifactId && !visible.allowOriginalPreparation)
      return false;
    busy.current = true;
    setInFlight(true);
    let hasJournal = false,
      contacted = false;
    setState({ ...visible, artifactId, artifacts: null, error: null });
    const execute = async () => {
      if (!current()) throw new Error("SOURCE_CHANGED");
      let storage: RecoveryStorage | null = null;
      try {
        storage = window.localStorage;
      } catch {
        if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
      }
      if (storage) {
        let selected: string | null = null;
        try {
          selected = readSelectedInstallPlanCharts(
            storage,
            binding,
            binding.operationId,
            binding.planId
          );
        } catch {
          if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
          storage = null;
        }
        if (selected && selected !== artifactId) throw new Error("RECOVERY_MISMATCH");
      }
      let body: ReturnType<typeof installPlanChartsRequest> | undefined;
      if (action === "prepare") {
        if (
          !storage ||
          !values ||
          values.artifactId !== artifactId ||
          (visible.artifactId && !visible.allowOriginalPreparation)
        )
          throw new Error("INVALID_INPUT");
        body = installPlanChartsRequest(values, parent);
        hasJournal = !!readInstallPlanChartsJournal(storage, binding, artifactId);
        const digest = await nativeTextDigest(JSON.stringify(body));
        if (!current()) throw new Error("SOURCE_CHANGED");
        saveInstallPlanChartsJournal(storage, binding, artifactId, digest);
        hasJournal = true;
      } else if (action === "retry") {
        if (!storage || !readInstallPlanChartsJournal(storage, binding, artifactId))
          throw new Error("RECOVERY_MISMATCH");
        hasJournal = true;
      } else if (storage) {
        try {
          hasJournal = !!readInstallPlanChartsJournal(storage, binding, artifactId);
          selectInstallPlanCharts(
            storage,
            binding,
            binding.operationId,
            binding.planId,
            artifactId
          );
        } catch {
          hasJournal = false;
        }
      }
      if (!current()) throw new Error("SOURCE_CHANGED");
      const input = {
        operationId: binding.operationId,
        expectedActorId: binding.actorId,
        expectedSiteVersion: binding.siteVersion,
        expectedOperationVersion: binding.operationVersion,
        expectedAuthorityVersion: binding.authorityVersion,
        planId: binding.planId,
        expectedPlanVersion: binding.planVersion,
        planDigest: binding.planDigest,
      };
      const options = {
        fetchPolicy: "no-cache" as const,
        context: {
          ...NVKE_REQUEST_CONTEXT,
          expectedNvkeOrganization: binding.organizationId,
        },
      };
      contacted = true;
      let envelope:
        | { ok?: boolean; errors?: readonly unknown[] | null; data?: unknown }
        | null
        | undefined;
      if (action === "prepare") {
        const response = await client.mutate<
          PrepareNvkeInstallPlanChartsMutation,
          PrepareNvkeInstallPlanChartsMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_CHARTS,
          variables: { input: { ...input, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.prepareNvkeOperatorInstallPlanCharts;
      } else if (action === "review") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanChartsMutation,
          ReviewNvkeInstallPlanChartsMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_CHARTS,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.reviewNvkeOperatorInstallPlanCharts;
      } else {
        const response = await client.mutate<
          RetryNvkeInstallPlanChartsMutation,
          ReviewNvkeInstallPlanChartsMutationVariables
        >({
          mutation: RETRY_NVKE_INSTALL_PLAN_CHARTS,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.recoverNvkeOperatorInstallPlanChartsPreparation;
      }
      if (!current()) throw new Error("SOURCE_CHANGED");
      if (envelope?.ok !== true || !Array.isArray(envelope.errors) || envelope.errors.length)
        throw new Error("UNCONFIRMED");
      const artifacts = validateInstallPlanCharts(envelope.data, parent, artifactId, values);
      setState({
        key,
        artifactId,
        artifacts,
        hasJournal,
        error: null,
        allowOriginalPreparation: false,
      });
      return true;
    };
    try {
      return action === "review" && !navigator.locks?.request
        ? await execute()
        : await withInstallPlanLock(binding, binding.operationId, execute);
    } catch {
      if (current())
        setState({
          key,
          artifactId,
          artifacts: null,
          hasJournal,
          error: t("requestFailed"),
          allowOriginalPreparation:
            (action === "review" && hasJournal) ||
            (!contacted && hasJournal && visible.allowOriginalPreparation),
        });
      return false;
    } finally {
      busy.current = false;
      setInFlight(false);
    }
  };
  const onChooseArtifact = async () => {
    if (
      !binding ||
      !visible?.artifactId ||
      !current() ||
      busy.current ||
      args.pending ||
      source?.pending ||
      (visible.hasJournal && !visible.artifacts && !visible.allowOriginalPreparation)
    )
      return;
    busy.current = true;
    setInFlight(true);
    try {
      const choose = async () => {
        if (!current()) return;
        try {
          if (
            readSelectedInstallPlanCharts(
              window.localStorage,
              binding,
              binding.operationId,
              binding.planId
            ) !== null
          )
            clearSelectedInstallPlanCharts(
              window.localStorage,
              binding,
              binding.operationId,
              binding.planId,
              visible.artifactId!
            );
        } catch (error) {
          if (visible.hasJournal) throw error;
        }
        if (current()) setSelectionEpoch((value) => value + 1);
      };
      if (!navigator.locks?.request && !visible.hasJournal) await choose();
      else await withInstallPlanLock(binding, binding.operationId, choose);
    } catch {
      if (current()) setState({ ...visible, error: t("storageRequired") });
    } finally {
      busy.current = false;
      setInFlight(false);
    }
  };
  if (!source) return null;
  return {
    scopeKey: key,
    site: source.site,
    operation: source.operation,
    authority: ready ? source.authority : null,
    plan: ready ? source.plan : null,
    artifacts: visible?.artifacts ?? null,
    retainedArtifactId: visible?.artifactId ?? null,
    canReview: ready && !!visible,
    canRetry: ready && !!visible?.hasJournal,
    allowOriginalPreparation: visible?.allowOriginalPreparation ?? false,
    pending: args.pending || source.pending || inFlight,
    loading: source.loading || (ready && !visible),
    error: source.error || visible?.error || null,
    onPrepare: (values) => run("prepare", values.artifactId, values),
    onReview: (id) => run("review", id),
    onRetryPreparation: (id) => run("retry", id),
    onChooseArtifact:
      visible?.artifactId &&
      (!visible.hasJournal || !!visible.artifacts || visible.allowOriginalPreparation)
        ? onChooseArtifact
        : undefined,
  };
};
