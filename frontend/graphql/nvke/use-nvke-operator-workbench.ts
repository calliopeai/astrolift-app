"use client";

import { useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { NvkeOperatorWorkbenchProps } from "@/components/screens/nvke/NvkeOperatorWorkbench";
import type { NvkeOperatorAgentAuthority } from "@/components/screens/nvke/NvkeOperatorAgentPanel";
import type { NvkeSiteView } from "@/components/screens/nvke/types";
import type {
  GetNvkeOperatorContractQuery,
  GetNvkeRequestQuery,
  RequestNvkeOperatorJobMutation,
  RequestNvkeOperatorJobMutationVariables,
} from "@/graphql/__generated__/operations";
import { admitNvkeOperatorJob } from "@/lib/nvke/operator-admission";
import { recoverNvkeNative } from "@/lib/nvke/native-admission";
import { operatorContractSchema } from "@/lib/nvke/operator-contract";
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
import { GET_NVKE_OPERATOR_CONTRACT } from "./nvke-operator.queries";
import { REQUEST_NVKE_OPERATOR_JOB } from "./nvke-operator.mutations";

export const useNvkeOperatorWorkbench = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  authority: NvkeOperatorAgentAuthority | null;
  enabled: boolean;
  onRefresh: () => void;
  onJournalChange: () => void;
}): NvkeOperatorWorkbenchProps | null => {
  const { context, site, authority } = args,
    client = useApolloClient(),
    router = useRouter(),
    t = useTranslations("nvke");
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const eligible =
    args.enabled &&
    !!site &&
    !!args.scopeKey &&
    !!context.scope &&
    !context.loading &&
    !context.failed &&
    !context.access.loading &&
    context.access.read;
  const { key, isCurrent: current } = useNvkeEpoch(
    eligible
      ? JSON.stringify([
          args.scopeKey,
          context.scope,
          context.access.admin,
          context.access.loading,
          site.id,
          site.version,
          site.upstreamRevision,
          site.isActive,
          authority,
          refreshEpoch,
        ])
      : ""
  );
  const contractRead = useNvkeRead<GetNvkeOperatorContractQuery>(
    GET_NVKE_OPERATOR_CONTRACT,
    {},
    eligible ? key : "",
    0,
    context.scope?.organizationId
  );
  const parsed = operatorContractSchema.safeParse(contractRead.data?.astroliftNvkeOperatorContract);
  const contract =
    parsed.success && parsed.data.revision === site?.upstreamRevision ? parsed.data : null;
  const [journal, setJournal] = useState<{
    key: string;
    record: NvkeRecoveryRecord | null;
    failed: boolean;
  } | null>(null);
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    error: string | null;
  } | null>(null);
  const busy = useRef(false),
    blockedEpoch = useRef("");
  useEffect(() => {
    if (!eligible || !site || !context.scope) return;
    let active = true;
    const scope = { ...context.scope };
    void withRecoveryLock(scope, site.id, async () => {
      const record = readRecovery(window.localStorage, scope, site.id);
      if (active && current()) setJournal({ key, record, failed: false });
    }).catch(() => {
      if (active && current()) setJournal({ key, record: null, failed: true });
    });
    return () => {
      active = false;
    };
    // The epoch binds caller permissions, site, agent pins and explicit refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const record = journal?.key === key ? journal.record : null;
  const journalReady = journal?.key === key && !journal.failed;
  const reviewed =
    eligible &&
    context.access.admin &&
    !!contract &&
    !contractRead.loading &&
    !contractRead.failed &&
    site?.isActive === true &&
    validNvkeGuid(site.id) &&
    Number.isSafeInteger(site.version) &&
    site.version > 0 &&
    !!authority &&
    validNvkeGuid(authority.id) &&
    validNvkeGuid(authority.instanceId) &&
    /^[0-9a-f]{64}$/.test(authority.bundleManifestSha256) &&
    Number.isSafeInteger(authority.version) &&
    authority.version > 0 &&
    authority.siteId === site.id &&
    authority.siteVersion === site.version &&
    authority.isEnabled === true &&
    authority.hasCredentials === true &&
    authority.deletedAt === null &&
    typeof authority.verifiedAt === "string" &&
    Number.isFinite(Date.parse(authority.verifiedAt)) &&
    authority.verificationCode === "AGENT_VERIFIED";
  const sameReview =
    !record ||
    (record.format === 7 &&
      record.expectedSiteVersion === site?.version &&
      record.revision === site?.upstreamRevision &&
      record.authorityVersion === authority?.version);
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
      throw new Error("RECEIPT_UNAVAILABLE");
    return response.data.astroliftNvkeRequest;
  };
  const setRecord = (record: NvkeRecoveryRecord | null) => {
    if (current()) {
      setJournal({ key, record, failed: false });
      args.onJournalChange();
    }
  };
  const finish = async (run: () => Promise<{ id: string; siteId: string } | null>) => {
    busy.current = true;
    setMutation({ key, pending: true, error: null });
    try {
      const row = await run();
      if (!current()) return false;
      if (row && site && row.siteId === site.id && validNvkeGuid(row.id))
        router.push(`/nvke/${site.id}/operations/${row.id}`);
      setMutation({ key, pending: false, error: null });
      return !!row;
    } catch (error) {
      const message =
        error instanceof NvkeRecoveryError
          ? t(error.code === "RECOVERY_MISMATCH" ? "recoveryMismatch" : "storageRequired")
          : error instanceof NvkeAdmissionError && error.code === "REQUEST_REJECTED"
            ? t("requestRejected")
            : t("requestFailed");
      if (current()) {
        blockedEpoch.current = key;
        setMutation({ key, pending: false, error: message });
      }
      return false;
    } finally {
      busy.current = false;
    }
  };
  if (!eligible || !site) return null;
  return {
    scopeKey: key,
    site,
    authority,
    contract,
    loading: journal?.key !== key || contractRead.loading,
    pending: mutation?.key === key && mutation.pending,
    canManage: reviewed && journalReady && sameReview && !(mutation?.key === key && mutation.error),
    canRecover: journalReady && !!record && context.access.read,
    recovery:
      record?.format === 7
        ? {
            requestId: record.requestId,
            operationKey: record.operationKey,
            timeoutSeconds: record.timeoutSeconds,
            acknowledgeInfrastructureChange: record.acknowledgeInfrastructureChange,
            expectedSiteVersion: record.expectedSiteVersion,
            authorityVersion: record.authorityVersion,
          }
        : null,
    error:
      mutation?.key === key && mutation.error
        ? mutation.error
        : journal?.key === key && journal.failed
          ? t("storageRequired")
          : contractRead.failed || (!contractRead.loading && !contract)
            ? t("readFailed")
            : null,
    onRefresh: () => {
      if (!current() || busy.current) return;
      setMutation(null);
      setRefreshEpoch((n) => n + 1);
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
    onRequest: (values) => {
      if (
        !current() ||
        busy.current ||
        blockedEpoch.current === key ||
        !context.scope ||
        !journalReady ||
        !reviewed ||
        !sameReview ||
        !contract.commands.some((row) => `tooling.${row.key}` === values.operationKey)
      )
        return Promise.resolve(false);
      return finish(() =>
        admitNvkeOperatorJob({
          scope: context.scope!,
          siteId: site.id,
          expectedSiteVersion: site.version,
          expectedAuthorityVersion: authority!.version,
          revision: site.upstreamRevision,
          values,
          storage: window.localStorage,
          current,
          sourceAvailable: () => current() && reviewed,
          onRecord: setRecord,
          receipt,
          admit: async (input) => {
            if (!current() || !reviewed) throw new NvkeAdmissionError("SOURCE_CHANGED");
            const response = await client.mutate<
              RequestNvkeOperatorJobMutation,
              RequestNvkeOperatorJobMutationVariables
            >({
              mutation: REQUEST_NVKE_OPERATOR_JOB,
              variables: { input },
              fetchPolicy: "no-cache",
              context: requestContext,
            });
            if (response.error) throw new NvkeAdmissionError("REQUEST_FAILED");
            return response.data?.requestNvkeOperatorJob;
          },
        })
      );
    },
  };
};
