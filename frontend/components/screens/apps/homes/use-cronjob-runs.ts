"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import type { CursorPage } from "@/components/data-table";
import { LIST_SCHEDULED_JOB_RUNS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";

interface RunsPageResp {
  astroliftScheduledJobRunsPage: CursorPage<AstroliftScheduledJobRun>;
}

/** One page of the run history; older pages load as the reader nears the end. */
const RUNS_PAGE_SIZE = 25;

/**
 * The run history behind CronjobHomeScreen, as a feed (Leo's list rule 5):
 * the newest page, polled so a fresh run surfaces without a reload, then
 * older pages on the cursor as the reader scrolls. The older pages are held
 * here rather than merged into the polled page, so a poll never drops them.
 *
 * Scoped to this workload on the server (#1512). It used to fetch the
 * app's 30 newest runs and keep this workload's in the browser, so a job
 * firing less often than its neighbours showed "no runs recorded yet"
 * while its runs existed.
 */
export function useCronjobRuns(appSlug: string, workloadSlug: string) {
  const client = useApolloClient();
  const scope = { appSlug, workloadSlug };
  const head = useQuery<RunsPageResp>(LIST_SCHEDULED_JOB_RUNS_PAGE, {
    variables: { ...scope, limit: RUNS_PAGE_SIZE },
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });
  const first = head.data?.astroliftScheduledJobRunsPage;
  const [older, setOlder] = React.useState<{
    rows: AstroliftScheduledJobRun[];
    cursor: string | null;
  } | null>(null);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [moreError, setMoreError] = React.useState<string | null>(null);
  const cursor = older ? older.cursor : (first?.nextCursor ?? null);

  const onLoadMore = React.useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const res = await client.query<RunsPageResp>({
        query: LIST_SCHEDULED_JOB_RUNS_PAGE,
        variables: { appSlug, workloadSlug, limit: RUNS_PAGE_SIZE, after: cursor },
        fetchPolicy: "network-only",
      });
      const page = res.data?.astroliftScheduledJobRunsPage;
      setOlder((prev) => ({
        rows: [...(prev?.rows ?? []), ...(page?.items ?? [])],
        cursor: page?.nextCursor ?? null,
      }));
    } catch (e) {
      setMoreError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  }, [appSlug, client, cursor, loadingMore, workloadSlug]);

  const runs = React.useMemo(() => {
    const seen = new Set<string>();
    return [...(first?.items ?? []), ...(older?.rows ?? [])].filter((r) =>
      seen.has(r.id) ? false : (seen.add(r.id), true)
    );
  }, [first?.items, older?.rows]);

  return {
    runs,
    totalCount: first?.totalCount ?? null,
    loading: head.loading && !head.data,
    /** With no runs it takes the feed; with runs it sits at the end. */
    error: head.data ? moreError : (head.error?.message ?? null),
    onRetry: () => void head.refetch(),
    hasMore: Boolean(cursor),
    loadingMore,
    onLoadMore: () => void onLoadMore(),
  };
}
