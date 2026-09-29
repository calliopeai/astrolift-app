"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import type { DocumentNode } from "graphql";
import * as React from "react";

import type { CursorPage } from "@/components/data-table";

/** The backend's MAX_PAGE_LIMIT: one request per 200 rows. */
export const WALK_LIMIT = 200;

/**
 * Past this many rows the walk stops and says so (`truncated`), so an org of
 * thousands never turns one list into dozens of requests.
 */
export const WALK_CAP = 2000;

const NONE: never[] = [];

export interface Walk<T> {
  rows: T[];
  /** First page in flight, nothing to show yet. */
  loading: boolean;
  /** Rows on screen answer an older question, or the tail is still walking. */
  stale: boolean;
  error: { message: string } | null;
  /** The walk stopped at the cap: filters and sort cover the first `WALK_CAP` rows. */
  truncated: boolean;
  refetch: () => void;
}

/**
 * Every row of one identity `…Page` query for `variables`, walked by cursor
 * to the end (or to `WALK_CAP`). The identity page queries take `search`,
 * `limit` and `after` only, so a list that promises filters, sort and page
 * numbers (spec 44 §5.1) has to hold the set and answer them itself, the way
 * the Clusters list does. The first page goes through `useQuery`, so a
 * refetch by operation name after a mutation re-walks the rest. When the
 * backend grows the §5.1 contract this hook goes away.
 */
export function useWalk<T>(
  query: DocumentNode,
  extract: (data: unknown) => CursorPage<T> | null | undefined,
  { variables = {}, skip = false }: { variables?: Record<string, unknown>; skip?: boolean } = {}
): Walk<T> {
  const client = useApolloClient();
  const head = useQuery(query, {
    variables: { ...variables, limit: WALK_LIMIT },
    skip,
    fetchPolicy: "cache-and-network",
  });
  const data = head.data ?? head.previousData;
  const first = extract(data);
  const cursor = first?.nextCursor ?? null;
  const key = JSON.stringify(variables);

  const [tail, setTail] = React.useState<{
    after: string;
    rows: T[];
    truncated: boolean;
  } | null>(null);
  const [tailError, setTailError] = React.useState<Error | null>(null);

  React.useEffect(() => {
    if (!cursor || skip) return;
    let cancelled = false;
    (async () => {
      const rows: T[] = [];
      let after: string | null = cursor;
      while (after && !cancelled && rows.length + WALK_LIMIT < WALK_CAP) {
        const res: { data?: unknown } = await client.query({
          query,
          variables: { ...JSON.parse(key), limit: WALK_LIMIT, after },
          fetchPolicy: "network-only",
        });
        const page = extract(res.data);
        rows.push(...(page?.items ?? []));
        after = page?.nextCursor ?? null;
      }
      if (!cancelled) {
        setTail({ after: cursor, rows, truncated: Boolean(after) });
        setTailError(null);
      }
    })().catch((e: unknown) => {
      if (!cancelled) setTailError(e instanceof Error ? e : new Error(String(e)));
    });
    return () => {
      cancelled = true;
    };
    // `first` changes on a refetch; re-walk so the tail is as fresh as the head.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, query, cursor, key, skip, first]);

  const tailRows = cursor && tail?.after === cursor ? tail.rows : null;
  const firstItems = first?.items;
  const rows = React.useMemo(
    () => [...(firstItems ?? []), ...(tailRows ?? [])],
    [firstItems, tailRows]
  );
  const error = head.error ?? tailError;

  return {
    rows: skip ? NONE : rows,
    loading: !skip && head.loading && !data,
    stale:
      !skip &&
      ((head.loading && !head.data && Boolean(data)) || Boolean(cursor && tail?.after !== cursor)),
    error: !skip && error ? { message: error.message } : null,
    truncated: Boolean(cursor && tail?.after === cursor && tail.truncated),
    refetch: () => {
      void head.refetch();
    },
  };
}
