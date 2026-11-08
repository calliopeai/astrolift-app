"use client";

import { useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { NvkeProviderRequestPanelProps } from "@/components/screens/nvke/NvkeProviderRequestPanel";
import type { NvkeSiteView } from "@/components/screens/nvke/types";
import { connectionSchema } from "@/components/screens/nvke/schema";
import type {
  GetNvkeRequestQuery,
  RequestNvkeProviderObservationMutation,
  RequestNvkeProviderObservationMutationVariables,
} from "@/graphql/__generated__/operations";
import { admitNvkeProviders } from "@/lib/nvke/provider-admission";
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
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import { GET_NVKE_REQUEST } from "./nvke.queries";
import { REQUEST_NVKE_PROVIDER_OBSERVATION } from "./nvke-providers.mutations";

export const useNvkeProviderRequest = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  contractRevision: string | null;
  enabled: boolean;
  onRefresh: () => void;
  onJournalChange: () => void;
}): NvkeProviderRequestPanelProps | null => {
  const { context, site } = args,
    client = useApolloClient(),
    router = useRouter(),
    t = useTranslations("nvke");
  const eligible =
    args.enabled &&
    !!context.scope &&
    !!args.scopeKey &&
    !context.loading &&
    !context.failed &&
    !context.access.loading &&
    context.access.read &&
    !!site;
  const connection = site?.connections.find((row) => row.surface === "DATASTORE") ?? null;
  const { key: visitKey, isCurrent: current } = useNvkeEpoch(
    eligible
      ? JSON.stringify([
          args.scopeKey,
          site.id,
          site.version,
          site.upstreamRevision,
          connection,
          args.contractRevision,
          context.access.admin,
        ])
      : ""
  );
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
  useEffect(() => {
    if (!eligible || !site || !context.scope) return;
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
    // The visit binds the complete caller and reviewed site/connection metadata.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visitKey, journalEpoch]);
  const record = journal?.key === visitKey ? journal.record : null;
  const journalReady = journal?.key === visitKey && !journal.failed;
  const connectionValid =
    !!connection &&
    validNvkeGuid(connection.id) &&
    Number.isSafeInteger(connection.version) &&
    connection.version > 0 &&
    connection.isEnabled === true &&
    connectionSchema.safeParse({
      ...connection,
      credentialMode: "preserve",
      token: "",
      caPem: "",
      clientCertificatePem: "",
      clientKeyPem: "",
    }).success;
  const reviewed =
    eligible &&
    site?.isActive === true &&
    Number.isSafeInteger(site.version) &&
    site.version > 0 &&
    validNvkeGuid(site.id) &&
    /^[0-9a-f]{40}$/.test(site.upstreamRevision) &&
    site.upstreamRevision === args.contractRevision &&
    connectionValid &&
    context.access.admin;
  const sameReview =
    !record ||
    (record.format === 6 &&
      record.expectedSiteVersion === site?.version &&
      record.revision === site?.upstreamRevision &&
      record.source.connectionId === connection?.id &&
      record.source.connectionVersion === connection?.version);
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
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
  const setRecord = (record: NvkeRecoveryRecord | null) => {
    if (current()) {
      setJournal({ key: visitKey, record, failed: false });
      args.onJournalChange();
    }
  };
  const finish = async (run: () => Promise<{ id: string; siteId: string } | null>) => {
    busy.current = true;
    setMutation({ key: visitKey, pending: true, error: null });
    try {
      const row = await run();
      if (!current()) return false;
      if (row && site && row.siteId === site.id && validNvkeGuid(row.id))
        router.push(`/nvke/${site.id}/operations/${row.id}`);
      setMutation({ key: visitKey, pending: false, error: null });
      return !!row;
    } catch (error) {
      const message =
        error instanceof NvkeRecoveryError
          ? t(error.code === "RECOVERY_MISMATCH" ? "recoveryMismatch" : "storageRequired")
          : error instanceof NvkeAdmissionError && error.code === "REQUEST_REJECTED"
            ? t("requestRejected")
            : t("requestFailed");
      if (current()) setMutation({ key: visitKey, pending: false, error: message });
      return false;
    } finally {
      busy.current = false;
    }
  };
  if (!eligible || !site) return null;
  return {
    scopeKey: visitKey,
    site,
    connection: connectionValid
      ? { id: connection!.id, version: connection!.version, endpoint: connection!.endpoint }
      : null,
    loading: journal?.key !== visitKey,
    pending: mutation?.key === visitKey && mutation.pending,
    canManage: reviewed && journalReady && sameReview,
    canRecover: journalReady && !!record && context.access.read,
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
          : null,
    onRefresh: () => {
      if (busy.current) return;
      setMutation(null);
      setJournalEpoch((value) => value + 1);
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
      if (!current() || busy.current || !context.scope || !journalReady || !reviewed || !sameReview)
        return Promise.resolve(false);
      return finish(() =>
        admitNvkeProviders({
          scope: context.scope!,
          siteId: site.id,
          expectedSiteVersion: site.version,
          source: { connectionId: connection!.id, connectionVersion: connection!.version },
          revision: site.upstreamRevision,
          timeoutSeconds,
          storage: window.localStorage,
          current,
          sourceAvailable: () => current() && reviewed,
          onRecord: setRecord,
          receipt,
          admit: async (input) => {
            if (!current() || !reviewed) throw new NvkeAdmissionError("SOURCE_CHANGED");
            const response = await client.mutate<
              RequestNvkeProviderObservationMutation,
              RequestNvkeProviderObservationMutationVariables
            >({
              mutation: REQUEST_NVKE_PROVIDER_OBSERVATION,
              variables: { input },
              fetchPolicy: "no-cache",
              context: requestContext,
            });
            if (response.error) throw new NvkeAdmissionError("REQUEST_FAILED");
            return response.data?.requestNvkeProviderObservation;
          },
        })
      );
    },
  };
};
