"use client";

import { NetworkStatus } from "@apollo/client";
import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";

import { appDeploymentsList, pageVariables, selectPage } from "./app-deployments-list";
import { APP_DEPLOYMENTS_PAGE } from "./app-deployments-query";

interface PageResp {
  astroliftDeploymentsPage: {
    items: AstroliftDeployment[];
    nextCursor: string | null;
    totalCount: number | null;
  };
}
interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

const POLL_MS = 30_000;

/**
 * The data half of AppDeploymentsScreen: list state in the URL (view, chips,
 * search, cursor), the page query, the app's environments as the env chip's
 * options, and new rows held behind the pill while the reader is on the
 * first page. Rows seen on any page stay resolvable, so two selected on
 * different pages can still be compared.
 */
export function useAppDeployments(slug: string, { previews = true }: { previews?: boolean } = {}) {
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, { variables: { appSlug: slug } });
  const envKey = (envs.data?.astroliftEnvironments ?? []).map((e) => e.name).join("\n");
  const definition = React.useMemo(
    () => appDeploymentsList(envKey ? envKey.split("\n") : [], { previews }),
    [envKey, previews]
  );
  const list = useListState(definition);
  const { state, filters } = list;
  const firstPage = state.after === null;
  const [now] = React.useState(() => Date.now());

  const page = useQuery<PageResp>(APP_DEPLOYMENTS_PAGE, {
    variables: pageVariables(slug, filters, state.q, state.pageSize, state.after),
    fetchPolicy: "cache-and-network",
    pollInterval: firstPage ? POLL_MS : 0,
  });

  const data = page.data?.astroliftDeploymentsPage;
  const selected = selectPage(data?.items ?? [], data?.nextCursor ?? null, filters, now);

  const held = useHeldRows(selected.rows, (d) => d.id, {
    live: firstPage,
    resetKey: JSON.stringify(filters) + state.q + state.pageSize,
  });

  const [seen, setSeen] = React.useState<ReadonlyMap<string, AstroliftDeployment>>(() => new Map());
  const items = data?.items;
  React.useEffect(() => {
    if (!items?.length) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- folds each fetched page into the index the compare selection reads
    setSeen((prev) => {
      const next = new Map(prev);
      for (const d of items) next.set(d.id, d);
      return next;
    });
  }, [items]);

  // Mine and Today narrow the page here, so the server's count is not theirs.
  const narrowed = Boolean(filters.startedBy || filters.since);

  return {
    list,
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: page.loading && !data,
    stale: page.networkStatus === NetworkStatus.setVariables && Boolean(data),
    error: page.error && !data ? { message: page.error.message } : null,
    onRetry: () => {
      void page.refetch();
    },
    nextCursor: selected.nextCursor,
    totalCount: narrowed ? null : (data?.totalCount ?? null),
    lookup: (id: string) => seen.get(id) ?? null,
  };
}
