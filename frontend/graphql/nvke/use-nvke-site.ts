"use client";

import { useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { DocumentNode } from "@apollo/client";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import {
  mayExecute,
  type NvkeOperationView,
  type NvkeSiteScreenProps,
} from "@/components/screens/nvke/types";
import type {
  ConfigureNvkeConnectionInput,
  GetNvkeSiteQuery,
  GetNvkeContractQuery,
  GetNvkeRequestQuery,
  ListNvkeOperationsQuery,
  RequestNvkeOperationTextMutation,
  RequestNvkeOperationTextMutationVariables,
  SetNvkeSiteEnabledInput,
  RemoveNvkeSiteInput,
} from "@/graphql/__generated__/operations";
import { nativeSchemaText, nativeTemplateText } from "@/lib/nvke/contract";
import {
  archiveRecovery,
  archiveRejectedRecovery,
  assertOriginalRequest,
  nativeTextDigest,
  NvkeRecoveryError,
  readRecovery,
  saveRecovery,
  validNvkeGuid,
  withRecoveryLock,
  type NvkeRecoveryRecord,
} from "@/lib/nvke/recovery";
import { useNvkeContext, useNvkeEpoch } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import {
  GET_NVKE_SITE,
  GET_NVKE_CONTRACT,
  GET_NVKE_REQUEST,
  LIST_NVKE_OPERATIONS,
} from "./nvke.queries";
import {
  CONFIGURE_NVKE_CONNECTION,
  REMOVE_NVKE_SITE,
  REQUEST_NVKE_OPERATION_TEXT,
  SET_NVKE_SITE_ENABLED,
} from "./nvke.mutations";

export const useNvkeSite = (siteId: string): NvkeSiteScreenProps & { scopeKey: string } => {
  const context = useNvkeContext(),
    client = useApolloClient(),
    router = useRouter();
  const t = useTranslations("nvke");
  const key =
    context.scopeKey && validNvkeGuid(siteId) && context.access.read
      ? JSON.stringify([context.scopeKey, siteId])
      : "";
  const { isCurrent: current, key: visitKey } = useNvkeEpoch(key);
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const siteQuery = useNvkeRead<GetNvkeSiteQuery>(
    GET_NVKE_SITE,
    { siteId },
    key,
    0,
    context.scope?.organizationId
  );
  const catalog = useNvkeRead<GetNvkeContractQuery>(
    GET_NVKE_CONTRACT,
    {},
    key,
    0,
    context.scope?.organizationId
  );
  const site =
    siteQuery.data?.astroliftNvkeSite?.id === siteId ? siteQuery.data.astroliftNvkeSite : null;
  const contract = catalog.data?.astroliftNvkeContract;
  const operations = useScopedCursorTable<NvkeOperationView>(
    {
      query: LIST_NVKE_OPERATIONS,
      variables: { siteId },
      extract: (data) => (data as ListNvkeOperationsQuery | undefined)?.astroliftNvkeOperations,
      skip: !key,
      urlKey: "nvke-operations",
    },
    key,
    requestContext
  );
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
  const [journalEpoch, setJournalEpoch] = useState(0);
  const busy = useRef(false);
  useEffect(() => {
    if (!key || !context.scope) return;
    let active = true;
    void withRecoveryLock(context.scope, siteId, async () => {
      const record = readRecovery(window.localStorage, context.scope!, siteId);
      if (active) setJournal({ key, record, failed: false });
    }).catch(() => {
      if (active) setJournal({ key, record: null, failed: true });
    });
    return () => {
      active = false;
    };
    // key includes the complete caller and site identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, journalEpoch]);
  const recovery = journal?.key === key ? journal.record : null;
  const pending = mutation?.key === visitKey && mutation.pending;
  const loadFailed =
    context.failed || siteQuery.failed || catalog.failed || (!context.loading && !key);
  const error = loadFailed
    ? t("readFailed")
    : journal?.key === key && journal.failed
      ? t("storageRequired")
      : mutation?.key === visitKey
        ? mutation.error
        : null;
  const refresh = () => {
    setMutation(null);
    setJournalEpoch((value) => value + 1);
    siteQuery.refresh();
    catalog.refresh();
    operations.refetch();
  };
  const runConfiguration = async (
    document: DocumentNode,
    input: object,
    field: string,
    removed = false
  ) => {
    if (
      !current() ||
      !key ||
      !site ||
      siteQuery.loading ||
      siteQuery.failed ||
      !context.access.admin ||
      busy.current
    )
      return false;
    busy.current = true;
    setMutation({ key: visitKey, pending: true, error: null });
    try {
      const response = await client.mutate<
        Record<string, { ok: boolean; data?: { id: string; version: number } | null }>
      >({
        mutation: document,
        variables: { input },
        fetchPolicy: "no-cache",
        context: requestContext,
      });
      if (!current()) return false;
      const reply = response.data?.[field];
      if (!reply?.ok || reply.data?.id !== siteId || reply.data.version <= site.version)
        throw new Error("CONFIGURATION_FAILED");
      setMutation({ key: visitKey, pending: false, error: null });
      if (removed) router.push("/nvke");
      else refresh();
      return true;
    } catch {
      if (current())
        setMutation({ key: visitKey, pending: false, error: t("configurationFailed") });
      return false;
    } finally {
      busy.current = false;
    }
  };
  return {
    scopeKey: visitKey,
    site,
    loading: context.loading || siteQuery.loading,
    error,
    access: context.access,
    pending,
    operations,
    actions: contract?.actions ?? [],
    contractRevision: contract?.revision ?? null,
    catalogLoading: catalog.loading || journal?.key !== key || !!journal?.failed,
    schemaText: (action) => (contract ? nativeSchemaText(contract, action) : ""),
    templateText: nativeTemplateText,
    recovery,
    onRefresh: refresh,
    onConfigure: (values) => {
      if (!site) return Promise.resolve(false);
      const input: ConfigureNvkeConnectionInput = {
        siteId,
        expectedSiteVersion: site.version,
        expectedConnectionVersion:
          site.connections.find((row) => row.surface === values.surface)?.version ?? 0,
        surface: values.surface,
        endpoint: values.endpoint,
        timeoutSeconds: values.timeoutSeconds,
        allowHttp: values.allowHttp,
        credentials:
          values.credentialMode === "preserve"
            ? null
            : values.credentialMode === "clear"
              ? { token: "", caPem: "", clientCertificatePem: "", clientKeyPem: "" }
              : {
                  token: values.token,
                  caPem: values.caPem,
                  clientCertificatePem: values.clientCertificatePem,
                  clientKeyPem: values.clientKeyPem,
                },
      };
      return runConfiguration(CONFIGURE_NVKE_CONNECTION, input, "configureNvkeConnection");
    },
    onEnable: (enabled) =>
      runConfiguration(
        SET_NVKE_SITE_ENABLED,
        {
          siteId,
          expectedSiteVersion: site?.version ?? 0,
          enabled,
        } satisfies SetNvkeSiteEnabledInput,
        "setNvkeSiteEnabled"
      ),
    onRemove: () =>
      runConfiguration(
        REMOVE_NVKE_SITE,
        { siteId, expectedSiteVersion: site?.version ?? 0 } satisfies RemoveNvkeSiteInput,
        "removeNvkeSite",
        true
      ),
    onRequest: async (values) => {
      const action = contract?.actions.find((row) => row.key === values.operationKey);
      if (
        !current() ||
        !key ||
        !context.scope ||
        !site ||
        !contract ||
        !action ||
        !mayExecute(context.access, action.permission) ||
        siteQuery.loading ||
        catalog.loading ||
        loadFailed ||
        journal?.key !== key ||
        journal.failed ||
        busy.current
      )
        return false;
      const scope = context.scope;
      busy.current = true;
      let rejected = false;
      setMutation({ key: visitKey, pending: true, error: null });
      try {
        return await withRecoveryLock(scope, siteId, async () => {
          if (!current()) return false;
          let original = readRecovery(window.localStorage, scope, siteId);
          if (original) {
            const receipt = await client.query<GetNvkeRequestQuery>({
              query: GET_NVKE_REQUEST,
              variables: { siteId, requestId: original.requestId },
              fetchPolicy: "no-cache",
              context: requestContext,
            });
            if (!current()) return false;
            const operation = receipt.data?.astroliftNvkeRequest;
            if (operation) {
              archiveRecovery(window.localStorage, original, operation);
              setJournal({ key, record: null, failed: false });
              setMutation({ key: visitKey, pending: false, error: null });
              router.push(`/nvke/${siteId}/operations/${operation.id}`);
              return true;
            }
          }
          const digest = await nativeTextDigest(values.parametersText);
          if (!current()) return false;
          if (original) assertOriginalRequest(original, values, digest);
          else {
            if (
              !site.isActive ||
              site.upstreamRevision !== contract.revision ||
              !site.connections.some((row) => row.surface === action.surface && row.isEnabled)
            )
              throw new Error("SOURCE_CHANGED");
            original = {
              ...scope,
              format: 1,
              siteId,
              expectedSiteVersion: site.version,
              revision: contract.revision,
              operationKey: values.operationKey,
              timeoutSeconds: values.timeoutSeconds,
              requestId: crypto.randomUUID(),
              parametersDigest: digest,
              operationId: null,
            };
            saveRecovery(window.localStorage, original);
          }
          setJournal({ key, record: original, failed: false });
          const reply = await client.mutate<
            RequestNvkeOperationTextMutation,
            RequestNvkeOperationTextMutationVariables
          >({
            mutation: REQUEST_NVKE_OPERATION_TEXT,
            variables: {
              input: {
                siteId,
                expectedSiteVersion: original.expectedSiteVersion,
                expectedActorId: scope.actorId,
                requestId: original.requestId,
                operationKey: original.operationKey,
                timeoutSeconds: original.timeoutSeconds,
                parametersText: values.parametersText,
              },
            },
            fetchPolicy: "no-cache",
            context: requestContext,
          });
          if (!current()) return false;
          const operation = reply.data?.requestNvkeOperationText;
          if (!operation?.ok || !operation.data) {
            if (
              operation?.ok === false &&
              !operation.data &&
              operation.errors.length === 1 &&
              archiveRejectedRecovery(window.localStorage, original, operation.errors[0].code)
            ) {
              rejected = true;
              setJournal({ key, record: null, failed: false });
            }
            throw new Error("REQUEST_FAILED");
          }
          archiveRecovery(window.localStorage, original, operation.data);
          setJournal({ key, record: null, failed: false });
          setMutation({ key: visitKey, pending: false, error: null });
          router.push(`/nvke/${siteId}/operations/${operation.data.id}`);
          return true;
        });
      } catch (failure) {
        if (current()) {
          const message =
            failure instanceof NvkeRecoveryError
              ? t(failure.code === "RECOVERY_MISMATCH" ? "recoveryMismatch" : "storageRequired")
              : t(rejected ? "requestRejected" : "requestFailed");
          setMutation({ key: visitKey, pending: false, error: message });
        }
        return false;
      } finally {
        busy.current = false;
      }
    },
  };
};
