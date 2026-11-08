"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { NvkeWorkflowPanelProps } from "@/components/screens/nvke/NvkeWorkflowPanel";
import type { NvkeMaintenancePanelProps } from "@/components/screens/nvke/NvkeMaintenancePanel";
import type { NvkeSiteView, NvkeOperationView } from "@/components/screens/nvke/types";
import type {
  GetNvkeContractQuery,
  GetNvkeTemporalQuery,
  GetNvkeNativeHistoryQuery,
  GetNvkeNativeHistoryQueryVariables,
  GetNvkeRequestQuery,
  RequestNvkeNativeWorkflowMutation,
  RequestNvkeNativeWorkflowMutationVariables,
  RequestNvkeNativeResetMutation,
  RequestNvkeNativeResetMutationVariables,
} from "@/graphql/__generated__/operations";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import {
  admitNvkeNative,
  recoverNvkeNative,
  nativeMutationInput,
  type NvkeNativeValues,
} from "@/lib/nvke/native-admission";
import {
  readRecovery,
  withRecoveryLock,
  NvkeRecoveryError,
  validNvkeGuid,
  type NvkeRecoveryRecord,
} from "@/lib/nvke/recovery";
import { NvkeAdmissionError } from "@/lib/nvke/admission";
import { reviewedNativeHistory, type NvkeNativeHistoryEvent } from "@/lib/nvke/native-history";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import { GET_NVKE_TEMPORAL, GET_NVKE_NATIVE_HISTORY, GET_NVKE_REQUEST } from "./nvke.queries";
import { REQUEST_NVKE_NATIVE_WORKFLOW, REQUEST_NVKE_NATIVE_RESET } from "./nvke.mutations";
const names = ["RenewK8sCertsV2", "RotateEtcdEncryptionKey", "RetireLostEtcdRotationNode"] as const;
type Inspectable = "RenewK8sCertsV2" | "RotateEtcdEncryptionKey";

export const useNvkeWorkflows = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  operation: NvkeOperationView | null;
  contract: GetNvkeContractQuery["astroliftNvkeContract"] | undefined;
  maintenance: NvkeMaintenancePanelProps | null;
  enabled: boolean;
  onRefresh: () => void;
}): NvkeWorkflowPanelProps | null => {
  const { context, site, operation, contract, maintenance } = args;
  const client = useApolloClient(),
    router = useRouter(),
    t = useTranslations("nvke");
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const eligible =
    args.enabled &&
    !!context.scope &&
    !!args.scopeKey &&
    !context.failed &&
    !context.loading &&
    context.access.read &&
    !!site &&
    operation?.siteId === site.id &&
    operation.operationKey === "getCluster" &&
    operation.status === "SUCCEEDED" &&
    !operation.isInventory;
  const base = eligible
    ? JSON.stringify([
        args.scopeKey,
        site.id,
        site.version,
        operation.id,
        operation.version,
        context.access.admin,
        context.access.loading,
        refreshEpoch,
      ])
    : "";
  const authorityRead = useNvkeRead<GetNvkeTemporalQuery>(
    GET_NVKE_TEMPORAL,
    { siteId: site?.id },
    base,
    0,
    context.scope?.organizationId
  );
  const raw = authorityRead.data?.astroliftNvkeTemporalAuthority;
  const authority =
    raw &&
    site &&
    raw.siteId === site.id &&
    raw.siteVersion === site.version &&
    validNvkeGuid(raw.id) &&
    Number.isSafeInteger(raw.version) &&
    raw.version > 0 &&
    raw.deletedAt === null
      ? { ...raw, verifiedAt: raw.verifiedAt ?? null, deletedAt: raw.deletedAt ?? null }
      : null;
  const authorityPresent =
    !!authorityRead.data && Object.hasOwn(authorityRead.data, "astroliftNvkeTemporalAuthority");
  const authorityFailed =
    authorityRead.failed ||
    (!authorityRead.loading && (!authorityPresent || (raw !== null && !authority)));
  const source = maintenance?.observation;
  const matched =
    !!source &&
    !!site &&
    !!operation &&
    source.siteId === site.id &&
    source.operationId === operation.id &&
    source.operationVersion === operation.version &&
    validNvkeGuid(source.clusterId);
  const sourceCurrent =
    matched &&
    source.sourceSiteVersion === site!.version &&
    !maintenance?.loading &&
    !maintenance?.error;
  const workflows: NvkeWorkflowPanelProps["workflows"] = [];
  let catalogValid =
    !!contract &&
    !!site &&
    contract.revision === site.upstreamRevision &&
    contract.nativeWorkflows.length === names.length;
  for (const name of names) {
    const rows = contract?.nativeWorkflows.filter((item) => item.name === name) ?? [];
    const row = rows[0];
    try {
      if (
        rows.length !== 1 ||
        row.permission !== "nvke.admin" ||
        typeof row.inputSchemaText !== "string" ||
        row.inputSchemaText.length > 1048576 ||
        JSON.parse(row.inputSchemaText)?.type !== "object"
      )
        throw Error("CATALOG");
      workflows.push({ name, schemaText: row.inputSchemaText });
    } catch {
      catalogValid = false;
    }
  }
  const owners: Partial<Record<Inspectable, string>> = {};
  if (matched && source.renewal?.workflowId) owners.RenewK8sCertsV2 = source.renewal.workflowId;
  if (matched && source.etcdKeyRotation?.workflowId)
    owners.RotateEtcdEncryptionKey = source.etcdKeyRotation.workflowId;
  const { key: visitKey, isCurrent: current } = useNvkeEpoch(
    base
      ? JSON.stringify([
          base,
          authority?.id,
          authority?.version,
          authority?.isEnabled,
          source?.sourceSiteVersion,
          source?.clusterId,
          owners,
          contract?.revision,
        ])
      : ""
  );
  const [journal, setJournal] = useState<{
    key: string;
    record: NvkeRecoveryRecord | null;
    failed: boolean;
  } | null>(null);
  const [journalEpoch, setJournalEpoch] = useState(0);
  const [choice, setChoice] = useState<{ key: string; workflow: Inspectable } | null>(null);
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    error: string | null;
  } | null>(null);
  const busy = useRef(false);
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  useEffect(() => {
    if (!base || !site || !context.scope) return;
    let active = true;
    const scope = { ...context.scope };
    void withRecoveryLock(scope, site.id, async () => {
      const record = readRecovery(window.localStorage, scope, site.id);
      if (active && current()) setJournal({ key: visitKey, record, failed: false });
    }).catch(() => {
      if (active && current()) setJournal({ key: visitKey, record: null, failed: true });
    });
    return () => {
      active = false;
    };
    // visitKey binds the exact caller/site/source/authority visit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visitKey, journalEpoch]);
  const record = journal?.key === visitKey ? journal.record : null;
  const journalReady = journal?.key === visitKey && !journal.failed;
  const metadataReady =
    eligible &&
    !!site?.isActive &&
    sourceCurrent &&
    !!authority?.isEnabled &&
    !authorityFailed &&
    !authorityRead.loading &&
    catalogValid;
  const canInspect = metadataReady && context.access.admin && !context.access.loading;
  const selected = choice?.key === visitKey ? choice.workflow : null;
  const owner = selected ? owners[selected] : undefined;
  const variables = {
    siteId: site?.id ?? "",
    expectedSiteVersion: site?.version ?? 0,
    expectedActorId: context.scope?.actorId ?? 0,
    expectedAuthorityVersion: authority?.version ?? 0,
    sourceOperationId: operation?.id ?? "",
    expectedSourceVersion: operation?.version ?? 0,
    workflow: selected ?? "RotateEtcdEncryptionKey",
  };
  const historyScope =
    canInspect && selected && owner ? JSON.stringify([visitKey, selected, owner]) : "";
  const summary = useNvkeRead<GetNvkeNativeHistoryQuery>(
    GET_NVKE_NATIVE_HISTORY,
    { ...variables, limit: 1 },
    historyScope,
    0,
    context.scope?.organizationId
  );
  const history =
    historyScope && source && owner
      ? reviewedNativeHistory(summary.data?.astroliftNvkeNativeHistory, {
          ...variables,
          limit: 1,
          clusterId: source.clusterId,
          workflowId: owner,
        })
      : null;
  const validatePage = useCallback(
    (data: unknown, requested: Record<string, unknown>) => {
      if (!history) return false;
      return !!reviewedNativeHistory(
        (data as GetNvkeNativeHistoryQuery | undefined)?.astroliftNvkeNativeHistory,
        {
          ...(requested as GetNvkeNativeHistoryQueryVariables),
          clusterId: history.clusterId,
          workflowId: history.workflowId,
          runId: history.runId,
        }
      );
    },
    [history]
  );
  const tableScope = history
    ? JSON.stringify([historyScope, history.workflowId, history.runId])
    : "";
  const table = useScopedCursorTable<NvkeNativeHistoryEvent>(
    {
      query: GET_NVKE_NATIVE_HISTORY,
      variables,
      extract: (data) =>
        (data as GetNvkeNativeHistoryQuery | undefined)?.astroliftNvkeNativeHistory.data,
      skip: !tableScope,
      urlKey: "nvke-native-history",
    },
    tableScope,
    requestContext,
    validatePage
  );
  const pending = mutation?.key === visitKey && mutation.pending;
  const historyFailed =
    !!historyScope && (summary.failed || (!summary.loading && !history) || table.state === "error");
  const error = !eligible
    ? null
    : mutation?.key === visitKey && mutation.error
      ? mutation.error
      : journal?.key === visitKey && journal.failed
        ? t("storageRequired")
        : authorityFailed || maintenance?.error
          ? t("readFailed")
          : !sourceCurrent && !maintenance?.loading
            ? t("workflow.sourceUnavailable")
            : null;
  const receipt = async (requestId: string) => {
    const response = await client.query<GetNvkeRequestQuery>({
      query: GET_NVKE_REQUEST,
      variables: { siteId: site!.id, requestId },
      fetchPolicy: "no-cache",
      context: requestContext,
    });
    if (
      response.error ||
      !response.data ||
      !Object.hasOwn(response.data, "astroliftNvkeRequest") ||
      response.data.astroliftNvkeRequest === undefined
    )
      throw Error("RECEIPT_UNAVAILABLE");
    return response.data.astroliftNvkeRequest;
  };
  const report = (error: unknown) =>
    error instanceof NvkeRecoveryError
      ? t(error.code === "RECOVERY_MISMATCH" ? "recoveryMismatch" : "storageRequired")
      : error instanceof NvkeAdmissionError && error.code === "REQUEST_REJECTED"
        ? t("requestRejected")
        : t("requestFailed");
  const setRecord = (record: NvkeRecoveryRecord | null) => {
    if (current()) setJournal({ key: visitKey, record, failed: false });
  };
  const navigate = (row: { id: string; siteId: string }) => {
    if (current() && site && row.siteId === site.id && validNvkeGuid(row.id))
      router.push(`/nvke/${site.id}/operations/${row.id}`);
  };
  const retirementEligible =
    metadataReady &&
    history?.workflow === "RotateEtcdEncryptionKey" &&
    history.workflowId === owners.RotateEtcdEncryptionKey &&
    history.retainedMutationLock === true &&
    source?.etcdKeyRotation?.state === "failed" &&
    ["add_secondary", "promote_primary", "rewrite", "remove_old_keys"].includes(
      source.etcdKeyRotation.phase ?? ""
    ) &&
    ["FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"].includes(history.status) &&
    !historyFailed;
  if (!eligible || !site || !operation) return null;
  return {
    scopeKey: visitKey,
    site,
    authority,
    source: { operationId: operation.id, version: operation.version },
    clusterId: matched ? source.clusterId : "—",
    owners,
    workflows: catalogValid ? workflows : [],
    canManage: metadataReady && journalReady && context.access.admin && !context.access.loading,
    canRecover: !!context.scope && context.access.read && journalReady && !!record,
    canInspect,
    retirementEligible: !!retirementEligible,
    loading: authorityRead.loading || !!maintenance?.loading,
    pending: !!pending,
    error: error || (!catalogValid ? t("readFailed") : null),
    recovery: record ? { requestId: record.requestId, operationKey: record.operationKey } : null,
    history: history ?? null,
    historyTable:
      table.state === "error"
        ? { ...table, rows: [], totalCount: null, nextCursor: null, isStale: false }
        : table,
    historyError: historyFailed ? t("readFailed") : null,
    onHistory: (workflow) => {
      if (current() && canInspect && owners[workflow] && !busy.current)
        setChoice({ key: visitKey, workflow });
    },
    onRefresh: () => {
      if (busy.current) return;
      setMutation(null);
      setChoice(null);
      setRefreshEpoch((value) => value + 1);
      setJournalEpoch((value) => value + 1);
      authorityRead.refresh();
      summary.refresh();
      table.refetch();
      args.onRefresh();
    },
    onRecover: async () => {
      if (!current() || busy.current || !context.scope || !journalReady || !context.access.read)
        return false;
      busy.current = true;
      setMutation({ key: visitKey, pending: true, error: null });
      try {
        const row = await recoverNvkeNative({
          scope: context.scope,
          siteId: site.id,
          storage: window.localStorage,
          current,
          receipt,
          onRecord: setRecord,
        });
        if (!current()) return false;
        if (row) navigate(row);
        setMutation({ key: visitKey, pending: false, error: null });
        return !!row;
      } catch (error) {
        if (current()) setMutation({ key: visitKey, pending: false, error: report(error) });
        return false;
      } finally {
        busy.current = false;
      }
    },
    onRequest: async (values: NvkeNativeValues) => {
      if (
        !current() ||
        busy.current ||
        !context.scope ||
        !journalReady ||
        !context.access.read ||
        context.access.loading
      )
        return false;
      const resetReady =
        !!history &&
        history.workflow === "RotateEtcdEncryptionKey" &&
        !historyFailed &&
        !table.isStale &&
        table.state === "ready" &&
        history.retainedMutationLock === true &&
        ["failed", "running"].includes(history.recordedState ?? "") &&
        ["FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"].includes(history.status);
      const reviewed =
        metadataReady &&
        context.access.admin &&
        !(
          values.mode === "workflow" &&
          values.workflow === "RetireLostEtcdRotationNode" &&
          !retirementEligible
        ) &&
        !(
          values.mode === "reset" &&
          (!resetReady ||
            values.review.workflowId !== history?.workflowId ||
            values.review.baseRunId !== history?.runId ||
            values.review.phase !== history?.recordedPhase)
        );
      if (!record && !reviewed) return false;
      busy.current = true;
      setMutation({ key: visitKey, pending: true, error: null });
      try {
        const row = await admitNvkeNative({
          scope: context.scope,
          siteId: site.id,
          expectedSiteVersion: site.version,
          authorityVersion: authority?.version ?? 0,
          source: { operationId: operation.id, version: operation.version, resourceKey: null },
          revision: contract?.revision ?? site.upstreamRevision,
          values,
          storage: window.localStorage,
          current,
          sourceAvailable: () => current() && metadataReady,
          onRecord: setRecord,
          receipt,
          admit: async (input) => {
            if (!current() || !reviewed) throw new NvkeAdmissionError("SOURCE_CHANGED");
            const mapped = nativeMutationInput(input);
            if (mapped.kind === "workflow")
              return (
                await client.mutate<
                  RequestNvkeNativeWorkflowMutation,
                  RequestNvkeNativeWorkflowMutationVariables
                >({
                  mutation: REQUEST_NVKE_NATIVE_WORKFLOW,
                  variables: { input: mapped.input },
                  fetchPolicy: "no-cache",
                  context: requestContext,
                })
              ).data?.requestNvkeNativeWorkflow;
            return (
              await client.mutate<
                RequestNvkeNativeResetMutation,
                RequestNvkeNativeResetMutationVariables
              >({
                mutation: REQUEST_NVKE_NATIVE_RESET,
                variables: { input: mapped.input },
                fetchPolicy: "no-cache",
                context: requestContext,
              })
            ).data?.requestNvkeNativeReset;
          },
        });
        if (!current()) return false;
        if (row) navigate(row);
        setMutation({ key: visitKey, pending: false, error: null });
        return !!row;
      } catch (error) {
        if (current()) setMutation({ key: visitKey, pending: false, error: report(error) });
        return false;
      } finally {
        busy.current = false;
      }
    },
  };
};
