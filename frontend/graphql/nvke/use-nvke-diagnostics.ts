"use client";
import type {
  NvkeDiagnosticsPanelProps,
  NvkeDiagnosticRow,
} from "@/components/screens/nvke/NvkeDiagnosticsPanel";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type {
  GetNvkeDiagnosticSummaryQuery,
  GetNvkeOperationQuery,
  ListNvkeDiagnosticRowsQuery,
} from "@/graphql/__generated__/operations";
import { useTranslations } from "next-intl";
import { useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import { GET_NVKE_DIAGNOSTIC_SUMMARY, LIST_NVKE_DIAGNOSTIC_ROWS } from "./nvke.queries";
const kinds: Record<string, string> = {
  getClusterControlPlane: "control_plane",
  getClusterControlPlaneConfigHistory: "config_history",
  getClusterSpireServer: "spire",
};
const validatePage = (input: unknown, variables: Record<string, unknown>) => {
  const page = (input as ListNvkeDiagnosticRowsQuery | undefined)?.astroliftNvkeDiagnosticPage;
  return (
    !!page &&
    page.siteId === variables.siteId &&
    page.operationId === variables.operationId &&
    page.operationVersion === variables.expectedVersion
  );
};
export const useNvkeDiagnostics = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  siteId: string;
  operation: GetNvkeOperationQuery["astroliftNvkeOperation"] | null;
  enabled: boolean;
}): NvkeDiagnosticsPanelProps | null => {
  const t = useTranslations("nvke");
  const { operation, context } = args;
  const eligible =
    args.enabled &&
    operation?.status === "SUCCEEDED" &&
    !!operation.resourceSource &&
    !!kinds[operation.operationKey];
  const scope = eligible ? JSON.stringify([args.scopeKey, operation.id, operation.version]) : "";
  const variables = {
    siteId: args.siteId,
    operationId: operation?.id,
    expectedActorId: context.scope?.actorId,
    expectedVersion: operation?.version,
  };
  const read = useNvkeRead<GetNvkeDiagnosticSummaryQuery>(
    GET_NVKE_DIAGNOSTIC_SUMMARY,
    variables,
    scope,
    0,
    context.scope?.organizationId
  );
  const data = read.data?.astroliftNvkeDiagnosticPage;
  const matches = (page: { siteId: string; operationId: string; operationVersion: number }) =>
    !!operation &&
    page.siteId === args.siteId &&
    page.operationId === operation?.id &&
    page.operationVersion === operation.version;
  const matched = !!data && matches(data) && data.summary.kind === kinds[operation!.operationKey];
  const table = useScopedCursorTable<NvkeDiagnosticRow>(
    {
      query: LIST_NVKE_DIAGNOSTIC_ROWS,
      variables,
      extract: (input) =>
        (input as ListNvkeDiagnosticRowsQuery | undefined)?.astroliftNvkeDiagnosticPage,
      skip: !scope || !matched || data.summary.kind === "control_plane",
      searchVariable: "search",
      urlKey: "nvke-diagnostics",
    },
    scope,
    { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: context.scope?.organizationId },
    validatePage
  );
  if (!eligible) return null;
  return {
    observation: matched ? data : null,
    table:
      table.state === "error"
        ? { ...table, state: "error", rows: [], totalCount: null, nextCursor: null, isStale: false }
        : table,
    loading: read.loading,
    error: read.failed || (!!data && !matched) || table.state === "error" ? t("readFailed") : null,
    onRefresh: () => {
      read.refresh();
      table.refetch();
    },
  };
};
