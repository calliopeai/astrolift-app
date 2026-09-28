"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import type { DocumentNode } from "graphql";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import {
  BULK_PUSH_SECRETS,
  BULK_RESYNC_MANIFEST,
  BULK_ROLLING_RESTART,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  BulkOperationResult,
} from "@/graphql/lifecycle/lifecycle.types";
import {
  LIST_APPS_PAGE,
  LIST_MY_APPS_PAGE,
  LIST_WORKLOADS_PAGE,
} from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { classifyTopology } from "@/lib/topology";

import { APPS_LIST, type AppRow, selectApps } from "./apps-list";

/** The walk's page size: the backend's page cap, so up to 200 apps is one request. */
const WALK_LIMIT = 200;

type Page<T> = Record<string, CursorPage<T> | undefined>;

/**
 * Every row a cursor-paged field returns for `variables`: the first page
 * through `useQuery`, the rest walked by cursor, so nothing past the first
 * page is dropped (the Clusters list's walk, #1230).
 */
function useWalk<T>(
  query: DocumentNode,
  field: string,
  cursorVariable: "cursor" | "after",
  variables: Record<string, unknown>
) {
  const client = useApolloClient();
  const head = useQuery<Page<T>>(query, {
    variables: { ...variables, limit: WALK_LIMIT },
    fetchPolicy: "cache-and-network",
  });
  const data = head.data ?? head.previousData;
  const first = data?.[field];
  const cursor = first?.nextCursor ?? null;
  const key = JSON.stringify(variables);

  const [tail, setTail] = React.useState<{ from: string; rows: T[] } | null>(null);
  const [tailError, setTailError] = React.useState<Error | null>(null);

  React.useEffect(() => {
    if (!cursor) return;
    let cancelled = false;
    (async () => {
      const rows: T[] = [];
      let next: string | null = cursor;
      while (next && !cancelled) {
        const res: { data?: Page<T> } = await client.query<Page<T>>({
          query,
          variables: { ...JSON.parse(key), limit: WALK_LIMIT, [cursorVariable]: next },
          fetchPolicy: "network-only",
        });
        const page = res.data?.[field];
        rows.push(...(page?.items ?? []));
        next = page?.nextCursor ?? null;
      }
      if (!cancelled) {
        setTail({ from: `${key}|${cursor}`, rows });
        setTailError(null);
      }
    })().catch((e: unknown) => {
      if (!cancelled) setTailError(e instanceof Error ? e : new Error(String(e)));
    });
    return () => {
      cancelled = true;
    };
  }, [client, query, field, cursorVariable, key, cursor]);

  const walking = Boolean(cursor) && tail?.from !== `${key}|${cursor}`;
  const items = React.useMemo(
    () => [...(first?.items ?? []), ...(cursor && !walking ? (tail?.rows ?? []) : [])],
    [first?.items, cursor, walking, tail]
  );

  return {
    items,
    loading: head.loading && !data,
    // Rows on screen answer an older search, or the tail is still walking.
    stale: (head.loading && !head.data && Boolean(data)) || walking,
    error: head.error ?? tailError,
    refetch: head.refetch,
  };
}

/**
 * The Apps list: URL list state, the registry walk joined with the org's
 * environments (clusters) and workloads (topology), the per-browser pin set,
 * and the three bulk mutations. The data half of AppsListScreen.
 */
export function useAppsList() {
  const list = useListState(APPS_LIST);
  const { state } = list;
  const { can } = useMyPermissions();
  const mine = state.view === "mine";

  const walk = useWalk<AstroliftRegisteredApp>(
    mine ? LIST_MY_APPS_PAGE : LIST_APPS_PAGE,
    mine ? "astroliftMyAppsPage" : "astroliftAppsPage",
    "cursor",
    {
      includeFreshness: true,
      search: state.q.trim() || null,
      includeArchived: state.view === "archived",
    }
  );
  const workloads = useWalk<AstroliftWorkload>(
    LIST_WORKLOADS_PAGE,
    "astroliftWorkloadsPage",
    "after",
    {}
  );
  const envs = useQuery<{ astroliftEnvironments: AstroliftAppEnvironment[] }>(LIST_ENVIRONMENTS, {
    variables: { appSlug: null },
  });

  // Kind and cluster are best effort: when either side query fails the list
  // still renders, and those two columns read "unknown".
  const apps: AppRow[] = React.useMemo(() => {
    const byApp = new Map<string, AstroliftWorkload[]>();
    for (const w of workloads.items) {
      byApp.set(w.registeredAppSlug, [...(byApp.get(w.registeredAppSlug) ?? []), w]);
    }
    const clusters = new Map<string, string[]>();
    for (const e of envs.data?.astroliftEnvironments ?? []) {
      if (!e.clusterSlug) continue;
      const seen = clusters.get(e.registeredAppSlug) ?? [];
      if (!seen.includes(e.clusterSlug))
        clusters.set(e.registeredAppSlug, [...seen, e.clusterSlug]);
    }
    return walk.items.map((a) => {
      const ws = byApp.get(a.slug) ?? [];
      return {
        ...a,
        topology: ws.length > 0 ? classifyTopology({ workloads: ws }) : null,
        clusters: clusters.get(a.slug) ?? [],
      };
    });
  }, [walk.items, workloads.items, envs.data]);

  const { pinned, toggle: togglePin } = usePinnedApps();
  const { rows, totalCount } = selectApps(apps, {
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
    pinned,
  });

  const [bulkRollingRestart, rollingRestartState] = useMutation<{
    bulkRollingRestart: BulkOperationResult;
  }>(BULK_ROLLING_RESTART);
  const [bulkPushSecrets, pushSecretsState] = useMutation<{
    bulkPushSecrets: BulkOperationResult;
  }>(BULK_PUSH_SECRETS);
  const [bulkResyncManifest, resyncManifestState] = useMutation<{
    bulkResyncManifest: BulkOperationResult;
  }>(BULK_RESYNC_MANIFEST);

  // #698: each action fans out server-side; the toast reports the aggregate
  // okCount/total plus a separate error toast listing the failed slugs.
  function report(label: string, result: BulkOperationResult | undefined): boolean {
    if (!result) return false;
    const total = result.okCount + result.failedCount;
    toast.success(`${label}: ${result.okCount}/${total} apps succeeded`);
    if (result.failedCount > 0) {
      const failed = result.perApp
        .filter((p) => !p.ok)
        .map((p) => p.appSlug)
        .join(", ");
      toast.error(`${label} failed for: ${failed}`);
    }
    return true;
  }

  return {
    list,
    rows,
    totalCount,
    loading: walk.loading,
    stale: walk.stale,
    error: walk.error ? { message: walk.error.message } : null,
    onRetry: () => void walk.refetch(),
    canDeploy: can("app.deploy"),
    pinned: pinned as ReadonlySet<string>,
    togglePin,
    bulkBusy:
      rollingRestartState.loading || pushSecretsState.loading || resyncManifestState.loading,
    onRollingRestart: async (appSlugs: string[]) => {
      const { data } = await bulkRollingRestart({
        variables: { input: { appSlugs, environmentName: null } },
      });
      return report("Rolling restart", data?.bulkRollingRestart);
    },
    onPushSecrets: async (
      appSlugs: string[],
      bundleSlug: string,
      environmentName: string | null
    ) => {
      const { data } = await bulkPushSecrets({
        variables: { input: { appSlugs, bundleSlug, environmentName } },
      });
      return report("Push secrets", data?.bulkPushSecrets);
    },
    onResyncManifest: async (appSlugs: string[]) => {
      const { data } = await bulkResyncManifest({ variables: { input: { appSlugs } } });
      return report("Resync manifest", data?.bulkResyncManifest);
    },
  };
}

// #697: pinned apps persist in localStorage so operators who work with the
// same two or three apps daily keep them at the top across sessions. The pin
// set is per browser, not synced (a personal sort preference).
const PINNED_APPS_KEY = "astrolift.apps.pinned.v1";

function loadPinned(): Set<string> {
  try {
    const raw = window.localStorage.getItem(PINNED_APPS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(
      Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : []
    );
  } catch {
    return new Set();
  }
}

function usePinnedApps() {
  const [pinned, setPinned] = React.useState<Set<string>>(() => new Set());
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage is the external system; reading it in render would break hydration
    setPinned(loadPinned());
  }, []);
  const toggle = React.useCallback((slug: string) => {
    setPinned((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      try {
        window.localStorage.setItem(PINNED_APPS_KEY, JSON.stringify(Array.from(next)));
      } catch {
        // Storage blocked or full: the pin lasts for this visit only.
      }
      return next;
    });
  }, []);
  return { pinned, toggle };
}
