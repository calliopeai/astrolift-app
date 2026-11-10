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
import type { NvkeOperatorInstallPlanImagePanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanImagePanel";
import type {
  PrepareNvkeInstallPlanImageMutation,
  PrepareNvkeInstallPlanImageMutationVariables,
  ReviewNvkeInstallPlanImageMutation,
  ReviewNvkeInstallPlanImageMutationVariables,
  RetryNvkeInstallPlanImageMutation,
} from "@/graphql/__generated__/operations";
import {
  validateInstallPlanWorkload,
  type NvkeInstallPlanWorkload,
} from "@/lib/nvke/operator-install-plan-workload";
import { installPlanSchema, installPlanMatches } from "@/lib/nvke/operator-install-plan";
import {
  installPlanImageRequest,
  installPlanImageFormSchema,
  validateInstallPlanImage,
  type NvkeInstallPlanImage,
  type NvkeInstallPlanImageForm,
} from "@/lib/nvke/operator-install-plan-image";
import {
  readSelectedInstallPlanImage,
  selectInstallPlanImage,
  readInstallPlanImageJournal,
  saveInstallPlanImageJournal,
  clearSelectedInstallPlanImage,
  type NvkeInstallPlanImageJournalSource,
  type NvkeInstallPlanImageChoice,
} from "@/lib/nvke/install-plan-image-recovery";
import { withInstallPlanLock } from "@/lib/nvke/install-plan-recovery";
import { nativeTextDigest, validNvkeGuid, type RecoveryStorage } from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import {
  PREPARE_NVKE_INSTALL_PLAN_IMAGE,
  REVIEW_NVKE_INSTALL_PLAN_IMAGE,
  RETRY_NVKE_INSTALL_PLAN_IMAGE,
} from "./nvke-install-plan-image.mutations";

type State = {
  key: string;
  selection: NvkeInstallPlanImageChoice | null;
  artifacts: NvkeInstallPlanImage | null;
  hasJournal: boolean;
  allowOriginalPreparation: boolean;
  error: string | null;
};

export const useNvkeInstallPlanImage = (args: {
  context: ReturnType<typeof useNvkeContext>;
  source: NvkeOperatorInstallPlanTreePanelProps | null;
  pending: boolean;
  workload?: NvkeInstallPlanWorkload | null;
}): NvkeOperatorInstallPlanImagePanelProps | null => {
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
  let workload: NvkeInstallPlanWorkload | undefined;
  if (parent && charts && tree && args.workload) {
    try {
      workload = validateInstallPlanWorkload(args.workload, parent, charts, tree, args.workload.id);
    } catch {
      /* Source must be reviewed again. */
    }
  }
  const ready = !!(
    (args.workload === undefined || workload) &&
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
          ...(workload
            ? {
                workloadId: workload.id,
                workloadVersion: workload.version,
                workloadDigest: workload.workloadDigest,
              }
            : {}),
        })
      : "";
  const binding = useMemo(
    () => (sourceText ? (JSON.parse(sourceText) as NvkeInstallPlanImageJournalSource) : null),
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
      let selection: NvkeInstallPlanImageChoice | null = null,
        hasJournal = false,
        error: string | null = null;
      try {
        selection = readSelectedInstallPlanImage(window.localStorage, binding);
        hasJournal =
          !!selection && !!readInstallPlanImageJournal(window.localStorage, binding, selection);
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
    values?: NvkeInstallPlanImageForm
  ) => {
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
      artifactId === binding.workloadId ||
      (visible.selection && visible.selection.artifactId !== artifactId)
    )
      return false;
    if (action === "prepare" && visible.selection && !visible.allowOriginalPreparation)
      return false;
    const chosen = { artifactId };
    let body: ReturnType<typeof installPlanImageRequest> | undefined;
    try {
      if (action === "prepare") {
        const form = installPlanImageFormSchema.parse(values);
        if (form.artifactId !== artifactId) return false;
        body = installPlanImageRequest(
          form,
          parent,
          charts,
          tree,
          args.workload === undefined ? undefined : (workload ?? null)
        );
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
        let selected: NvkeInstallPlanImageChoice | null = null;
        try {
          selected = readSelectedInstallPlanImage(storage, binding);
        } catch {
          if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
          storage = null;
        }
        if (selected && selected.artifactId !== artifactId) throw new Error("RECOVERY_MISMATCH");
      }
      if (action === "prepare") {
        if (!storage || !body) throw new Error("INVALID_INPUT");
        hasJournal = !!readInstallPlanImageJournal(storage, binding, chosen);
        const digest = await nativeTextDigest(JSON.stringify(body));
        if (!current()) throw new Error("SOURCE_CHANGED");
        saveInstallPlanImageJournal(storage, binding, chosen, digest);
        hasJournal = true;
      } else if (action === "retry") {
        if (!storage || !readInstallPlanImageJournal(storage, binding, chosen))
          throw new Error("RECOVERY_MISMATCH");
        hasJournal = true;
      } else if (storage) {
        try {
          hasJournal = !!readInstallPlanImageJournal(storage, binding, chosen);
          selectInstallPlanImage(storage, binding, chosen);
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
        workloadId: binding.workloadId ?? null,
        expectedWorkloadVersion: binding.workloadVersion ?? null,
        workloadDigest: binding.workloadDigest ?? null,
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
          PrepareNvkeInstallPlanImageMutation,
          PrepareNvkeInstallPlanImageMutationVariables
        >({
          mutation: PREPARE_NVKE_INSTALL_PLAN_IMAGE,
          variables: { input: { ...input, requestText: JSON.stringify(body) } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.prepareNvkeOperatorInstallPlanImage;
      } else if (action === "review") {
        const response = await client.mutate<
          ReviewNvkeInstallPlanImageMutation,
          ReviewNvkeInstallPlanImageMutationVariables
        >({
          mutation: REVIEW_NVKE_INSTALL_PLAN_IMAGE,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.reviewNvkeOperatorInstallPlanImage;
      } else {
        const response = await client.mutate<
          RetryNvkeInstallPlanImageMutation,
          ReviewNvkeInstallPlanImageMutationVariables
        >({
          mutation: RETRY_NVKE_INSTALL_PLAN_IMAGE,
          variables: { input: { ...input, artifactId } },
          ...options,
        });
        if (response.error) throw new Error("UNCONFIRMED");
        envelope = response.data?.recoverNvkeOperatorInstallPlanImagePreparation;
      }
      if (!current()) throw new Error("SOURCE_CHANGED");
      if (envelope?.ok !== true || !Array.isArray(envelope.errors) || envelope.errors.length)
        throw new Error("UNCONFIRMED");
      const artifacts = validateInstallPlanImage(
        envelope.data,
        parent,
        charts,
        tree,
        artifactId,
        action === "prepare" ? body : undefined,
        args.workload === undefined ? undefined : (workload ?? null)
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
          if (readSelectedInstallPlanImage(window.localStorage, binding) !== null)
            clearSelectedInstallPlanImage(window.localStorage, binding, visible.selection!);
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
    workload: args.workload === undefined ? undefined : (workload ?? null),
    scopeKey: key,
    site: source.site,
    operation: source.operation,
    authority: ready ? source.authority : null,
    plan: ready ? source.plan : null,
    charts: ready ? charts : null,
    tree: ready ? tree : null,
    artifacts: visible?.artifacts ?? null,
    retainedArtifactId: visible?.selection?.artifactId ?? null,
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
      visible?.selection &&
      (!visible.hasJournal || !!visible.artifacts || visible.allowOriginalPreparation)
        ? onChooseArtifact
        : undefined,
  };
};
