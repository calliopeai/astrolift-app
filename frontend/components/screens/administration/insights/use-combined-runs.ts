"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { exportCsv } from "@/components/list/exportCsv";
import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { RUN_AUDIT } from "@/graphql/operations/run-audit.queries";

import {
  type CombinedRun,
  fromRunAuditItem,
  RUN_AUDIT_LIST,
  RUN_CSV,
  type RunAuditItem,
  runAuditVariables,
} from "./combined-runs";
import type { CombinedRunsScreenProps } from "./CombinedRunsScreen";

const POLL_MS = 15_000;

/** The export walks the cursor this many rows at a time, and stops at the cap. */
const EXPORT_PAGE = 200;
const EXPORT_CAP = 5_000;

interface RunAuditResp {
  astroliftRunAudit: {
    items: RunAuditItem[];
    nextCursor: string | null;
    totalCount: number | null;
  };
}

/**
 * The run audit's data half: one `astroliftRunAudit` page for the list
 * state in the URL (#2152). The server filters, searches, orders and
 * counts across every kind; the newest page polls, and new runs wait
 * behind the pill while someone reads.
 */
export function useCombinedRuns(): CombinedRunsScreenProps {
  const client = useApolloClient();
  const list = useListState(RUN_AUDIT_LIST);
  // `since:24h` is anchored when the page opens, so the variables stay stable.
  const [now] = React.useState(() => Date.now());
  const variables = runAuditVariables(
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      pageSize: list.state.pageSize,
      after: list.state.after,
    },
    now
  );
  const firstPage = list.state.after === null;

  const query = useQuery<RunAuditResp>(RUN_AUDIT, {
    variables,
    fetchPolicy: "cache-and-network",
    pollInterval: firstPage ? POLL_MS : 0,
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftRunAudit;
  const rows = React.useMemo(() => (page?.items ?? []).map(fromRunAuditItem), [page]);

  const held = useHeldRows(rows, (r) => r.key, {
    live: firstPage && !query.loading,
    resetKey:
      JSON.stringify(variables.filter) + variables.search + variables.sort + variables.first,
  });

  /** Every run the filters match, walked on the server's cursor, as CSV. */
  async function onExportCsv() {
    const out: CombinedRun[] = [];
    let after: string | null = null;
    try {
      do {
        const res: { data?: RunAuditResp } = await client.query<RunAuditResp>({
          query: RUN_AUDIT,
          variables: { ...variables, first: EXPORT_PAGE, after },
          fetchPolicy: "network-only",
        });
        const chunk = res.data?.astroliftRunAudit;
        out.push(...(chunk?.items ?? []).map(fromRunAuditItem));
        after = chunk?.nextCursor ?? null;
      } while (after && out.length < EXPORT_CAP);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "The export failed");
      return;
    }
    if (after) toast.warning(`Exported the first ${EXPORT_CAP.toLocaleString("en-US")} runs.`);
    exportCsv(`run-audit-${new Date().toISOString().slice(0, 10)}`, out, RUN_CSV);
  }

  return {
    list,
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: query.loading && !data,
    stale: query.loading && !query.data && Boolean(data),
    error: query.error && !query.data ? { message: query.error.message } : null,
    onRetry: () => void query.refetch(),
    nextCursor: page?.nextCursor ?? null,
    totalCount: page?.totalCount ?? null,
    onExportCsv: () => void onExportCsv(),
  };
}
