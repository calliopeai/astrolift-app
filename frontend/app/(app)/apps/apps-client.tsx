"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertCircleIcon,
  ExternalLinkIcon,
  GitBranchIcon,
  PlusIcon,
  RefreshCcwIcon,
  RocketIcon,
  SearchIcon,
  StarIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
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
  const apps: AstroliftRegisteredApp[] = useMemo(() => {
    if (pinnedSet.size === 0) return rawApps;
    const pins: AstroliftRegisteredApp[] = [];
    const rest: AstroliftRegisteredApp[] = [];
    for (const a of rawApps) {
      if (pinnedSet.has(a.slug)) pins.push(a);
      else rest.push(a);
    }
    return [...pins, ...rest];
  }, [rawApps, pinnedSet]);

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
        <Can permission="app.create">
          <Button asChild>
            <Link href="/apps/new">
              <PlusIcon className="size-4" />
              {t("register")}
            </Link>
          </Button>
        </Can>
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
        <div className="relative max-w-md">
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
            className="border-border bg-muted text-muted-foreground pointer-events-none absolute top-1/2 right-2 hidden h-5 -translate-y-1/2 items-center rounded border px-1.5 font-mono text-[10px] sm:inline-flex"
          >
            /
          </kbd>
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
            {t("count", { shown: apps.length, total: totalCount })}
          </p>
        )}
      </div>

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
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {apps.map((app) => {
              const isPinned = pinnedSet.has(app.slug);
              return (
              <Link key={app.id} href={`/apps/${app.slug}`} className="contents">
                <Card className="hover:bg-accent/30 group relative transition-colors">
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
                    className="text-muted-foreground hover:text-amber-500 absolute top-3 right-12 z-10 size-7 rounded-md p-1 transition-colors"
                    title={isPinned ? "Unpin app" : "Pin to top"}
                    aria-pressed={isPinned}
                  >
                    <StarIcon
                      className={isPinned ? "size-4 fill-amber-500 text-amber-500" : "size-4"}
                      aria-hidden
                    />
                    <span className="sr-only">{isPinned ? "Unpin app" : "Pin to top"}</span>
                  </button>
                  <CardContent className="flex flex-col gap-3 p-5">
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
    </PageShell>
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
