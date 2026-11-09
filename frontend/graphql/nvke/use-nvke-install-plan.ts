"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type { NvkeOperatorInstallPlanPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallPlanPanel";
import type { NvkeOperatorJobPanelProps } from "@/components/screens/nvke/NvkeOperatorJobPanel";
import type {
  PrepareNvkeInstallPlanMutation,
  PrepareNvkeInstallPlanMutationVariables,
  ReviewNvkeInstallPlanMutation,
  ReviewNvkeInstallPlanMutationVariables,
  RetryNvkeInstallPlanMutation,
  GetNvkeInstallPlanItemPageQuery,
  GetNvkeInstallPlanWorkloadPageQuery,
} from "@/graphql/__generated__/operations";
import {
  installPlanFormSchema,
  installPlanSchema,
  installPlanMatches,
  validateInstallPlanPage,
  installPlanItemPageSchema,
  installPlanWorkloadPageSchema,
  type NvkeInstallPlan,
  type NvkeInstallPlanForm,
  type NvkeInstallPlanItem,
} from "@/lib/nvke/operator-install-plan";
import type { NvkeInstallWorkload } from "@/lib/nvke/operator-install-review";
import {
  readSelectedInstallPlan,
  selectInstallPlan,
  readInstallPlanJournal,
  saveInstallPlanJournal,
  clearSelectedInstallPlan,
  withInstallPlanLock,
  type NvkeInstallPlanJournalSource,
} from "@/lib/nvke/install-plan-recovery";
import {
  NvkeRecoveryError,
  nativeTextDigest,
  validNvkeGuid,
  type RecoveryStorage,
} from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import {
  PREPARE_NVKE_INSTALL_PLAN,
  REVIEW_NVKE_INSTALL_PLAN,
  RETRY_NVKE_INSTALL_PLAN,
} from "./nvke-install-plan.mutations";
import {
  GET_NVKE_INSTALL_PLAN_ITEM_PAGE,
  GET_NVKE_INSTALL_PLAN_WORKLOAD_PAGE,
} from "./nvke-install-plan.queries";

type State = {
  key: string;
  planId: string | null;
  plan: NvkeInstallPlan | null;
  pending: boolean;
  error: string | null;
  hasJournal: boolean;
  allowOriginalPreparation: boolean;
};
export const useNvkeInstallPlan = (args: {
  context: ReturnType<typeof useNvkeContext>;
  source: NvkeOperatorJobPanelProps | null;
  pending: boolean;
}): NvkeOperatorInstallPlanPanelProps | null => {
  const { context, source } = args,
    client = useApolloClient(),
    t = useTranslations("nvke");
  const eligible =
    source?.operation.operationKey === "tooling.management.install" &&
    source.operation.status === "UNCERTAIN";
  const authority = source?.authority;
  const ready =
    !!eligible &&
    !!source?.canManage &&
    !!context.scope &&
    !!context.scopeKey &&
    context.access.admin &&
    context.access.read &&
    !context.access.loading &&
    !context.loading &&
    !context.failed &&
    !!authority &&
    authority.siteId === source.site.id &&
    authority.siteVersion === source.site.version &&
    authority.isEnabled &&
    authority.deletedAt === null &&
    authority.hasCredentials &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    !!authority.verifiedAt &&
    Number.isFinite(Date.parse(authority.verifiedAt));
  const [selectionEpoch, setSelectionEpoch] = useState(0);
  const { key, isCurrent: current } = useNvkeEpoch(
    JSON.stringify([
      ready,
      context.scopeKey,
      context.scope,
      source?.scopeKey,
      source?.site,
      source?.operation.id,
      source?.operation.version,
      source?.operation.status,
      authority,
      selectionEpoch,
    ])
  );
  const sourceText =
    ready && context.scope && source && authority
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
        })
      : "";
  const binding = useMemo(
    () => (sourceText ? (JSON.parse(sourceText) as NvkeInstallPlanJournalSource) : null),
    [sourceText]
  );
  const [state, setState] = useState<State | null>(null);
  const busy = useRef(false);
  const visible = state?.key === key ? state : null;
  const plan = ready ? (visible?.plan ?? null) : null;
  useEffect(() => {
    if (!binding) return;
    let active = true;
    Promise.resolve().then(() => {
      let planId: string | null = null;
      try {
        planId = readSelectedInstallPlan(window.localStorage, binding, binding.operationId);
        const hasJournal =
          !!planId && !!readInstallPlanJournal(window.localStorage, binding, planId);
        if (active)
          setState({
            key,
            planId,
            plan: null,
            pending: false,
            error: null,
            hasJournal,
            allowOriginalPreparation: false,
          });
      } catch {
        if (active)
          setState({
            key,
            planId,
            plan: null,
            pending: false,
            error: t("storageRequired"),
            hasJournal: false,
            allowOriginalPreparation: false,
          });
      }
    });
    return () => {
      active = false;
    };
  }, [binding, key, t]);
  const input = {
    operationId: source?.operation.id,
    expectedActorId: context.scope?.actorId,
    expectedSiteVersion: source?.site.version,
    expectedAuthorityVersion: authority?.version,
    expectedOperationVersion: source?.operation.version,
    planId: plan?.id,
  };
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const validateItems = useCallback(
    (response: unknown, variables: Record<string, unknown>) => {
      const envelope = (response as GetNvkeInstallPlanItemPageQuery | undefined)
        ?.astroliftNvkeOperatorInstallPlanItemPage;
      try {
        if (
          !plan ||
          envelope?.ok !== true ||
          !Array.isArray(envelope.errors) ||
          envelope.errors.length
        )
          return false;
        validateInstallPlanPage(
          envelope.data,
          plan,
          "items",
          variables.limit as number,
          variables.search as string,
          !variables.after
        );
        return true;
      } catch {
        return false;
      }
    },
    [plan]
  );
  const validateWorkloads = useCallback(
    (response: unknown, variables: Record<string, unknown>) => {
      const envelope = (response as GetNvkeInstallPlanWorkloadPageQuery | undefined)
        ?.astroliftNvkeOperatorInstallPlanWorkloadPage;
      try {
        if (
          !plan ||
          envelope?.ok !== true ||
          !Array.isArray(envelope.errors) ||
          envelope.errors.length
        )
          return false;
        validateInstallPlanPage(
          envelope.data,
          plan,
          "workloads",
          variables.limit as number,
          variables.search as string,
          !variables.after
        );
        return true;
      } catch {
        return false;
      }
    },
    [plan]
  );
  const pageScope = plan ? JSON.stringify([key, plan.id, plan.version, plan.plan.planDigest]) : "";
  const itemTable = useScopedCursorTable<NvkeInstallPlanItem>(
    {
      query: GET_NVKE_INSTALL_PLAN_ITEM_PAGE,
      variables: { input, planDigest: plan?.plan.planDigest },
      skip: !plan,
      searchVariable: "search",
      emptySearchValue: "",
      extract: (response) => {
        const value = installPlanItemPageSchema.safeParse(
          (response as GetNvkeInstallPlanItemPageQuery | undefined)
            ?.astroliftNvkeOperatorInstallPlanItemPage?.data
        );
        return value.success ? value.data : undefined;
      },
    },
    pageScope,
    requestContext,
    validateItems
  );
  const workloadTable = useScopedCursorTable<NvkeInstallWorkload>(
    {
      query: GET_NVKE_INSTALL_PLAN_WORKLOAD_PAGE,
      variables: { input, planDigest: plan?.plan.planDigest },
      skip: !plan,
      searchVariable: "search",
      emptySearchValue: "",
      extract: (response) => {
        const value = installPlanWorkloadPageSchema.safeParse(
          (response as GetNvkeInstallPlanWorkloadPageQuery | undefined)
            ?.astroliftNvkeOperatorInstallPlanWorkloadPage?.data
        );
        return value.success ? value.data : undefined;
      },
    },
    pageScope,
    requestContext,
    validateWorkloads
  );

  const run = async (
    action: "prepare" | "review" | "retry",
    planId: string,
    values?: NvkeInstallPlanForm
  ) => {
    if (
      !ready ||
      !binding ||
      !visible ||
      !current() ||
      busy.current ||
      args.pending ||
      !validNvkeGuid(planId) ||
      planId === binding.operationId ||
      (visible.planId && visible.planId !== planId)
    )
      return false;
    const privateText = values?.requestText;
    busy.current = true;
    let hasJournal = visible.hasJournal;
    let contacted = false;
    setState({ ...visible, planId, plan: null, pending: true, error: null });
    const execute = async () => {
      if (!current()) throw new Error("SOURCE_CHANGED");
      let storage: RecoveryStorage | null = null;
      try {
        storage = window.localStorage;
      } catch {
        if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
      }
      if (storage) {
        // Another tab may have selected a plan while this tab awaited its lock.
        let selected: string | null = null;
        try {
          selected = readSelectedInstallPlan(storage, binding, binding.operationId);
        } catch {
          if (action !== "review") throw new Error("STORAGE_UNAVAILABLE");
          storage = null;
          hasJournal = false;
        }
        if (selected && selected !== planId) throw new Error("RECOVERY_MISMATCH");
      }
      if (action === "prepare") {
        if (
          !storage ||
          !values ||
          !installPlanFormSchema.safeParse({ planId, requestText: privateText }).success ||
          values.planId !== planId ||
          (visible.planId && !visible.allowOriginalPreparation)
        )
          throw new Error("INVALID_INPUT");
        const digest = await nativeTextDigest(privateText!);
        if (!current()) throw new Error("SOURCE_CHANGED");
        saveInstallPlanJournal(storage, binding, planId, digest);
        hasJournal = true;
      } else if (action === "retry") {
        if (!storage || !readInstallPlanJournal(storage, binding, planId))
          throw new Error("RECOVERY_MISMATCH");
        hasJournal = true;
      } else if (storage) {
        try {
          hasJournal = !!readInstallPlanJournal(storage, binding, planId);
          selectInstallPlan(storage, binding, binding.operationId, planId);
        } catch {
          hasJournal = false;
        }
      }
      if (!current()) throw new Error("SOURCE_CHANGED");
      const jobInput = {
        operationId: binding.operationId,
        expectedActorId: binding.actorId,
        expectedSiteVersion: binding.siteVersion,
        expectedAuthorityVersion: binding.authorityVersion,
        expectedOperationVersion: binding.operationVersion,
      };
      const reviewInput = { ...jobInput, planId };
      contacted = true;
      const response = await client.mutate<
        | PrepareNvkeInstallPlanMutation
        | ReviewNvkeInstallPlanMutation
        | RetryNvkeInstallPlanMutation,
        PrepareNvkeInstallPlanMutationVariables | ReviewNvkeInstallPlanMutationVariables
      >({
        mutation:
          action === "prepare"
            ? PREPARE_NVKE_INSTALL_PLAN
            : action === "review"
              ? REVIEW_NVKE_INSTALL_PLAN
              : RETRY_NVKE_INSTALL_PLAN,
        variables: {
          input: action === "prepare" ? { ...jobInput, requestText: privateText! } : reviewInput,
        },
        fetchPolicy: "no-cache",
        context: requestContext,
      });
      if (!current()) throw new Error("SOURCE_CHANGED");
      const data = response.data;
      const envelope =
        data &&
        ("prepareNvkeOperatorInstallPlan" in data
          ? data.prepareNvkeOperatorInstallPlan
          : "reviewNvkeOperatorInstallPlan" in data
            ? data.reviewNvkeOperatorInstallPlan
            : data.recoverNvkeOperatorInstallPlanPreparation);
      if (
        response.error ||
        envelope?.ok !== true ||
        !Array.isArray(envelope.errors) ||
        envelope.errors.length
      )
        throw new Error("UNCONFIRMED");
      const result = installPlanSchema.parse(envelope.data);
      if (result.id !== planId || !installPlanMatches(result, binding))
        throw new Error("SOURCE_CHANGED");
      setState({
        key,
        planId,
        plan: result,
        pending: false,
        error: null,
        hasJournal,
        allowOriginalPreparation: false,
      });
      return true;
    };
    try {
      // Read-only inspection remains available when browser locks are unavailable.
      return action === "review" && !navigator.locks?.request
        ? await execute()
        : await withInstallPlanLock(binding, binding.operationId, execute);
    } catch (error) {
      if (current())
        setState({
          key,
          planId,
          plan: null,
          pending: false,
          error:
            error instanceof NvkeRecoveryError && error.code === "RECOVERY_MISMATCH"
              ? t("recoveryMismatch")
              : t("requestFailed"),
          hasJournal,
          allowOriginalPreparation:
            (action === "review" && hasJournal) || (!contacted && visible.allowOriginalPreparation),
        });
      return false;
    } finally {
      busy.current = false;
    }
  };
  const onChoosePlan = async () => {
    if (
      !binding ||
      !visible ||
      !current() ||
      busy.current ||
      args.pending ||
      !visible.planId ||
      (visible.hasJournal && !visible.plan && !visible.allowOriginalPreparation)
    )
      return;
    busy.current = true;
    try {
      const choose = async () => {
        if (!current()) return;
        try {
          const selected = readSelectedInstallPlan(
            window.localStorage,
            binding,
            binding.operationId
          );
          if (selected !== null)
            clearSelectedInstallPlan(
              window.localStorage,
              binding,
              binding.operationId,
              visible.planId!
            );
        } catch (error) {
          if (visible.hasJournal) throw error;
        }
        setSelectionEpoch((value) => value + 1);
      };
      if (!navigator.locks?.request && !visible.hasJournal) await choose();
      else await withInstallPlanLock(binding, binding.operationId, choose);
    } catch {
      if (current()) setState({ ...visible, error: t("storageRequired") });
    } finally {
      busy.current = false;
    }
  };
  if (!eligible || !source) return null;
  return {
    scopeKey: key,
    site: source.site,
    operation: source.operation,
    authority: ready ? (authority ?? null) : null,
    plan,
    retainedPlanId: visible?.planId ?? null,
    itemTable,
    workloadTable,
    canReview: ready && !!visible,
    canRetry: ready && !!visible?.hasJournal,
    allowOriginalPreparation: visible?.allowOriginalPreparation ?? false,
    onChoosePlan:
      visible?.planId && (!visible.hasJournal || !!visible.plan || visible.allowOriginalPreparation)
        ? onChoosePlan
        : undefined,
    pending: args.pending || visible?.pending === true,
    loading: ready && !visible,
    error: visible?.error ?? null,
    onPrepare: (values) => run("prepare", values.planId, values),
    onReview: (id) => run("review", id),
    onRetryPreparation: (id) => run("retry", id),
  };
};
