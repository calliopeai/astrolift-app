"use client";

import { useMutation } from "@apollo/client/react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  useCursorTable,
  useRowSelection,
  type CursorTableController,
} from "@/components/data-table";
import type { AppsListSortKey } from "@/graphql/__generated__/schema";
import {
  BULK_PUSH_SECRETS,
  BULK_RESYNC_MANIFEST,
  BULK_ROLLING_RESTART,
} from "@/graphql/lifecycle/lifecycle.mutations";
import type { BulkOperationResult } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS_PAGE } from "@/graphql/registry/registry.queries";
import type {
  AppListStatusFilter,
  AstroliftRegisteredApp,
  AstroliftRegisteredAppPage,
} from "@/graphql/registry/registry.types";
import { useViewToggle } from "@/hooks/use-view-toggle";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface Resp {
  astroliftAppsPage: AstroliftRegisteredAppPage;
}

// Filter pill identifiers — the union is the user-facing axis (matches
// the health pulse + a synthetic "in-flight" bucket). The pill-to-server
// mapping lives in `PILL_TO_STATUS_FILTER`.
export type Pill = "all" | "ok" | "degraded" | "stale" | "never_deployed";

const PILL_TO_STATUS_FILTER: Record<Pill, AppListStatusFilter | null> = {
  all: null,
  ok: "OK",
  degraded: "DEGRADED",
  stale: "STALE",
  never_deployed: "NEVER_DEPLOYED",
};

const STATUS_FILTER_TO_PILL: Record<AppListStatusFilter, Pill> = {
  ALL: "all",
  OK: "ok",
  DEGRADED: "degraded",
  STALE: "stale",
  NEVER_DEPLOYED: "never_deployed",
};

// #697 — pinned apps persist in localStorage so operators who work
// with the same 2-3 apps daily can keep them at the top across
// sessions. The pin set is per-browser, not synced server-side
// (no privacy implications, just a personal sort preference).
const PINNED_APPS_KEY = "astrolift.apps.pinned.v1";

function loadPinned(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(PINNED_APPS_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed.filter((x) => typeof x === "string")) : new Set();
  } catch {
    return new Set();
  }
}

function savePinned(pinned: Set<string>) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PINNED_APPS_KEY, JSON.stringify(Array.from(pinned)));
  } catch {
    // localStorage might be disabled (private mode, quota) — degrade silently
  }
}

function usePinnedApps() {
  const [pinned, setPinned] = useState<Set<string>>(new Set());
  useEffect(() => {
    setPinned(loadPinned());
  }, []);
  const toggle = useCallback((slug: string) => {
    setPinned((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      savePinned(next);
      return next;
    });
  }, []);
  return { pinned, toggle };
}

function pillFromParam(value: string | null): Pill {
  if (!value) return "all";
  const upper = value.toUpperCase() as AppListStatusFilter;
  return STATUS_FILTER_TO_PILL[upper] ?? "all";
}

/**
 * The apps registry list: the paged server walk, the filter axes it does
 * not own (status pill, team, project, sort) mirrored into the URL, the
 * per-browser pin set, row selection, and the three bulk mutations.
 * The data half of AppsListScreen.
 */
export function useAppsList() {
  const [viewMode, setViewMode] = useViewToggle("astrolift_view_apps", "card");
  const searchParams = useSearchParams();
  const { can } = useMyPermissions();
  const canDeploy = can("app.deploy");

  // URL params seed the initial filter state so a shared link arrives
  // pre-filtered. Read once, on mount: the effect below owns the query
  // string from then on. The search term and page size are the
  // controller's business (?apps-q=, ?apps-size=).
  const [pill, setPill] = useState<Pill>(() => pillFromParam(searchParams.get("status")));
  const [teamSlug, setTeamSlug] = useState(() => searchParams.get("team") ?? "");
  const [projectSlug, setProjectSlug] = useState(() => searchParams.get("project") ?? "");
  const [sortBy, setSortBy] = useState<AppsListSortKey>("CREATED_DESC");

  const variables = useMemo(
    () => ({
      includeFreshness: true,
      status: PILL_TO_STATUS_FILTER[pill],
      teamSlug: teamSlug || null,
      projectSlug: projectSlug || null,
      sortBy,
    }),
    [pill, teamSlug, projectSlug, sortBy]
  );

  const table = useCursorTable<AstroliftRegisteredApp>({
    query: LIST_APPS_PAGE,
    variables,
    extract: (d) => (d as Resp | undefined)?.astroliftAppsPage,
    searchVariable: "search",
    // This query spells its cursor argument `cursor`; the audit and
    // events pages spell theirs `after`.
    cursorVariable: "cursor",
    urlKey: "apps",
  });

  const { clearFilters: clearSearch, isFiltered, rows } = table;

  // Reflect the filter axes the controller does not own back into the
  // URL, so the page state stays shareable. Mutates the existing query
  // string rather than rebuilding it, because `useCursorTable` writes
  // ?apps-q= / ?apps-size= into the same one — and uses replaceState for
  // the same reason it does: a Next navigation would remount the tree
  // and throw away the cursor stack.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const set = (key: string, value: string) => {
      if (value) params.set(key, value);
      else params.delete(key);
    };
    set("status", pill === "all" ? "" : (PILL_TO_STATUS_FILTER[pill] ?? ""));
    set("team", teamSlug);
    set("project", projectSlug);
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [pill, teamSlug, projectSlug]);

  // #697 — pinned apps sort to the top. This is the one client-side
  // reorder left on the page and it is deliberately scoped to the page
  // in hand: there is no server-side "pinned" axis (the set is a
  // per-browser preference), so an app pinned on page three stays on
  // page three until the operator walks to it.
  const { pinned: pinnedSet, toggle: togglePin } = usePinnedApps();
  const apps = useMemo(() => {
    if (pinnedSet.size === 0) return rows;
    const pins: AstroliftRegisteredApp[] = [];
    const rest: AstroliftRegisteredApp[] = [];
    for (const app of rows) {
      if (pinnedSet.has(app.slug)) pins.push(app);
      else rest.push(app);
    }
    return [...pins, ...rest];
  }, [rows, pinnedSet]);

  // Same controller, pinned rows first: paging, search, sort and state
  // all still come from the server-side walk.
  const pinnedController: CursorTableController<AstroliftRegisteredApp> = {
    ...table,
    rows: apps,
  };

  // #698 — bulk-action selection. Row ids are slugs (not guids) because
  // the bulk mutations are keyed on slug.
  const selection = useRowSelection();
  const [pushSecretsOpen, setPushSecretsOpen] = useState(false);

  const [bulkRollingRestart, rollingRestartState] = useMutation<{
    bulkRollingRestart: BulkOperationResult;
  }>(BULK_ROLLING_RESTART);
  const [bulkPushSecrets, pushSecretsState] = useMutation<{
    bulkPushSecrets: BulkOperationResult;
  }>(BULK_PUSH_SECRETS);
  const [bulkResyncManifest, resyncManifestState] = useMutation<{
    bulkResyncManifest: BulkOperationResult;
  }>(BULK_RESYNC_MANIFEST);

  const bulkBusy =
    rollingRestartState.loading || pushSecretsState.loading || resyncManifestState.loading;

  function reportBulkResult(label: string, result: BulkOperationResult) {
    const total = result.okCount + result.failedCount;
    toast.success(`${label}: ${result.okCount}/${total} apps succeeded`);
    if (result.failedCount > 0) {
      const failedSlugs = result.perApp
        .filter((p) => !p.ok)
        .map((p) => p.appSlug)
        .join(", ");
      toast.error(`${label} failed for: ${failedSlugs}`);
    }
  }

  async function handleRollingRestart(appSlugs: string[]) {
    const { data } = await bulkRollingRestart({
      variables: { input: { appSlugs, environmentName: null } },
    });
    if (data?.bulkRollingRestart) {
      reportBulkResult("Rolling restart", data.bulkRollingRestart);
      selection.clear();
    }
  }

  async function handlePushSecrets(bundleSlug: string, environmentName: string | null) {
    const appSlugs = selection.selectedIds;
    const { data } = await bulkPushSecrets({
      variables: { input: { appSlugs, bundleSlug, environmentName } },
    });
    if (data?.bulkPushSecrets) {
      reportBulkResult("Push secrets", data.bulkPushSecrets);
      selection.clear();
      setPushSecretsOpen(false);
    }
  }

  async function handleResyncManifest(appSlugs: string[]) {
    const { data } = await bulkResyncManifest({
      variables: { input: { appSlugs } },
    });
    if (data?.bulkResyncManifest) {
      reportBulkResult("Resync manifest", data.bulkResyncManifest);
      selection.clear();
    }
  }

  // Filters the controller doesn't own. A pill / team / project filter
  // narrows the result set server-side but leaves `isFiltered` false —
  // that flag tracks the search box — so the "you have no apps" empty
  // state has to be swapped for the "nothing matched" one by hand.
  const narrowed = pill !== "all" || teamSlug !== "" || projectSlug !== "";
  const hasActiveFilters = narrowed || isFiltered;

  const clearFilters = useCallback(() => {
    setPill("all");
    setTeamSlug("");
    setProjectSlug("");
    clearSearch();
  }, [clearSearch]);

  return {
    viewMode,
    setViewMode,
    canDeploy,
    pill,
    setPill,
    sortBy,
    setSortBy,
    narrowed,
    hasActiveFilters,
    clearFilters,
    table,
    pinnedController,
    apps,
    pinnedSet: pinnedSet as ReadonlySet<string>,
    togglePin,
    selection,
    bulkBusy,
    handleRollingRestart,
    handlePushSecrets,
    handleResyncManifest,
    pushSecretsOpen,
    setPushSecretsOpen,
  };
}
