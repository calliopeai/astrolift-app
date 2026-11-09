"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import type { NvkeOperatorInstallPlanTreePanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanTreePanel";
import {
  validateInstallPlanTree,
  type NvkeInstallPlanTree,
} from "@/lib/nvke/operator-install-plan-tree";
import {
  validateInstallPlanCharts,
  type NvkeInstallPlanCharts,
} from "@/lib/nvke/operator-install-plan-charts";
import type { NvkeOperatorInstallPlanContextPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel";
import type {
  PrepareNvkeInstallPlanContextMutation,
  PrepareNvkeInstallPlanContextMutationVariables,
  ReviewNvkeInstallPlanContextMutation,
  ReviewNvkeInstallPlanContextMutationVariables,
  RetryNvkeInstallPlanContextMutation,
} from "@/graphql/__generated__/operations";
import { installPlanSchema, installPlanMatches } from "@/lib/nvke/operator-install-plan";
import {
  installPlanContextRequest,
  installPlanContextFormSchema,
  installPlanContextSelectionSchema,
  type NvkeInstallPlanContextSelection,
  validateInstallPlanContext,
  type NvkeInstallPlanContext,
  type NvkeInstallPlanContextForm,
} from "@/lib/nvke/operator-install-plan-context";
import {
  readSelectedInstallPlanContext,
  selectInstallPlanContext,
  readInstallPlanContextJournal,
  saveInstallPlanContextJournal,
  clearSelectedInstallPlanContext,
  type NvkeInstallPlanContextJournalSource,
  type NvkeInstallPlanContextChoice,
} from "@/lib/nvke/install-plan-context-recovery";
import { withInstallPlanLock } from "@/lib/nvke/install-plan-recovery";
import { nativeTextDigest, validNvkeGuid, type RecoveryStorage } from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import {
  PREPARE_NVKE_INSTALL_PLAN_CONTEXT,
  REVIEW_NVKE_INSTALL_PLAN_CONTEXT,
  RETRY_NVKE_INSTALL_PLAN_CONTEXT,
} from "./nvke-install-plan-context.mutations";

type State = {
  key: string;
  selection: NvkeInstallPlanContextChoice | null;
  artifacts: NvkeInstallPlanContext | null;
  hasJournal: boolean;
  allowOriginalPreparation: boolean;
  error: string | null;
};

export const useNvkeInstallPlanContext = (args: {
  context: ReturnType<typeof useNvkeContext>;
  source: NvkeOperatorInstallPlanTreePanelProps | null;
  pending: boolean;
}): NvkeOperatorInstallPlanContextPanelProps | null => {
  const { context, source } = args;
  const client = useApolloClient(),
    t = useTranslations("nvke");
  const authority = source?.authority;
  const parsed = installPlanSchema.safeParse(source?.plan);
  const parent = parsed.success ? parsed.data : null;
  let charts: NvkeInstallPlanCharts | null = null;
  if (parent && source?.charts) {
    try {
      charts = validateInstallPlanCharts(source.charts, parent, source.charts.id);
    } catch {
      /* Invalidated chart publication. */
    }
  }
  let tree: NvkeInstallPlanTree | null = null;
  if (parent && charts && source?.artifacts) {
    try {
      tree = validateInstallPlanTree(source.artifacts, parent, charts, source.artifacts.id);
    } catch {
      /* Invalidated tree. */
    }
  }
  const ready = !!(
    source?.canReview &&
    !source.loading &&
    !source.error &&
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
    charts &&
    tree &&
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
    ready && context.scope && source && authority && parent && charts && tree
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
          chartArtifactId: charts.id,
          chartArtifactVersion: charts.version,
          chartArtifactDigest: charts.artifactDigest,
          treeId: tree.id,
          treeVersion: tree.version,
          treeDigest: tree.treeDigest,
        })
      : "";
  const binding = useMemo(
    () => (sourceText ? (JSON.parse(sourceText) as NvkeInstallPlanContextJournalSource) : null),
    [sourceText]
  );
  const [selectionEpoch, setSelectionEpoch] = useState(0);
  const { key, isCurrent: current } = useNvkeEpoch(
    JSON.stringify([
      sourceText,
      context.scopeKey,
      source?.scopeKey,
      source?.plan,
      source?.artifacts,
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
      let selection: NvkeInstallPlanContextChoice | null = null,
        hasJournal = false,
        error: string | null = null;
      try {
        selection = readSelectedInstallPlanContext(window.localStorage, binding);
        hasJournal =
          !!selection && !!readInstallPlanContextJournal(window.localStorage, binding, selection);
      } catch {
        selection = null;
        hasJournal = false;
        error = t("storageRequired");
      }
      if (active)
        setState({
          key,
          selection,
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
    selection: NvkeInstallPlanContextSelection,
    values?: NvkeInstallPlanContextForm
  ) => {
    const parsedSelection = installPlanContextSelectionSchema.safeParse(selection);
    if (!parsedSelection.success) return false;
    if (
      !ready ||
      !binding ||
      !parent ||
      !charts ||
      !tree ||
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
      artifactId === binding.chartArtifactId ||
      artifactId === binding.treeId ||
      (visible.selection &&
        (visible.selection.artifactId !== artifactId ||
          visible.selection.item !== selection.item ||
          visible.selection.ordinal !== selection.ordinal))
    )
      return false;
    if (action === "prepare" && visible.selection && !visible.allowOriginalPreparation)
      return false;
    if (parsedSelection.data.ordinal >= parent.itemCount) return false;
    const chosen = { ...parsedSelection.data, artifactId };
    let body: ReturnType<typeof installPlanContextRequest> | undefined;
    try {
      if (action === "prepare") {
        const form = installPlanContextFormSchema.parse(values);
        if (
          form.artifactId !== artifactId ||
          form.item !== chosen.item ||
          Number(form.ordinal) !== chosen.ordinal
        )
          return false;
        body = installPlanContextRequest(form, parent, charts, tree);
      }
    } catch {
      return false;
    }
    busy.current = true;
    setInFlight(true);
    let hasJournal = false,
      contacted = false;
    setState({ ...visible, selection: chosen, artifacts: null, error: null });
    const execute = async () => {
      if (!current()) throw new Error("SOURCE_CHANGED");
      let storage: RecoveryStorage | null = null;
      try {
        storage = window.localStorage;
      } catch {
        if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
      }
      if (storage) {
        let selected: NvkeInstallPlanContextChoice | null = null;
        try {
          selected = readSelectedInstallPlanContext(storage, binding);
        } catch {
          if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
          storage = null;
        }
        if (
          selected &&
          (selected.artifactId !== artifactId ||
            selected.item !== chosen.item ||
            selected.ordinal !== chosen.ordinal)
        )
          throw new Error("RECOVERY_MISMATCH");
      }
      if (action === "prepare") {
        if (!storage || !body) throw new Error("INVALID_INPUT");
        hasJournal = !!readInstallPlanContextJournal(storage, binding, chosen);
        const digest = await nativeTextDigest(JSON.stringify(body));
        if (!current()) throw new Error("SOURCE_CHANGED");
        saveInstallPlanContextJournal(storage, binding, chosen, digest);
        hasJournal = true;
      } else if (action === "retry") {
        if (!storage || !readInstallPlanContextJournal(storage, binding, chosen))
          throw new Error("RECOVERY_MISMATCH");
        hasJournal = true;
      } else if (storage) {
        try {
          hasJournal = !!readInstallPlanContextJournal(storage, binding, chosen);
          selectInstallPlanContext(storage, binding, chosen);
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
        chartArtifactId: binding.chartArtifactId,
        expectedChartArtifactVersion: binding.chartArtifactVersion,
        chartArtifactDigest: binding.chartArtifactDigest,
        treeId: binding.treeId,
        expectedTreeVersion: binding.treeVersion,
        treeDigest: binding.treeDigest,
        item: chosen.item,
        ordinal: chosen.ordinal,
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
          PrepareNvkeInstallPlanContextMutation,
          PrepareNvkeInstallPlanContextMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_CONTEXT,
          variables: { input: { ...input, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.prepareNvkeOperatorInstallPlanContext;
      } else if (action === "review") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanContextMutation,
          ReviewNvkeInstallPlanContextMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_CONTEXT,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.reviewNvkeOperatorInstallPlanContext;
      } else {
        const response = await client.mutate<
          RetryNvkeInstallPlanContextMutation,
          ReviewNvkeInstallPlanContextMutationVariables
        >({
          mutation: RETRY_NVKE_INSTALL_PLAN_CONTEXT,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.recoverNvkeOperatorInstallPlanContextPreparation;
      }
      if (!current()) throw new Error("SOURCE_CHANGED");
      if (envelope?.ok !== true || !Array.isArray(envelope.errors) || envelope.errors.length)
        throw new Error("UNCONFIRMED");
      const artifacts = validateInstallPlanContext(
        envelope.data,
        parent,
        charts,
        tree,
        artifactId,
        parsedSelection.data
      );
      setState({
        key,
        selection: chosen,
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
          selection: chosen,
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
      !visible?.selection ||
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
          if (readSelectedInstallPlanContext(window.localStorage, binding) !== null)
            clearSelectedInstallPlanContext(window.localStorage, binding, visible.selection!);
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
    charts: ready ? charts : null,
    tree: ready ? tree : null,
    artifacts: visible?.artifacts ?? null,
    retainedArtifactId: visible?.selection?.artifactId ?? null,
    retainedSelection: visible?.selection
      ? { item: visible.selection.item, ordinal: visible.selection.ordinal }
      : null,
    canReview: ready && !!visible,
    canRetry: ready && !!visible?.hasJournal,
    allowOriginalPreparation: visible?.allowOriginalPreparation ?? false,
    pending: args.pending || source.pending || inFlight,
    loading: source.loading || (ready && !visible),
    error: source.error || visible?.error || null,
    onPrepare: (values) =>
      run(
        "prepare",
        values.artifactId,
        { item: values.item, ordinal: Number(values.ordinal) },
        values
      ),
    onReview: (id, selected) => run("review", id, selected),
    onRetryPreparation: (id, selected) => run("retry", id, selected),
    onChooseArtifact:
      visible?.selection &&
      (!visible.hasJournal || !!visible.artifacts || visible.allowOriginalPreparation)
        ? onChooseArtifact
        : undefined,
  };
};
