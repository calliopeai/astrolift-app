"use client";
import {
  validateInstallPlanWorkload,
  type NvkeInstallPlanWorkload,
} from "@/lib/nvke/operator-install-plan-workload";
import type { NvkeOperatorInstallPlanWorkloadPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanWorkloadPanel";
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

import {
  validateInstallPlanImageBinding,
  type NvkeInstallPlanImageBinding,
} from "@/lib/nvke/operator-install-plan-image-binding";
import type { NvkeOperatorInstallPlanImageBindingPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanImageBindingPanel";

import {
  type NvkeInstallPlan,
  installPlanSchema,
  installPlanMatches,
} from "@/lib/nvke/operator-install-plan";

import {
  readSelectedInstallPlanImmutable,
  selectInstallPlanImmutable,
  readInstallPlanImmutableJournal,
  saveInstallPlanImmutableJournal,
  clearSelectedInstallPlanImmutable,
  type NvkeInstallPlanImmutableJournalSource,
  type NvkeInstallPlanImmutableChoice,
} from "@/lib/nvke/install-plan-immutable-recovery";
import { withInstallPlanLock } from "@/lib/nvke/install-plan-recovery";
import { nativeTextDigest, validNvkeGuid, type RecoveryStorage } from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";

import type { ReviewNvkeInstallPlanImagePayloadMutationVariables } from "@/graphql/__generated__/operations";
export type InstallPlanArtifactArgs = {
  context: ReturnType<typeof useNvkeContext>;
  imageBinding: NvkeOperatorInstallPlanImageBindingPanelProps | null;
  source: NvkeOperatorInstallPlanTreePanelProps | null;
  pending: boolean;
  workload: NvkeOperatorInstallPlanWorkloadPanelProps | null;
};
type Parents = [
  NvkeInstallPlan,
  NvkeInstallPlanCharts,
  NvkeInstallPlanTree,
  NvkeInstallPlanWorkload,
  NvkeInstallPlanImageBinding,
];
export type InstallPlanArtifactEnvelope =
  | { ok?: boolean; errors?: readonly unknown[] | null; data?: unknown }
  | null
  | undefined;
type Policy<Form, Artifact> = {
  mode: NvkeInstallPlanImmutableJournalSource["mode"];
  parse: (value: unknown) => Form;
  request: (form: Form, ...parents: Parents) => object;
  validate: (value: unknown, parents: Parents, artifactId: string, limits?: Form) => Artifact;
  mutate: (
    client: ReturnType<typeof useApolloClient>,
    action: "prepare" | "review" | "retry",
    input: Omit<ReviewNvkeInstallPlanImagePayloadMutationVariables["input"], "artifactId">,
    artifactId: string,
    body: object | undefined,
    organizationId: string
  ) => Promise<InstallPlanArtifactEnvelope>;
};
type State<Artifact> = {
  key: string;
  selection: NvkeInstallPlanImmutableChoice | null;
  artifacts: Artifact | null;
  hasJournal: boolean;
  allowOriginalPreparation: boolean;
  error: string | null;
};

export const useNvkeInstallPlanArtifact = <Form extends { artifactId: string }, Artifact>(
  args: InstallPlanArtifactArgs,
  policy: Policy<Form, Artifact>
) => {
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
  let inventory: NvkeInstallPlanWorkload | null = null;
  if (
    parent &&
    charts &&
    tree &&
    args.workload?.canReview &&
    !args.workload.loading &&
    !args.workload.error &&
    args.workload.artifacts
  ) {
    try {
      inventory = validateInstallPlanWorkload(
        args.workload.artifacts,
        parent,
        charts,
        tree,
        args.workload.artifacts.id
      );
    } catch {
      /* Workload source changed. */
    }
  }
  let images: NvkeInstallPlanImageBinding | null = null;
  if (
    parent &&
    charts &&
    tree &&
    inventory &&
    args.imageBinding?.canReview &&
    !args.imageBinding.loading &&
    !args.imageBinding.error &&
    args.imageBinding.artifacts
  ) {
    try {
      images = validateInstallPlanImageBinding(
        args.imageBinding.artifacts,
        parent,
        charts,
        tree,
        inventory,
        args.imageBinding.artifacts.id
      );
    } catch {
      /* Reviewed image binding changed. */
    }
  }
  const ready = !!(
    images &&
    inventory &&
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
    ready && context.scope && source && authority && parent && charts && tree && inventory && images
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
          workloadId: inventory.id,
          workloadVersion: inventory.version,
          workloadDigest: inventory.workloadDigest,
          mode: policy.mode,
          bindingId: images.id,
          bindingVersion: images.version,
          bindingDigest: images.bindingDigest,
        })
      : "";
  const binding = useMemo(
    () => (sourceText ? (JSON.parse(sourceText) as NvkeInstallPlanImmutableJournalSource) : null),
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
      source?.pending,
      args.workload?.scopeKey,
      args.workload?.artifacts,
      args.workload?.pending,
      args.imageBinding?.scopeKey,
      args.imageBinding?.artifacts,
      args.imageBinding?.pending,
      ready,
      selectionEpoch,
    ])
  );
  const [state, setState] = useState<State<Artifact> | null>(null);
  const [inFlight, setInFlight] = useState(false);
  const busy = useRef(false);
  const visible = ready && state?.key === key ? state : null;

  useEffect(() => {
    if (!binding) return;
    let active = true;
    Promise.resolve().then(() => {
      let selection: NvkeInstallPlanImmutableChoice | null = null,
        hasJournal = false,
        error: string | null = null;
      try {
        selection = readSelectedInstallPlanImmutable(window.localStorage, binding);
        hasJournal =
          !!selection && !!readInstallPlanImmutableJournal(window.localStorage, binding, selection);
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

  const run = async (action: "prepare" | "review" | "retry", artifactId: string, values?: Form) => {
    if (
      !ready ||
      !binding ||
      !parent ||
      !charts ||
      !tree ||
      !inventory ||
      !images ||
      !visible ||
      !current() ||
      busy.current ||
      args.pending ||
      args.workload?.pending ||
      args.imageBinding?.pending ||
      source?.pending ||
      source?.loading ||
      source?.error ||
      !validNvkeGuid(artifactId) ||
      artifactId === binding.operationId ||
      artifactId === binding.planId ||
      artifactId === binding.chartArtifactId ||
      artifactId === binding.treeId ||
      artifactId === binding.workloadId ||
      artifactId === binding.bindingId ||
      (visible.selection && visible.selection.artifactId !== artifactId)
    )
      return false;
    if (action === "prepare" && visible.selection && !visible.allowOriginalPreparation)
      return false;
    const chosen = { artifactId };
    let body: object | undefined;
    let reviewedForm: Form | undefined;
    try {
      if (action === "prepare") {
        const form = policy.parse(values);
        reviewedForm = form;
        if (form.artifactId !== artifactId) return false;
        body = policy.request(form, parent, charts, tree, inventory, images);
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
        let selected: NvkeInstallPlanImmutableChoice | null = null;
        try {
          selected = readSelectedInstallPlanImmutable(storage, binding);
        } catch {
          if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
          storage = null;
        }
        if (selected && selected.artifactId !== artifactId) throw new Error("RECOVERY_MISMATCH");
      }
      if (action === "prepare") {
        if (!storage || !body) throw new Error("INVALID_INPUT");
        hasJournal = !!readInstallPlanImmutableJournal(storage, binding, chosen);
        const digest = await nativeTextDigest(JSON.stringify(body));
        if (!current()) throw new Error("SOURCE_CHANGED");
        saveInstallPlanImmutableJournal(storage, binding, chosen, digest);
        hasJournal = true;
      } else if (action === "retry") {
        if (!storage || !readInstallPlanImmutableJournal(storage, binding, chosen))
          throw new Error("RECOVERY_MISMATCH");
        hasJournal = true;
      } else if (storage) {
        try {
          hasJournal = !!readInstallPlanImmutableJournal(storage, binding, chosen);
          selectInstallPlanImmutable(storage, binding, chosen);
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
        workloadId: binding.workloadId,
        expectedWorkloadVersion: binding.workloadVersion,
        workloadDigest: binding.workloadDigest,
        bindingId: binding.bindingId,
        expectedBindingVersion: binding.bindingVersion,
        bindingDigest: binding.bindingDigest,
      };
      contacted = true;
      const envelope = await policy.mutate(
        client,
        action,
        input,
        artifactId,
        body,
        binding.organizationId
      );
      if (!current()) throw new Error("SOURCE_CHANGED");
      if (envelope?.ok !== true || !Array.isArray(envelope.errors) || envelope.errors.length)
        throw new Error("UNCONFIRMED");
      const artifacts = policy.validate(
        envelope.data,
        [parent, charts, tree, inventory, images],
        artifactId,
        action === "prepare" ? reviewedForm : undefined
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
      args.workload?.pending ||
      args.imageBinding?.pending ||
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
          if (readSelectedInstallPlanImmutable(window.localStorage, binding) !== null)
            clearSelectedInstallPlanImmutable(window.localStorage, binding, visible.selection!);
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
    workload: ready ? inventory : null,
    binding: ready ? images : null,
    artifacts: visible?.artifacts ?? null,
    retainedArtifactId: visible?.selection?.artifactId ?? null,
    canReview: ready && !!visible,
    canRetry: ready && !!visible?.hasJournal,
    allowOriginalPreparation: visible?.allowOriginalPreparation ?? false,
    pending:
      args.pending ||
      source.pending ||
      args.workload?.pending ||
      args.imageBinding?.pending ||
      inFlight,
    loading: source.loading || (ready && !visible),
    error: source.error || visible?.error || null,
    onPrepare: (values: Form) => run("prepare", values.artifactId, values),
    onReview: (id: string) => run("review", id),
    onRetryPreparation: (id: string) => run("retry", id),
    onChooseArtifact:
      visible?.selection &&
      (!visible.hasJournal || !!visible.artifacts || visible.allowOriginalPreparation)
        ? onChooseArtifact
        : undefined,
  };
};
