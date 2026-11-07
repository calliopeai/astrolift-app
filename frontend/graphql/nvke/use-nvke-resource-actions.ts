"use client";
import { useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type {
  NvkeResourceActionPanelProps,
  NvkeResourceEditorView,
} from "@/components/screens/nvke/NvkeResourceActionPanel";
import {
  mayExecute,
  type NvkeCollectionView,
  type NvkeRequestValues,
  type NvkeSiteView,
} from "@/components/screens/nvke/types";
import type {
  GetNvkeContractQuery,
  GetNvkeOperationQuery,
  GetNvkeRequestQuery,
  GetNvkeResourceEditorQuery,
  RequestNvkeResourceOperationMutation,
  RequestNvkeResourceOperationMutationVariables,
} from "@/graphql/__generated__/operations";
import { admitNvkeRequest, NvkeAdmissionError } from "@/lib/nvke/admission";
import { nativeSchemaText } from "@/lib/nvke/contract";
import {
  archiveRecovery,
  readRecovery,
  withRecoveryLock,
  sameResourceSource,
  resourceSourceMetadata,
  NvkeRecoveryError,
  type NvkeRecoveryRecord,
  type NvkeResourceSource,
} from "@/lib/nvke/recovery";
import { useNvkeContext, useNvkeEpoch } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import { GET_NVKE_OPERATION, GET_NVKE_REQUEST, GET_NVKE_RESOURCE_EDITOR } from "./nvke.queries";
import { REQUEST_NVKE_RESOURCE_OPERATION } from "./nvke.mutations";

export const useNvkeResourceActions = (args: {
  context: ReturnType<typeof useNvkeContext>;
  site: NvkeSiteView | null;
  contract: GetNvkeContractQuery["astroliftNvkeContract"] | undefined;
  collection: NvkeCollectionView | null;
  source: NvkeResourceSource | null;
  target: string;
  sourceReady: boolean;
}): NvkeResourceActionPanelProps | null => {
  const { context, site, contract, collection, source } = args;
  const client = useApolloClient(),
    router = useRouter(),
    t = useTranslations("nvke");
  const actions =
    collection?.actions.filter(
      (action) => action.mutates && !["POST", "create"].includes(action.verb)
    ) ?? [];
  const key =
    context.scopeKey && site && source && actions.length
      ? JSON.stringify([context.scopeKey, site.id, source])
      : "";
  const { key: visitKey, isCurrent: current } = useNvkeEpoch(key);
  const [choice, setChoice] = useState<{ key: string; action: string } | null>(null);
  const [journal, setJournal] = useState<{
    key: string;
    record: NvkeRecoveryRecord | null;
    failed: boolean;
  } | null>(null);
  const pendingAction = journal?.key === visitKey ? journal.record?.operationKey : undefined;
  const selectedKey =
    choice?.key === visitKey
      ? choice.action
      : (actions.find((action) => action.key === pendingAction)?.key ??
        actions.find((action) => !["DELETE", "delete"].includes(action.verb))?.key ??
        actions[0]?.key ??
        "");
  const selected = actions.find((action) => action.key === selectedKey);
  const [journalEpoch, setJournalEpoch] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; editor: NvkeResourceEditorView } | null>(
    null
  );
  const [state, setState] = useState<{
    key: string;
    pending: boolean;
    error: string | null;
  } | null>(null);
  const preparation = useRef<{
    key: string;
    operationId: string;
    action: string;
    source: NvkeResourceSource;
  } | null>(null);
  const busy = useRef(false);
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  useEffect(() => {
    if (!key || !site || !context.scope) return;
    let active = true;
    void withRecoveryLock(context.scope, site.id, async () => {
      const record = readRecovery(window.localStorage, context.scope!, site.id);
      if (active) setJournal({ key: visitKey, record, failed: false });
    }).catch(() => {
      if (active) setJournal({ key: visitKey, record: null, failed: true });
    });
    return () => {
      active = false;
    };
    // visitKey captures the complete actor/site/resource visit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visitKey, journalEpoch]);
  const recovery = journal?.key === visitKey ? journal.record : null;
  const pending = state?.key === visitKey && state.pending;
  const editor =
    loaded?.key === visitKey && loaded.editor.operationKey === selectedKey ? loaded.editor : null;
  const allowed =
    !!key &&
    !!selected &&
    args.sourceReady &&
    mayExecute(context.access, selected.permission) &&
    journal?.key === visitKey &&
    !journal.failed;
  const message = (error: unknown) =>
    error instanceof NvkeRecoveryError
      ? t(error.code === "RECOVERY_MISMATCH" ? "recoveryMismatch" : "storageRequired")
      : t(
          error instanceof NvkeAdmissionError && error.code === "REQUEST_REJECTED"
            ? "requestRejected"
            : "requestFailed"
        );
  const receipt = async (requestId: string) => {
    const response = await client.query<GetNvkeRequestQuery>({
      query: GET_NVKE_REQUEST,
      variables: { siteId: site!.id, requestId },
      fetchPolicy: "no-cache",
      context: requestContext,
    });
    return response.data?.astroliftNvkeRequest ?? null;
  };
  const readEditor = async (reference: NvkeResourceSource) => {
    const response = await client.query<GetNvkeResourceEditorQuery>({
      query: GET_NVKE_RESOURCE_EDITOR,
      variables: {
        siteId: site!.id,
        expectedActorId: context.scope!.actorId,
        operationKey: selectedKey,
        sourceOperationId: reference.operationId,
        expectedSourceVersion: reference.version,
        sourceResourceKey: reference.resourceKey,
      },
      fetchPolicy: "no-cache",
      context: requestContext,
    });
    if (!current()) return false;
    const reply = response.data?.astroliftNvkeResourceEditor,
      value = reply?.data;
    if (
      !reply?.ok ||
      !value ||
      value.operationKey !== selectedKey ||
      !sameResourceSource(value.source, reference) ||
      !["native_version", "observed_only"].includes(value.concurrency)
    )
      throw new Error("EDITOR_UNAVAILABLE");
    setLoaded({
      key: visitKey,
      editor: {
        id: crypto.randomUUID(),
        siteVersion: site!.version,
        operationKey: selectedKey,
        parametersText: value.parametersText ?? null,
        preparationActionKey: value.preparationActionKey ?? null,
        concurrency: value.concurrency as "native_version" | "observed_only",
        source: reference,
      },
    });
    return true;
  };
  const submit = (values: NvkeRequestValues, reference: NvkeResourceSource) =>
    admitNvkeRequest({
      scope: context.scope!,
      siteId: site!.id,
      expectedSiteVersion: site!.version,
      revision: contract!.revision,
      mode: "resource",
      source: reference,
      values,
      storage: window.localStorage,
      current,
      sourceAvailable: () =>
        args.sourceReady && site!.isActive && site!.upstreamRevision === contract!.revision,
      onRecord: (record) => setJournal({ key: visitKey, record, failed: false }),
      receipt,
      admit: async ({ source: original, ...input }) => {
        if (!original) throw new NvkeRecoveryError("INVALID_RECOVERY");
        const response = await client.mutate<
          RequestNvkeResourceOperationMutation,
          RequestNvkeResourceOperationMutationVariables
        >({
          mutation: REQUEST_NVKE_RESOURCE_OPERATION,
          variables: {
            input: {
              ...input,
              sourceOperationId: original.operationId,
              expectedSourceVersion: original.version,
              sourceResourceKey: original.resourceKey,
            },
          },
          fetchPolicy: "no-cache",
          context: requestContext,
        });
        return response.data?.requestNvkeResourceOperation;
      },
    });
  const perform = async (work: () => Promise<boolean>) => {
    if (!current() || !key || busy.current) return false;
    busy.current = true;
    setState({ key: visitKey, pending: true, error: null });
    try {
      const result = await work();
      if (current()) setState({ key: visitKey, pending: false, error: null });
      return result;
    } catch (error) {
      if (current()) setState({ key: visitKey, pending: false, error: message(error) });
      return false;
    } finally {
      busy.current = false;
    }
  };
  if (!site || !source || !actions.length) return null;
  return {
    siteName: site.name,
    siteVersion: site.version,
    target: args.target,
    actions,
    selectedKey,
    access: context.access,
    sourceReady: allowed,
    pending,
    error:
      journal?.key === visitKey && journal.failed
        ? t("storageRequired")
        : state?.key === visitKey
          ? state.error
          : null,
    recovery,
    editor,
    schemaText: selected && contract ? nativeSchemaText(contract, selected) : "",
    onSelect: (action) => {
      if (!busy.current && !recovery) {
        setChoice({ key: visitKey, action });
        setLoaded(null);
        setState(null);
        preparation.current = null;
      }
    },
    onRefresh: () => {
      if (!busy.current) {
        setState(null);
        setJournalEpoch((epoch) => epoch + 1);
      }
    },
    onRecover: () =>
      perform(async () => {
        if (!context.access.read || !context.scope) return false;
        return withRecoveryLock(context.scope, site.id, async () => {
          const record = readRecovery(window.localStorage, context.scope!, site.id);
          if (!record) return true;
          const operation = await receipt(record.requestId);
          if (!current()) return false;
          if (!operation) throw new NvkeRecoveryError("RECOVERY_MISMATCH");
          archiveRecovery(window.localStorage, record, operation);
          setJournal({ key: visitKey, record: null, failed: false });
          router.push(`/nvke/${site.id}/operations/${operation.id}`);
          return true;
        });
      }),
    onLoad: () =>
      perform(async () => {
        if (!allowed) return false;
        let reference = source;
        if (recovery?.format === 3) {
          const recoveringPreparation = collection?.actions.some(
            (action) =>
              action.key === recovery.operationKey &&
              !action.mutates &&
              action.key !== collection.key
          );
          if (recovery.operationKey !== selectedKey && !recoveringPreparation)
            throw new NvkeRecoveryError("RECOVERY_MISMATCH");
          if (!sameResourceSource(recovery.source, source)) {
            const result = await client.query<GetNvkeOperationQuery>({
              query: GET_NVKE_OPERATION,
              variables: { operationId: recovery.source.operationId },
              fetchPolicy: "no-cache",
              context: requestContext,
            });
            if (!current()) return false;
            const operation = result.data?.astroliftNvkeOperation;
            if (
              recovery.source.resourceKey !== null ||
              operation?.id !== recovery.source.operationId ||
              operation.siteId !== site.id ||
              operation.status !== "SUCCEEDED" ||
              operation.version !== recovery.source.version ||
              !sameResourceSource(operation.resourceSource, source)
            )
              throw new NvkeRecoveryError("RECOVERY_MISMATCH");
          }
          reference = recovery.source;
        }
        return readEditor(reference);
      }),
    onRequest: (values) =>
      perform(async () => {
        if (
          !allowed ||
          !editor ||
          editor.siteVersion !== site.version ||
          values.operationKey !== selectedKey
        )
          return false;
        const operation = await submit(values, editor.source);
        if (!operation || !current()) return false;
        router.push(`/nvke/${site.id}/operations/${operation.id}`);
        return true;
      }),
    onPrepare: () =>
      perform(async () => {
        if (!allowed || !editor?.preparationActionKey || !contract) return false;
        let prepared = preparation.current?.key === visitKey ? preparation.current : null;
        if (!prepared) {
          const operation = await submit(
            {
              operationKey: editor.preparationActionKey,
              parametersText: "{}",
              timeoutSeconds: 7200,
            },
            editor.source
          );
          if (!operation || !current()) return false;
          if (
            operation.operationKey !== editor.preparationActionKey ||
            !sameResourceSource(operation.resourceSource, editor.source)
          ) {
            router.push(`/nvke/${site.id}/operations/${operation.id}`);
            return true;
          }
          prepared = {
            key: visitKey,
            operationId: operation.id,
            action: editor.preparationActionKey,
            source: editor.source,
          };
          preparation.current = prepared;
        }
        for (let attempt = 0; attempt < 15; attempt++) {
          if (!current()) return false;
          const response = await client.query<GetNvkeOperationQuery>({
            query: GET_NVKE_OPERATION,
            variables: { operationId: prepared.operationId },
            fetchPolicy: "no-cache",
            context: requestContext,
          });
          if (!current()) return false;
          const operation = response.data?.astroliftNvkeOperation;
          if (
            !operation ||
            operation.id !== prepared.operationId ||
            operation.siteId !== site.id ||
            operation.operationKey !== prepared.action ||
            !sameResourceSource(operation.resourceSource, prepared.source)
          )
            throw new Error("PREPARATION_UNAVAILABLE");
          if (operation.status === "SUCCEEDED") {
            const reference = resourceSourceMetadata({
              operationId: operation.id,
              version: operation.version,
              resourceKey: null,
            })!;
            const result = await readEditor(reference);
            preparation.current = null;
            return result;
          }
          if (["FAILED", "CANCELLED", "UNCERTAIN"].includes(operation.status)) {
            preparation.current = null;
            throw new Error("PREPARATION_FAILED");
          }
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
        throw new Error("PREPARATION_PENDING");
      }),
  };
};
