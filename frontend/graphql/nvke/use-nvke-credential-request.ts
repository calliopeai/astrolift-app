"use client";
import { useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { NvkeCredentialRequestPanelProps } from "@/components/screens/nvke/NvkeCredentialRequestPanel";
import type { NvkeMaintenancePanelProps } from "@/components/screens/nvke/NvkeMaintenancePanel";
import type { NvkeSiteView, NvkeOperationView } from "@/components/screens/nvke/types";
import type {
  GetNvkeMetricsAuthorityQuery,
  GetNvkeRequestQuery,
  RequestNvkeCredentialObservationMutation,
  RequestNvkeCredentialObservationMutationVariables,
} from "@/graphql/__generated__/operations";
import { admitNvkeCredentials } from "@/lib/nvke/credential-admission";
import { recoverNvkeNative } from "@/lib/nvke/native-admission";
import { NvkeAdmissionError } from "@/lib/nvke/admission";
import {
  readRecovery,
  withRecoveryLock,
  validNvkeGuid,
  NvkeRecoveryError,
  type NvkeRecoveryRecord,
} from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import { GET_NVKE_REQUEST } from "./nvke.queries";
import { GET_NVKE_METRICS_AUTHORITY } from "./nvke-metrics.queries";
import { REQUEST_NVKE_CREDENTIAL_OBSERVATION } from "./nvke-metrics.mutations";
import { metricsAuthoritySchema } from "@/components/screens/nvke/schema";

export const useNvkeCredentialRequest = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  operation: NvkeOperationView | null;
  maintenance: NvkeMaintenancePanelProps | null;
  enabled: boolean;
  onRefresh: () => void;
}): NvkeCredentialRequestPanelProps | null => {
  const { context, site, operation, maintenance } = args;
  const client = useApolloClient(),
    router = useRouter(),
    t = useTranslations("nvke");
  const [epoch, setEpoch] = useState(0);
  const eligible =
    args.enabled &&
    !!context.scope &&
    !!args.scopeKey &&
    !context.loading &&
    !context.failed &&
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
        epoch,
      ])
    : "";
  const read = useNvkeRead<GetNvkeMetricsAuthorityQuery>(
    GET_NVKE_METRICS_AUTHORITY,
    { siteId: site?.id },
    base,
    0,
    context.scope?.organizationId
  );
  const raw = read.data?.astroliftNvkeMetricsAuthority;
  const authority =
    raw &&
    site &&
    raw.siteId === site.id &&
    raw.siteVersion === site.version &&
    validNvkeGuid(raw.id) &&
    Number.isSafeInteger(raw.version) &&
    raw.version > 0 &&
    typeof raw.isEnabled === "boolean" &&
    typeof raw.hasCredentials === "boolean" &&
    metricsAuthoritySchema.safeParse({
      ...raw,
      credentialMode: "preserve",
      token: "",
      caPem: "",
      clientCertificatePem: "",
      clientKeyPem: "",
    }).success &&
    raw.deletedAt === null
      ? raw
      : null;
  const present = !!read.data && Object.hasOwn(read.data, "astroliftNvkeMetricsAuthority");
  const authorityFailed =
    read.failed || (!read.loading && (!present || (raw !== null && !authority)));
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
  const { key: visitKey, isCurrent: current } = useNvkeEpoch(base);
  const [journal, setJournal] = useState<{
    key: string;
    record: NvkeRecoveryRecord | null;
    failed: boolean;
  } | null>(null);
  const [journalEpoch, setJournalEpoch] = useState(0);
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
    // The visit binds the complete caller, source and authority review.
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
    !read.loading;
  const sameReview =
    !record ||
    (record.format === 5 &&
      record.expectedSiteVersion === site?.version &&
      record.authorityVersion === authority?.version &&
      record.source.operationId === operation?.id &&
      record.source.version === operation?.version &&
      record.revision === site?.upstreamRevision);
  const reviewed = metadataReady && context.access.admin && !context.access.loading;
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
  const finish = async (run: () => Promise<{ id: string; siteId: string } | null>) => {
    busy.current = true;
    setMutation({ key: visitKey, pending: true, error: null });
    try {
      const row = await run();
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
  };
  if (!eligible || !site || !operation) return null;
  return {
    scopeKey: visitKey,
    site,
    source: { operationId: operation.id, version: operation.version },
    clusterId: matched ? source.clusterId : null,
    authority: authority ? { version: authority.version, endpoint: authority.endpoint } : null,
    loading: read.loading || !!maintenance?.loading,
    pending: mutation?.key === visitKey && mutation.pending,
    canManage: reviewed && journalReady && sameReview,
    canRecover: journalReady && !!record && context.access.read && !context.access.loading,
    recovery: record
      ? {
          requestId: record.requestId,
          operationKey: record.operationKey,
          timeoutSeconds: record.timeoutSeconds,
        }
      : null,
    error:
      mutation?.key === visitKey && mutation.error
        ? mutation.error
        : journal?.key === visitKey && journal.failed
          ? t("storageRequired")
          : authorityFailed || maintenance?.error
            ? t("readFailed")
            : null,
    onRefresh: () => {
      if (busy.current) return;
      setMutation(null);
      setEpoch((value) => value + 1);
      setJournalEpoch((value) => value + 1);
      read.refresh();
      args.onRefresh();
    },
    onRecover: () => {
      if (
        !current() ||
        busy.current ||
        !context.scope ||
        !journalReady ||
        !context.access.read ||
        context.access.loading
      )
        return Promise.resolve(false);
      return finish(() =>
        recoverNvkeNative({
          scope: context.scope!,
          siteId: site.id,
          storage: window.localStorage,
          current,
          receipt,
          onRecord: setRecord,
        })
      );
    },
    onRequest: (timeoutSeconds) => {
      if (
        !current() ||
        busy.current ||
        !context.scope ||
        !journalReady ||
        !context.access.read ||
        context.access.loading ||
        (!record && !reviewed)
      )
        return Promise.resolve(false);
      return finish(() =>
        admitNvkeCredentials({
          scope: context.scope!,
          siteId: site.id,
          expectedSiteVersion: site.version,
          authorityVersion: authority?.version ?? 0,
          source: { operationId: operation.id, version: operation.version, resourceKey: null },
          revision: site.upstreamRevision,
          timeoutSeconds,
          storage: window.localStorage,
          current,
          sourceAvailable: () => current() && reviewed,
          onRecord: setRecord,
          receipt,
          admit: async (input) => {
            if (!current() || !reviewed) throw new NvkeAdmissionError("SOURCE_CHANGED");
            return (
              await client.mutate<
                RequestNvkeCredentialObservationMutation,
                RequestNvkeCredentialObservationMutationVariables
              >({
                mutation: REQUEST_NVKE_CREDENTIAL_OBSERVATION,
                variables: { input },
                fetchPolicy: "no-cache",
                context: requestContext,
              })
            ).data?.requestNvkeCredentialObservation;
          },
        })
      );
    },
  };
};
