"use client";
import { useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import {
  fixedNvkeTable,
  mayExecute,
  type NvkeResourcesScreenProps,
  type NvkeInventoryScreenProps,
  type NvkeInventoryItemView,
  type NvkeResourceScreenProps,
} from "@/components/screens/nvke/types";
import type {
  GetNvkeSiteQuery,
  GetNvkeContractQuery,
  GetNvkeOperationQuery,
  GetNvkeRequestQuery,
  ListNvkeInventoryPageQuery,
  RequestNvkeInventoryMutation,
  RequestNvkeInventoryMutationVariables,
  GetNvkeInventoryResourceQuery,
  GetNvkeInventoryDocumentTextQuery,
} from "@/graphql/__generated__/operations";
import { nativeSchemaText } from "@/lib/nvke/contract";
import { admitNvkeRequest, NvkeAdmissionError } from "@/lib/nvke/admission";
import { nvkeInventoryHref, validNvkeCollectionKey } from "@/lib/nvke/inventory";
import {
  validNvkeGuid,
  NvkeRecoveryError,
  readRecovery,
  withRecoveryLock,
  type NvkeRecoveryRecord,
} from "@/lib/nvke/recovery";
import { useNvkeContext, useNvkeEpoch } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import {
  GET_NVKE_SITE,
  GET_NVKE_CONTRACT,
  GET_NVKE_OPERATION,
  GET_NVKE_REQUEST,
  LIST_NVKE_INVENTORY_PAGE,
  GET_NVKE_INVENTORY_RESOURCE,
  GET_NVKE_INVENTORY_DOCUMENT_TEXT,
} from "./nvke.queries";
import { REQUEST_NVKE_INVENTORY } from "./nvke.mutations";
import { useNvkeResourceActions } from "./use-nvke-resource-actions";

const useInventorySource = (siteId: string) => {
  const context = useNvkeContext();
  const key =
    context.scopeKey && context.access.read && validNvkeGuid(siteId)
      ? JSON.stringify([context.scopeKey, siteId])
      : "";
  const visit = useNvkeEpoch(key);
  const readKey = key ? visit.key : "";
  const siteQuery = useNvkeRead<GetNvkeSiteQuery>(
    GET_NVKE_SITE,
    { siteId },
    readKey,
    0,
    context.scope?.organizationId
  );
  const catalog = useNvkeRead<GetNvkeContractQuery>(
    GET_NVKE_CONTRACT,
    {},
    readKey,
    0,
    context.scope?.organizationId
  );
  const liveSite =
    siteQuery.data?.astroliftNvkeSite?.id === siteId ? siteQuery.data.astroliftNvkeSite : null;
  const liveContract = catalog.data?.astroliftNvkeContract;
  const [saved, setSaved] = useState<{
    key: string;
    site: NonNullable<GetNvkeSiteQuery["astroliftNvkeSite"]>;
    contract: GetNvkeContractQuery["astroliftNvkeContract"];
  } | null>(null);
  if (
    liveSite &&
    liveContract &&
    (saved?.key !== readKey || saved.site !== liveSite || saved.contract !== liveContract)
  ) {
    setSaved({ key: readKey, site: liveSite, contract: liveContract });
  }
  // Keep this visit's editor mounted during refresh; admission still checks loading/errors.
  const previous = readKey && saved?.key === readKey ? saved : null;
  return {
    context,
    key,
    siteQuery,
    catalog,
    site: liveSite ?? previous?.site ?? null,
    contract: liveContract ?? previous?.contract,
  };
};

export const useNvkeResources = (
  siteId: string
): NvkeResourcesScreenProps & { scopeKey: string } => {
  const { context, key, site, siteQuery, catalog, contract } = useInventorySource(siteId);
  const t = useTranslations("nvke");
  const visit = useNvkeEpoch(key);
  // These are the 18 pinned collection definitions, not a capped remote resource list.
  const collections = fixedNvkeTable(contract?.collections ?? []);
  return {
    scopeKey: visit.key,
    site,
    collections,
    loading: context.loading || siteQuery.loading || catalog.loading,
    error:
      context.failed || siteQuery.failed || catalog.failed || (!context.loading && !key)
        ? t("readFailed")
        : null,
    onRefresh: () => {
      siteQuery.refresh();
      catalog.refresh();
    },
  };
};

export const useNvkeInventory = (
  siteId: string,
  collectionKey: string,
  snapshotId: string | null
): NvkeInventoryScreenProps & { scopeKey: string } => {
  const { context, key, site, siteQuery, catalog, contract } = useInventorySource(siteId);
  const client = useApolloClient(),
    router = useRouter(),
    t = useTranslations("nvke");
  const collection = contract?.collections.find((row) => row.key === collectionKey) ?? null;
  const binding =
    key && validNvkeCollectionKey(collectionKey)
      ? JSON.stringify([key, collectionKey, snapshotId])
      : "";
  const { key: visitKey, isCurrent: current } = useNvkeEpoch(binding);
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const allowed = !!collection && mayExecute(context.access, collection.permission);
  const observation = useNvkeRead<GetNvkeOperationQuery>(
    GET_NVKE_OPERATION,
    { operationId: snapshotId },
    binding && allowed && validNvkeGuid(snapshotId) ? binding : "",
    0,
    context.scope?.organizationId
  );
  const received = observation.data?.astroliftNvkeOperation;
  const operation =
    received?.id === snapshotId &&
    received.siteId === siteId &&
    received.operationKey === collectionKey &&
    received.isInventory
      ? received
      : null;
  const complete = operation?.status === "SUCCEEDED";
  const table = useScopedCursorTable<NvkeInventoryItemView>(
    {
      query: LIST_NVKE_INVENTORY_PAGE,
      variables: { operationId: snapshotId, expectedActorId: context.scope?.actorId },
      extract: (data) =>
        (data as ListNvkeInventoryPageQuery | undefined)?.astroliftNvkeInventoryPage,
      skip: !binding || !allowed || !complete,
      searchVariable: "search",
      urlKey: "nvke-inventory",
    },
    binding,
    requestContext
  );
  const [journalEpoch, setJournalEpoch] = useState(0);
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
  const busy = useRef(false);
  useEffect(() => {
    if (!binding || !context.scope) return;
    let active = true;
    void withRecoveryLock(context.scope, siteId, async () => {
      const record = readRecovery(window.localStorage, context.scope!, siteId);
      if (active) setJournal({ key: binding, record, failed: false });
    }).catch(() => {
      if (active) setJournal({ key: binding, record: null, failed: true });
    });
    return () => {
      active = false;
    };
    // binding includes the complete caller, site, collection and snapshot visit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding, journalEpoch]);
  const failed =
    context.failed ||
    siteQuery.failed ||
    catalog.failed ||
    observation.failed ||
    (!context.loading && !key) ||
    (!!contract && (!collection || !allowed)) ||
    (!!snapshotId && !observation.loading && !operation);
  const refresh = () => {
    siteQuery.refresh();
    catalog.refresh();
    observation.refresh();
    table.refetch();
    setJournalEpoch((value) => value + 1);
    setMutation(null);
  };
  return {
    scopeKey: visitKey,
    site,
    collection,
    table,
    operation,
    observedAt: operation?.finishedAt ?? null,
    loading: context.loading || siteQuery.loading || catalog.loading,
    access: context.access,
    actions: collection?.actions.filter((row) => row.key === collection.key) ?? [],
    contractRevision: contract?.revision ?? null,
    catalogLoading: catalog.loading || journal?.key !== binding || !!journal?.failed,
    schemaText: (action) => (contract ? nativeSchemaText(contract, action) : ""),
    templateText: () => collection?.defaultParametersText ?? "{}",
    recovery: journal?.key === binding ? journal.record : null,
    pending: mutation?.key === visitKey && mutation.pending,
    error: failed
      ? t("readFailed")
      : journal?.key === binding && journal.failed
        ? t("storageRequired")
        : mutation?.key === visitKey
          ? mutation.error
          : null,
    onRefresh: refresh,
    onRequest: async (values) => {
      if (
        !current() ||
        !binding ||
        !context.scope ||
        !site ||
        !collection ||
        !contract ||
        !allowed ||
        failed ||
        values.operationKey !== collection.key ||
        siteQuery.loading ||
        catalog.loading ||
        journal?.key !== binding ||
        journal.failed ||
        busy.current
      )
        return false;
      busy.current = true;
      setMutation({ key: visitKey, pending: true, error: null });
      try {
        const operation = await admitNvkeRequest({
          scope: context.scope,
          siteId,
          expectedSiteVersion: site.version,
          revision: contract.revision,
          mode: "inventory",
          values,
          storage: window.localStorage,
          current,
          sourceAvailable: () =>
            site.isActive &&
            site.upstreamRevision === contract.revision &&
            site.connections.some((row) => row.surface === collection.surface && row.isEnabled),
          onRecord: (record) => setJournal({ key: binding, record, failed: false }),
          receipt: async (requestId) => {
            const result = await client.query<GetNvkeRequestQuery>({
              query: GET_NVKE_REQUEST,
              variables: { siteId, requestId },
              fetchPolicy: "no-cache",
              context: requestContext,
            });
            return result.data?.astroliftNvkeRequest ?? null;
          },
          admit: async (input) => {
            const result = await client.mutate<
              RequestNvkeInventoryMutation,
              RequestNvkeInventoryMutationVariables
            >({
              mutation: REQUEST_NVKE_INVENTORY,
              variables: { input },
              fetchPolicy: "no-cache",
              context: requestContext,
            });
            return result.data?.requestNvkeInventory;
          },
        });
        if (!operation || !current()) return false;
        setMutation({ key: visitKey, pending: false, error: null });
        router.push(
          operation.isInventory
            ? nvkeInventoryHref(siteId, operation.operationKey, operation.id)
            : `/nvke/${siteId}/operations/${operation.id}`
        );
        return true;
      } catch (failure) {
        if (current())
          setMutation({
            key: visitKey,
            pending: false,
            error:
              failure instanceof NvkeRecoveryError
                ? t(failure.code === "RECOVERY_MISMATCH" ? "recoveryMismatch" : "storageRequired")
                : t(
                    failure instanceof NvkeAdmissionError && failure.code === "REQUEST_REJECTED"
                      ? "requestRejected"
                      : "requestFailed"
                  ),
          });
        return false;
      } finally {
        busy.current = false;
      }
    },
  };
};

export const useNvkeResource = (
  siteId: string,
  collectionKey: string,
  operationId: string,
  resourceKey: string
): NvkeResourceScreenProps & { scopeKey: string } => {
  const { context, key, site, siteQuery, catalog, contract } = useInventorySource(siteId);
  const client = useApolloClient(),
    t = useTranslations("nvke");
  const selected = contract?.collections.find((row) => row.key === collectionKey);
  const allowed = !!selected && mayExecute(context.access, selected.permission);
  const binding =
    key &&
    validNvkeGuid(operationId) &&
    validNvkeCollectionKey(collectionKey) &&
    resourceKey.length <= 4096 &&
    allowed
      ? JSON.stringify([key, collectionKey, operationId, resourceKey])
      : "";
  const { key: visitKey, isCurrent: current } = useNvkeEpoch(binding);
  const variables = { operationId, expectedActorId: context.scope?.actorId, resourceKey };
  const observation = useNvkeRead<GetNvkeInventoryResourceQuery>(
    GET_NVKE_INVENTORY_RESOURCE,
    variables,
    binding,
    0,
    context.scope?.organizationId
  );
  const liveData = observation.data?.astroliftNvkeInventoryResource;
  const [saved, setSaved] = useState<{
    key: string;
    data: GetNvkeInventoryResourceQuery["astroliftNvkeInventoryResource"];
  } | null>(null);
  if (
    liveData?.siteId === siteId &&
    liveData.operationId === operationId &&
    liveData.collection.key === collectionKey &&
    liveData.item.key === resourceKey &&
    (saved?.key !== visitKey || saved.data !== liveData)
  )
    setSaved({ key: visitKey, data: liveData });
  const data =
    liveData ??
    (binding && !observation.failed && saved?.key === visitKey ? saved.data : undefined);
  const matched =
    data?.siteId === siteId &&
    data.operationId === operationId &&
    data.collection.key === collectionKey &&
    data.item.key === resourceKey;
  const failed =
    context.failed ||
    siteQuery.failed ||
    catalog.failed ||
    observation.failed ||
    (!!data && !matched) ||
    (!context.loading && !key) ||
    (!!contract && (!selected || !allowed));
  const [download, setDownload] = useState<{
    key: string;
    pending: boolean;
    failed: boolean;
  } | null>(null);
  const busy = useRef(false);
  const resourceActions = useNvkeResourceActions({
    context,
    site,
    contract,
    collection: matched ? data.collection : null,
    source:
      matched && Number.isSafeInteger(data.operationVersion) && data.operationVersion > 0
        ? { operationId, version: data.operationVersion, resourceKey }
        : null,
    target: matched ? data.item.name || data.item.identity : "",
    sourceReady:
      !!binding &&
      allowed &&
      !failed &&
      !siteQuery.loading &&
      !catalog.loading &&
      !observation.loading,
  });
  return {
    scopeKey: visitKey,
    siteId,
    operationId,
    siteName: site?.name ?? "",
    resourceActions,
    collection: matched ? data.collection : null,
    item: matched ? data.item : null,
    observedAt: matched ? (data.observedAt ?? null) : null,
    loading: context.loading || siteQuery.loading || catalog.loading || observation.loading,
    pending: (download?.key === visitKey && download.pending) || !!resourceActions?.pending,
    error: failed
      ? t("readFailed")
      : download?.key === visitKey && download.failed
        ? t("downloadFailed")
        : null,
    canRead: !!binding && allowed && !failed,
    onRefresh: () => {
      siteQuery.refresh();
      catalog.refresh();
      observation.refresh();
      setDownload(null);
      resourceActions?.onRefresh();
    },
    onDownload: async () => {
      if (!current() || !binding || !allowed || !matched || failed || busy.current) return;
      busy.current = true;
      setDownload({ key: visitKey, pending: true, failed: false });
      try {
        const response = await client.query<GetNvkeInventoryDocumentTextQuery>({
          query: GET_NVKE_INVENTORY_DOCUMENT_TEXT,
          variables,
          fetchPolicy: "no-cache",
          context: {
            ...NVKE_REQUEST_CONTEXT,
            expectedNvkeOrganization: context.scope?.organizationId,
          },
        });
        if (!current()) return;
        const result = response.data?.astroliftNvkeInventoryDocumentText;
        if (!result?.ok || typeof result.data !== "string") throw new Error("DOWNLOAD_FAILED");
        const url = URL.createObjectURL(
          new Blob([result.data], { type: "application/json;charset=utf-8" })
        );
        try {
          const link = document.createElement("a");
          link.href = url;
          link.download = `nvke-resource-${operationId}.json`;
          link.click();
        } finally {
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
        setDownload({ key: visitKey, pending: false, failed: false });
      } catch {
        if (current()) setDownload({ key: visitKey, pending: false, failed: true });
      } finally {
        busy.current = false;
      }
    },
  };
};
