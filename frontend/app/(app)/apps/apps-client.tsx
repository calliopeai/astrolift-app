"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertCircleIcon,
  ExternalLinkIcon,
  GitBranchIcon,
  KeyIcon,
  PlusIcon,
  RefreshCcwIcon,
  RefreshCwIcon,
  RocketIcon,
  RotateCcwIcon,
  SearchIcon,
  StarIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { ViewToggle } from "@/components/ViewToggle";
import { useViewToggle } from "@/hooks/use-view-toggle";
import { useListControls } from "@/hooks/use-list-controls";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
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
  ProvisioningStatus,
} from "@/graphql/registry/registry.types";
import { useDebounce } from "@/hooks/use-debounce";
import { cn } from "@/lib/utils";

import { AppFreshnessRow } from "./components/AppFreshnessRow";

interface Resp {
  astroliftAppsPage: AstroliftRegisteredAppPage;
}

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

// Filter pill identifiers — the union is the user-facing axis (matches
// the health pulse + a synthetic "in-flight" bucket). The pill-to-server
// mapping lives in `PILL_TO_STATUS_FILTER`.
type Pill = "all" | "ok" | "degraded" | "stale" | "never_deployed";

const PILL_ORDER: Pill[] = ["all", "ok", "degraded", "stale", "never_deployed"];

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

const PAGE_SIZE = 50;

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

export function AppsClient() {
  const t = useTranslations("apps.list");
  const [viewMode, setViewMode] = useViewToggle("astrolift_view_apps", "card");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // URL params seed the initial filter state so a shared link arrives
  // pre-filtered. We treat the URL as the source of truth for filter
  // state and round-trip user input through router.replace so the
  // browser back/forward buttons still work as expected.
  const initialSearch = searchParams.get("q") ?? "";
  const initialPill = pillFromParam(searchParams.get("status"));
  const initialTeam = searchParams.get("team") ?? "";
  const initialProject = searchParams.get("project") ?? "";

  const [rawSearch, setRawSearch] = useState(initialSearch);
  const debouncedSearch = useDebounce(rawSearch, 200);
  const [pill, setPill] = useState<Pill>(initialPill);
  const [teamSlug, setTeamSlug] = useState(initialTeam);
  const [projectSlug, setProjectSlug] = useState(initialProject);
  const searchRef = useRef<HTMLInputElement>(null);

  // Reflect filter state back into the URL whenever it changes — this
  // is what makes the page state shareable. Skips replace when the URL
  // already matches so the router doesn't churn on first paint.
  useEffect(() => {
    const next = new URLSearchParams();
    if (debouncedSearch.trim()) next.set("q", debouncedSearch.trim());
    if (pill !== "all") next.set("status", PILL_TO_STATUS_FILTER[pill] ?? "");
    if (teamSlug) next.set("team", teamSlug);
    if (projectSlug) next.set("project", projectSlug);
    const target = next.toString();
    const current = searchParams.toString();
    if (target === current) return;
    const url = target ? `${pathname}?${target}` : pathname;
    router.replace(url, { scroll: false });
  }, [debouncedSearch, pill, teamSlug, projectSlug, pathname, router, searchParams]);

  const queryVariables = useMemo(() => {
    const status = PILL_TO_STATUS_FILTER[pill];
    return {
      includeFreshness: true,
      limit: PAGE_SIZE,
      search: debouncedSearch.trim() || null,
      status: status,
      teamSlug: teamSlug || null,
      projectSlug: projectSlug || null,
    };
  }, [debouncedSearch, pill, teamSlug, projectSlug]);

  const { data, loading, error, refetch, fetchMore } = useQuery<Resp>(LIST_APPS_PAGE, {
    variables: queryVariables,
    notifyOnNetworkStatusChange: true,
  });

  const page = data?.astroliftAppsPage;
  const rawApps: AstroliftRegisteredApp[] = useMemo(() => page?.items ?? [], [page]);
  const totalCount = page?.totalCount ?? 0;
  const nextCursor = page?.nextCursor ?? null;

  // #697 — pinned apps sort to the top of the grid.
  const { pinned: pinnedSet, toggle: togglePin } = usePinnedApps();

  // #698 — bulk-action selection. Tracks slugs (not ids) because the
  // bulk mutations are keyed on slug.
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());
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
    rollingRestartState.loading ||
    pushSecretsState.loading ||
    resyncManifestState.loading;

  const toggleSelect = useCallback((slug: string) => {
    setSelectedSlugs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedSlugs(new Set()), []);

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

  async function handleRollingRestart() {
    const appSlugs = Array.from(selectedSlugs);
    const { data } = await bulkRollingRestart({
      variables: { input: { appSlugs, environmentName: null } },
    });
    if (data?.bulkRollingRestart) {
      reportBulkResult("Rolling restart", data.bulkRollingRestart);
      clearSelection();
    }
  }

  async function handlePushSecrets(bundleSlug: string, environmentName: string | null) {
    const appSlugs = Array.from(selectedSlugs);
    const { data } = await bulkPushSecrets({
      variables: { input: { appSlugs, bundleSlug, environmentName } },
    });
    if (data?.bulkPushSecrets) {
      reportBulkResult("Push secrets", data.bulkPushSecrets);
      clearSelection();
      setPushSecretsOpen(false);
    }
  }

  async function handleResyncManifest() {
    const appSlugs = Array.from(selectedSlugs);
    const { data } = await bulkResyncManifest({
      variables: { input: { appSlugs } },
    });
    if (data?.bulkResyncManifest) {
      reportBulkResult("Resync manifest", data.bulkResyncManifest);
      clearSelection();
    }
  }
  // #695 — client-side sort dropdown. Default is the backend's
  // `created_at desc` (so it matches the cursor pagination); other
  // options re-order the currently-loaded page. True cross-page sort
  // needs a backend `sort_by` parameter — filed as a follow-up; most
  // orgs fit in one page anyway.
  const [sortKey, setSortKey] = useState<"recent" | "deployed" | "name">("recent");
  const apps: AstroliftRegisteredApp[] = useMemo(() => {
    let sorted = rawApps;
    if (sortKey === "deployed") {
      sorted = [...rawApps].sort((a, b) => {
        const ta = a.lastDeployedAt ? Date.parse(a.lastDeployedAt) : 0;
        const tb = b.lastDeployedAt ? Date.parse(b.lastDeployedAt) : 0;
        return tb - ta;
      });
    } else if (sortKey === "name") {
      sorted = [...rawApps].sort((a, b) => a.name.localeCompare(b.name));
    }
    if (pinnedSet.size === 0) return sorted;
    const pins: AstroliftRegisteredApp[] = [];
    const rest: AstroliftRegisteredApp[] = [];
    for (const a of sorted) {
      if (pinnedSet.has(a.slug)) pins.push(a);
      else rest.push(a);
    }
    return [...pins, ...rest];
  }, [rawApps, pinnedSet, sortKey]);

  const ctrl = useListControls({
    data: apps,
    searchFn: (app) =>
      [app.name, app.slug, app.teamSlug, app.projectSlug, app.sourceKind].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, sort) => {
      if (sort.key === "name") {
        const cmp = a.name.localeCompare(b.name);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "status") {
        const cmp = a.provisioningStatus.localeCompare(b.provisioningStatus);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "createdAt") {
        const ta = a.createdAt ? Date.parse(a.createdAt) : 0;
        const tb = b.createdAt ? Date.parse(b.createdAt) : 0;
        return sort.dir === "asc" ? ta - tb : tb - ta;
      }
      return 0;
    },
  });

  // `/` global shortcut focuses the search input — but only when the
  // user isn't already typing into a form control / contenteditable,
  // and no modifier key is held (so it doesn't intercept browser
  // shortcuts).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) {
          return;
        }
      }
      e.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onLoadMore = useCallback(() => {
    if (!nextCursor) return;
    void fetchMore({
      variables: { ...queryVariables, cursor: nextCursor },
      updateQuery: (prev, { fetchMoreResult }) => {
        if (!fetchMoreResult) return prev;
        const prevPage = prev.astroliftAppsPage;
        const nextPage = fetchMoreResult.astroliftAppsPage;
        return {
          astroliftAppsPage: {
            ...nextPage,
            // Merge dedupes by id in case a row appears in both pages
            // (the cursor seek key is stable so this should never
            // happen in practice, but defensive merging avoids React
            // key collisions if it does).
            items: dedupeApps([...(prevPage?.items ?? []), ...nextPage.items]),
          },
        };
      },
    }).catch(() => {
      // Swallowed: a failed `fetchMore` leaves the existing page in
      // place; the error banner re-renders from the parent query.
    });
  }, [fetchMore, nextCursor, queryVariables]);

  const clearFilters = useCallback(() => {
    setRawSearch("");
    setPill("all");
    setTeamSlug("");
    setProjectSlug("");
  }, []);

  const hasActiveFilters =
    debouncedSearch.trim() !== "" ||
    pill !== "all" ||
    teamSlug !== "" ||
    projectSlug !== "";

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      actions={
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onChange={setViewMode} />
          <Can permission="app.create">
            <Button asChild>
              <Link href="/apps/new">
                <PlusIcon className="size-4" />
                {t("register")}
              </Link>
            </Button>
          </Can>
        </div>
      }
    >
      {error && (
        <div
          role="alert"
          className="border-destructive/40 bg-destructive/10 text-destructive flex flex-col gap-2 rounded-md border p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-start gap-2">
            <AlertCircleIcon className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0">
              <p className="font-medium">{t("errorBanner.title")}</p>
              <p className="text-destructive/90 mt-0.5 text-xs break-words">{error.message}</p>
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="border-destructive/40 text-destructive hover:bg-destructive/15 hover:text-destructive shrink-0 self-start sm:self-auto"
            onClick={() => {
              void refetch();
            }}
          >
            <RefreshCcwIcon className="size-3.5" />
            {t("errorBanner.retry")}
          </Button>
        </div>
      )}

      {/* Triage controls: search on top, status pills below. Pills wrap on
          narrow viewports so the row never overflows. */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative max-w-md flex-1">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              ref={searchRef}
              type="search"
              value={rawSearch}
              onChange={(e) => setRawSearch(e.target.value)}
              placeholder={t("search.placeholder")}
              aria-label={t("search.ariaLabel")}
              className="pr-12 pl-8"
            />
            <kbd
              aria-hidden="true"
              className="border-border bg-muted text-muted-foreground pointer-events-none absolute top-1/2 right-2 hidden h-5 -translate-y-1/2 items-center rounded border px-1.5 font-mono text-2xs sm:inline-flex"
            >
              /
            </kbd>
          </div>
          {/* #695 — client-side sort dropdown. Pinned apps always
              float to the top regardless of sort key. */}
          <label className="text-muted-foreground inline-flex items-center gap-2 text-xs">
            <span>Sort</span>
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
              className="border-border bg-background h-8 rounded-md border px-2 text-xs"
              aria-label="Sort apps"
            >
              <option value="recent">Recently registered</option>
              <option value="deployed">Last deployed</option>
              <option value="name">Name (A→Z)</option>
            </select>
          </label>
        </div>
        <div
          className="flex flex-wrap items-center gap-1.5"
          role="group"
          aria-label={t("filters.ariaLabel")}
        >
          {PILL_ORDER.map((p) => {
            const isActive = pill === p;
            return (
              <button
                key={p}
                type="button"
                onClick={() => setPill(p)}
                aria-pressed={isActive}
                className={cn(
                  "border-border focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none",
                  isActive
                    ? "bg-foreground text-background border-foreground"
                    : "bg-background text-muted-foreground hover:text-foreground hover:bg-accent/40"
                )}
              >
                <span>{t(`filters.${p}`)}</span>
              </button>
            );
          })}
          {hasActiveFilters && (
            <button
              type="button"
              onClick={clearFilters}
              className="border-border text-muted-foreground hover:text-foreground hover:bg-accent/40 ml-1 inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium transition-colors"
            >
              {t("noMatch.clear")}
            </button>
          )}
        </div>
        {totalCount > 0 && (
          <p className="text-muted-foreground text-xs">
            {t("count", { shown: ctrl.totalFiltered, total: totalCount })}
          </p>
        )}
      </div>

      {/* #698 — bulk-action toolbar. Shows when at least one app is
          selected. Each action fans out server-side; the toast reports
          aggregate okCount/total plus a separate destructive toast
          listing failed slugs. */}
      {selectedSlugs.size > 0 && (
        <Can permission="app.deploy">
          <div className="bg-accent/30 border-border flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm">
            <Badge variant="secondary">{selectedSlugs.size} selected</Badge>
            <Button
              size="sm"
              variant="outline"
              onClick={handleRollingRestart}
              disabled={bulkBusy}
            >
              <RotateCcwIcon className="size-3.5" />
              Rolling restart
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setPushSecretsOpen(true)}
              disabled={bulkBusy}
            >
              <KeyIcon className="size-3.5" />
              Push secrets
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={handleResyncManifest}
              disabled={bulkBusy}
            >
              <RefreshCwIcon className="size-3.5" />
              Resync manifest
            </Button>
            <Button size="sm" variant="ghost" onClick={clearSelection} className="ml-auto">
              <XIcon className="size-3.5" />
              Clear
            </Button>
          </div>
        </Can>
      )}

      {loading && !data ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : apps.length === 0 && !hasActiveFilters ? (
        <Card className="border-dashed">
          <CardContent className="p-8">
            <EmptyState
              icon={<RocketIcon className="size-5" />}
              title={t("empty.title")}
              description={t("empty.description")}
              actionHref="/apps/new"
              actionLabel={t("empty.action")}
            />
          </CardContent>
        </Card>
      ) : apps.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="p-8">
            <EmptyState
              icon={<SearchIcon className="size-5" />}
              title={t("noMatch.title")}
              description={t("noMatch.description")}
              secondary={
                <Button size="sm" variant="outline" onClick={clearFilters}>
                  {t("noMatch.clear")}
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <ListControls controls={ctrl} searchPlaceholder="Filter loaded apps…" hideSearch={false} />
          {viewMode === "list" && (
            <div className="flex items-center gap-3 px-4 py-1">
              <div className="w-4 shrink-0" />
              <div className="flex min-w-0 flex-1 items-center gap-4">
                <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                  Name
                </SortableHeader>
              </div>
              <SortableHeader sortKey="status" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                Status
              </SortableHeader>
              <div className="w-24 shrink-0" />
            </div>
          )}
          <div className={viewMode === "card" ? "grid gap-4 md:grid-cols-2 lg:grid-cols-3" : "flex flex-col gap-1"}>
            {ctrl.rows.map((app) => {
              const isPinned = pinnedSet.has(app.slug);
              const isSelected = selectedSlugs.has(app.slug);

              // ── List row ─────────────────────────────────────────────
              if (viewMode === "list") {
                return (
                  <Link key={app.id} href={`/apps/${app.slug}`} className="contents">
                    <div className="hover:bg-accent/50 flex items-center gap-3 rounded-md border px-4 py-2.5 transition-colors">
                      <StatusDot status={statusDot[app.provisioningStatus]} />
                      <div className="min-w-0 flex-1">
                        <span className="truncate font-medium">{app.name}</span>
                        <span className="text-muted-foreground ml-2 font-mono text-xs">{app.slug}</span>
                      </div>
                      <Badge variant="outline" className="shrink-0 text-xs">
                        {app.provisioningStatus}
                      </Badge>
                      {app.latestDeployment && (
                        <span className="text-muted-foreground shrink-0 text-xs">
                          {app.latestDeployment.imageTag}
                        </span>
                      )}
                      <ExternalLinkIcon className="text-muted-foreground size-3.5 shrink-0" />
                    </div>
                  </Link>
                );
              }

              // ── Card ──────────────────────────────────────────────────
              return (
              <Link key={app.id} href={`/apps/${app.slug}`} className="contents">
                <Card className="hover:bg-accent/30 group relative transition-colors">
                  {/* #698 — multi-select checkbox. Same stop-propagation
                      pattern as the pin button so a checkbox click
                      never navigates into the app. */}
                  <label
                    onClick={(e) => e.stopPropagation()}
                    className="absolute top-3 left-3 z-10 inline-flex cursor-pointer items-center"
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={(e) => {
                        e.stopPropagation();
                        toggleSelect(app.slug);
                      }}
                      aria-label={`Select ${app.name}`}
                    />
                  </label>
                  {/* #697 — Pin / unpin toggle. Positioned absolutely so
                      it can sit inside the card without breaking the
                      <Link> parent's whole-card click target. The
                      button stops both default + propagation so a
                      click on the star doesn't navigate to the app. */}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      togglePin(app.slug);
                    }}
                    className="text-muted-foreground hover:text-warning-fg absolute top-3 right-12 z-10 size-7 rounded-md p-1 transition-colors"
                    title={isPinned ? "Unpin app" : "Pin to top"}
                    aria-pressed={isPinned}
                  >
                    <StarIcon
                      className={isPinned ? "size-4 fill-warning text-warning-fg" : "size-4"}
                      aria-hidden
                    />
                    <span className="sr-only">{isPinned ? "Unpin app" : "Pin to top"}</span>
                  </button>
                  <CardContent className="flex flex-col gap-3 p-5 pl-9">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <h3 className="truncate text-lg font-semibold">{app.name}</h3>
                        <p className="text-muted-foreground font-mono text-xs">
                          {app.teamSlug}/{app.projectSlug}/{app.slug}
                        </p>
                      </div>
                      <StatusDot status={statusDot[app.provisioningStatus]} />
                    </div>

                    {app.description && (
                      <p className="text-muted-foreground line-clamp-2 text-sm">{app.description}</p>
                    )}

                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      {app.sourceRepo && (
                        <Badge variant="outline" className="gap-1">
                          <GitBranchIcon className="size-3" />
                          {app.sourceRepo}
                        </Badge>
                      )}
                      <Badge variant="secondary">{app.sourceKind}</Badge>
                      <Badge variant={app.isActive ? "default" : "secondary"}>
                        {app.provisioningStatus}
                      </Badge>
                      {/* #696 — live preview count badge. Hidden when 0
                          to keep the card tight; visible badge tells
                          operators "this app has N previews up right
                          now without leaving the list to find out". */}
                      {app.activePreviewCount > 0 && (
                        <Badge variant="outline" className="gap-1">
                          {app.activePreviewCount} preview
                          {app.activePreviewCount === 1 ? "" : "s"}
                        </Badge>
                      )}
                    </div>

                    <AppFreshnessRow
                      pulse={app.healthPulse}
                      latestDeployment={app.latestDeployment}
                      lastDeployedAt={app.lastDeployedAt ?? null}
                    />

                    <div className="text-muted-foreground flex items-center justify-between text-xs">
                      <span>
                        {t("branchLabel")} <span className="font-mono">{app.deployBranch}</span>
                      </span>
                      <span className="group-hover:text-foreground inline-flex items-center gap-1">
                        {t("open")} <ExternalLinkIcon className="size-3" />
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </Link>
              );
            })}
          </div>
          {nextCursor && (
            <div className="flex justify-center pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={onLoadMore}
                disabled={loading}
              >
                {loading ? t("loadMore.loading") : t("loadMore.label")}
              </Button>
            </div>
          )}
        </>
      )}

      <PushSecretsDialog
        open={pushSecretsOpen}
        onOpenChange={setPushSecretsOpen}
        appCount={selectedSlugs.size}
        busy={bulkBusy}
        onSubmit={handlePushSecrets}
      />
    </PageShell>
  );
}

// #698 — push-secrets dialog. Bundle slug is required; environment
// name is optional (null fans the bundle out to every environment the
// bundle is bound to on each app).
function PushSecretsDialog({
  open,
  onOpenChange,
  appCount,
  busy,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  appCount: number;
  busy: boolean;
  onSubmit: (bundleSlug: string, environmentName: string | null) => Promise<void>;
}) {
  const [bundleSlug, setBundleSlug] = useState("");
  const [environmentName, setEnvironmentName] = useState("");

  useEffect(() => {
    if (!open) {
      setBundleSlug("");
      setEnvironmentName("");
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Push secrets to {appCount} app{appCount === 1 ? "" : "s"}</DialogTitle>
          <DialogDescription>
            Project the named secret bundle onto every selected app. Leave environment blank to fan out to every environment the bundle is bound to.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!bundleSlug.trim()) return;
            await onSubmit(bundleSlug.trim(), environmentName.trim() || null);
          }}
          className="space-y-3"
        >
          <div className="space-y-1.5">
            <Label htmlFor="bundle-slug">Bundle slug</Label>
            <Input
              id="bundle-slug"
              value={bundleSlug}
              onChange={(e) => setBundleSlug(e.target.value)}
              placeholder="shared-prod"
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="environment-name">Environment (optional)</Label>
            <Input
              id="environment-name"
              value={environmentName}
              onChange={(e) => setEnvironmentName(e.target.value)}
              placeholder="prod"
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !bundleSlug.trim()}>
              {busy ? "Pushing..." : `Push to ${appCount} app${appCount === 1 ? "" : "s"}`}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function dedupeApps(rows: AstroliftRegisteredApp[]): AstroliftRegisteredApp[] {
  const seen = new Set<string>();
  const out: AstroliftRegisteredApp[] = [];
  for (const row of rows) {
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    out.push(row);
  }
  return out;
}
