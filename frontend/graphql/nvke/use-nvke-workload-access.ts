"use client";
import { useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type {
  NvkeAccessBinding,
  NvkeWorkloadAccessPanelProps,
} from "@/components/screens/nvke/NvkeWorkloadAccessPanel";
import type { NvkeOperationView, NvkeSiteView } from "@/components/screens/nvke/types";
import type {
  ListNvkeAccessQuery,
  GetNvkeResourceEditorQuery,
  EnrollNvkeAccessMutation,
  EnrollNvkeAccessMutationVariables,
  RevokeNvkeAccessMutation,
  RevokeNvkeAccessMutationVariables,
} from "@/graphql/__generated__/operations";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { useNvkeContext, useNvkeEpoch } from "./nvke.hooks";
import { useNvkeRead, NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import { LIST_NVKE_ACCESS, GET_NVKE_RESOURCE_EDITOR } from "./nvke.queries";
import { ENROLL_NVKE_ACCESS, REVOKE_NVKE_ACCESS } from "./nvke.mutations";

const validatePage = (data: unknown, variables: Record<string, unknown>) => {
  const page = (data as ListNvkeAccessQuery | undefined)?.astroliftNvkeWorkloadAccessBindings;
  return (
    !!page && page.items.every((row) => row.siteId === variables.siteId && row.deletedAt === null)
  );
};
export const useNvkeWorkloadAccess = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  operation?: NvkeOperationView | null;
  enabled: boolean;
}): NvkeWorkloadAccessPanelProps | null => {
  const { context, site, operation } = args,
    client = useApolloClient(),
    t = useTranslations("nvke.workloadAccess");
  const relevant =
    operation === undefined ||
    (operation?.operationKey === "getCluster" &&
      operation.status === "SUCCEEDED" &&
      !operation.isInventory);
  const scope =
    args.enabled &&
    relevant &&
    site &&
    context.scope &&
    context.scopeKey &&
    !context.failed &&
    !context.loading &&
    context.access.read
      ? JSON.stringify([
          args.scopeKey,
          site.id,
          site.version,
          operation?.id,
          operation?.version,
          context.access.admin,
        ])
      : "";
  const { key: visitKey, isCurrent: current } = useNvkeEpoch(scope);
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const table = useScopedCursorTable<NvkeAccessBinding>(
    {
      query: LIST_NVKE_ACCESS,
      variables: { siteId: site?.id },
      extract: (data) =>
        (data as ListNvkeAccessQuery | undefined)?.astroliftNvkeWorkloadAccessBindings,
      skip: !scope,
      urlKey: "nvke-access",
    },
    scope,
    requestContext,
    validatePage
  );
  const eligible =
    !!scope &&
    !!operation &&
    context.access.admin &&
    site?.isActive &&
    site.connections.some((c) => c.surface === "CONTROLLER" && c.isEnabled);
  const source = useNvkeRead<GetNvkeResourceEditorQuery>(
    GET_NVKE_RESOURCE_EDITOR,
    {
      siteId: site?.id,
      expectedActorId: context.scope?.actorId,
      operationKey: "getCluster",
      sourceOperationId: operation?.id,
      expectedSourceVersion: operation?.version,
      sourceResourceKey: null,
    },
    eligible ? visitKey : "",
    0,
    context.scope?.organizationId
  );
  const editor = source.data?.astroliftNvkeResourceEditor;
  let candidate: NvkeWorkloadAccessPanelProps["candidate"] = null;
  if (
    eligible &&
    operation &&
    site &&
    editor?.ok &&
    editor.data?.operationKey === "getCluster" &&
    editor.data.source.operationId === operation.id &&
    editor.data.source.version === operation.version &&
    editor.data.source.resourceKey === null &&
    editor.data.preparationActionKey === null &&
    editor.data.concurrency === "observed_only" &&
    typeof editor.data.parametersText === "string"
  ) {
    try {
      const parameters = JSON.parse(editor.data.parametersText);
      if (validNvkeGuid(parameters?.path?.clusterID))
        candidate = {
          clusterId: parameters.path.clusterID,
          sourceOperationId: operation.id,
          sourceVersion: operation.version,
          siteVersion: site.version,
        };
    } catch {}
  }
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    failed: boolean;
    enrolled?: boolean;
  } | null>(null);
  const busy = useRef(false);
  const canManage = !!scope && context.access.admin && !context.loading && !context.failed;
  const active = mutation?.key === visitKey ? mutation : null;
  const refresh = () => {
    if (busy.current) return;
    setMutation(null);
    source.refresh();
    table.refetch();
  };
  const perform = async (
    run: () => Promise<NvkeAccessBinding | null | undefined>,
    check: (binding: NvkeAccessBinding) => boolean,
    enrolled = false
  ) => {
    if (!current() || !canManage || !context.scope || busy.current) return false;
    busy.current = true;
    setMutation({ key: visitKey, pending: true, failed: false });
    try {
      const binding = await run();
      if (!current()) return false;
      if (
        !binding ||
        binding.siteId !== site?.id ||
        !validNvkeGuid(binding.id) ||
        !validNvkeGuid(binding.clusterId) ||
        !validNvkeGuid(binding.sourceOperationId) ||
        binding.version < 1 ||
        !check(binding)
      )
        throw new Error("UNCONFIRMED_GRANT");
      setMutation({ key: visitKey, pending: false, failed: false, enrolled });
      table.refetch();
      return true;
    } catch {
      if (current()) setMutation({ key: visitKey, pending: false, failed: true });
      return false;
    } finally {
      busy.current = false;
    }
  };
  if (!site || !relevant || !scope) return null;
  return {
    site,
    table,
    candidate,
    candidateLoading: !!eligible && source.loading,
    candidateFailed: !!eligible && !source.loading && !candidate,
    canManage,
    pending: active?.pending ?? false,
    error: active?.failed ? t("writeFailed") : null,
    enrolled: !!active?.enrolled,
    onRefresh: refresh,
    onEnroll: async (review) => {
      if (!candidate || JSON.stringify(review) !== JSON.stringify(candidate)) return false;
      return perform(
        async () => {
          const reply = (
            await client.mutate<EnrollNvkeAccessMutation, EnrollNvkeAccessMutationVariables>({
              mutation: ENROLL_NVKE_ACCESS,
              variables: {
                input: {
                  siteId: site.id,
                  expectedSiteVersion: review.siteVersion,
                  expectedActorId: context.scope!.actorId,
                  sourceOperationId: review.sourceOperationId,
                  expectedSourceVersion: review.sourceVersion,
                },
              },
              fetchPolicy: "no-cache",
              context: requestContext,
            })
          ).data?.enrollNvkeWorkloadAccess;
          return reply?.ok ? reply.data : null;
        },
        (binding) =>
          binding.deletedAt === null &&
          binding.clusterId === review.clusterId &&
          binding.upstreamRevision === site.upstreamRevision &&
          site.connections.some(
            (c) =>
              c.surface === "CONTROLLER" && c.isEnabled && c.id === binding.controllerConnectionId
          ),
        true
      );
    },
    onRevoke: async (review) => {
      if (
        review.siteVersion !== site.version ||
        !table.rows.some(
          (row) =>
            row.id === review.id &&
            row.version === review.version &&
            row.clusterId === review.clusterId
        )
      )
        return false;
      return perform(
        async () => {
          const reply = (
            await client.mutate<RevokeNvkeAccessMutation, RevokeNvkeAccessMutationVariables>({
              mutation: REVOKE_NVKE_ACCESS,
              variables: {
                input: {
                  siteId: site.id,
                  expectedSiteVersion: review.siteVersion,
                  expectedActorId: context.scope!.actorId,
                  bindingId: review.id,
                  expectedVersion: review.version,
                },
              },
              fetchPolicy: "no-cache",
              context: requestContext,
            })
          ).data?.revokeNvkeWorkloadAccess;
          return reply?.ok ? reply.data : null;
        },
        (binding) =>
          binding.id === review.id &&
          binding.version > review.version &&
          binding.deletedAt !== null &&
          binding.clusterId === review.clusterId
      );
    },
  };
};
