"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_AUDIT_EVENTS_PAGE } from "@/graphql/operations/operations.queries";
import type {
  AstroliftAuditEvent,
  AstroliftAuditEventPage,
} from "@/graphql/operations/operations.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface PageResp {
  astroliftAuditEventsPage: AstroliftAuditEventPage;
}

const PAGE_SIZE = 25;

/**
 * A person's Activity tab: the audit events they were the actor of, newest
 * first, older pages on the cursor as the Feed nears its end. The audit
 * query filters on the actor only; "grants and revokes about them" (design
 * 3.2) needs a target filter it does not take yet. Read needs
 * `audit_log.read`; without it nothing is fetched.
 */
export function usePersonActivity(userId: string | null) {
  const perms = useMyPermissions();
  const allowed = perms.can("audit_log.read");
  const { data, loading, error, fetchMore, refetch } = useQuery<PageResp>(LIST_AUDIT_EVENTS_PAGE, {
    variables: { actorId: userId, limit: PAGE_SIZE },
    skip: !userId || !allowed,
    fetchPolicy: "cache-and-network",
  });
  const page = data?.astroliftAuditEventsPage;
  const nextCursor = page?.nextCursor ?? null;
  const [loadingMore, setLoadingMore] = React.useState(false);

  const onLoadMore = React.useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      await fetchMore({
        variables: { actorId: userId, limit: PAGE_SIZE, after: nextCursor },
        updateQuery: (prev, { fetchMoreResult }) =>
          fetchMoreResult
            ? {
                astroliftAuditEventsPage: {
                  ...fetchMoreResult.astroliftAuditEventsPage,
                  items: [
                    ...prev.astroliftAuditEventsPage.items,
                    ...fetchMoreResult.astroliftAuditEventsPage.items,
                  ],
                },
              }
            : prev,
      });
    } finally {
      setLoadingMore(false);
    }
  }, [fetchMore, loadingMore, nextCursor, userId]);

  return {
    allowed,
    items: (page?.items ?? []) as AstroliftAuditEvent[],
    loading: allowed && (!userId || loading) && !page,
    error: error && !page ? { message: error.message } : null,
    hasMore: Boolean(nextCursor),
    loadingMore,
    onLoadMore: () => void onLoadMore(),
    onRetry: () => void refetch(),
  };
}
