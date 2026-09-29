"use client";

import * as React from "react";

/**
 * Load older for a field that takes a `limit` and no cursor (list rule 5,
 * until the field pages): the feed asks for `step` more each time the
 * reader nears the end, and the field is re-read at the larger limit. A
 * page that comes back shorter than the limit is the end of it.
 *
 *   const grow = useGrowingLimit(25);
 *   const { data, loading } = useQuery(Q, { variables: { limit: grow.limit } });
 *   <Feed {...grow.feed(items, loading)} … />
 *
 * Every step re-reads the whole window, so it is a stopgap for short
 * histories; a cursor on the field is the fix (needsBackend).
 */
export function useGrowingLimit(initial: number, step = initial) {
  const [limit, setLimit] = React.useState(initial);
  return {
    limit,
    /** Feed props for the items the query returned at `limit`. */
    feed: (count: number, loading: boolean) => ({
      hasMore: count >= limit,
      loadingMore: loading && count > 0,
      onLoadMore: () => setLimit((l) => (count >= l ? l + step : l)),
    }),
  };
}
