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
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useRef, useState } from "react";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  ProvisioningStatus,
} from "@/graphql/registry/registry.types";
import { useDebounce } from "@/hooks/use-debounce";
import { cn } from "@/lib/utils";

interface Resp {
  astroliftApps: AstroliftRegisteredApp[];
}

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

type Bucket = "all" | "running" | "failed" | "inflight" | "notDeployed";

const BUCKET_ORDER: Bucket[] = [
  "all",
  "running",
  "failed",
  "inflight",
  "notDeployed",
];

// Derive a triage bucket from the raw provisioning status. Anything outside
// the known set lands in `notDeployed` so the pill counts always add up.
function bucketFor(status: ProvisioningStatus | string | null | undefined): Exclude<Bucket, "all"> {
  switch (status) {
    case "ready":
      return "running";
    case "failed":
      return "failed";
    case "pending":
    case "provisioning":
      return "inflight";
    default:
      return "notDeployed";
  }
}

export function AppsClient() {
  const t = useTranslations("apps.list");
  const { data, loading, error, refetch } = useQuery<Resp>(LIST_APPS, {
    notifyOnNetworkStatusChange: true,
  });
  // Memoize so dependent useMemos don't re-run on every render when Apollo
  // returns the same `data` reference but the `?? []` fallback would
  // otherwise mint a fresh array each time.
  const apps = useMemo(() => data?.astroliftApps ?? [], [data]);

  const [rawSearch, setRawSearch] = useState("");
  const debouncedSearch = useDebounce(rawSearch, 200);
  const [bucket, setBucket] = useState<Bucket>("all");
  const searchRef = useRef<HTMLInputElement>(null);

  // `/` global shortcut focuses the search input — but only when the user
  // isn't already typing into a form control / contenteditable, and no
  // modifier key is held (so it doesn't intercept browser shortcuts).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (
          tag === "INPUT" ||
          tag === "TEXTAREA" ||
          tag === "SELECT" ||
          target.isContentEditable
        ) {
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

  // Counts always reflect the unfiltered list, so operators can see at a
  // glance how many apps fall into each triage bucket regardless of what's
  // currently filtered.
  const counts = useMemo(() => {
    const acc: Record<Bucket, number> = {
      all: apps.length,
      running: 0,
      failed: 0,
      inflight: 0,
      notDeployed: 0,
    };
    for (const app of apps) {
      acc[bucketFor(app.provisioningStatus)] += 1;
    }
    return acc;
  }, [apps]);

  const filtered = useMemo(() => {
    const needle = debouncedSearch.trim().toLowerCase();
    return apps.filter((app) => {
      if (bucket !== "all" && bucketFor(app.provisioningStatus) !== bucket) {
        return false;
      }
      if (!needle) return true;
      const haystack = [app.name, app.slug, app.sourceRepo ?? ""]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [apps, debouncedSearch, bucket]);

  const clearFilters = () => {
    setRawSearch("");
    setBucket("all");
  };

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
              <p className="text-destructive/90 mt-0.5 break-words text-xs">
                {error.message}
              </p>
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
          <SearchIcon className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2" />
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
            className="border-border bg-muted text-muted-foreground pointer-events-none absolute right-2 top-1/2 hidden h-5 -translate-y-1/2 items-center rounded border px-1.5 font-mono text-[10px] sm:inline-flex"
          >
            /
          </kbd>
        </div>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("filters.ariaLabel")}>
          {BUCKET_ORDER.map((b) => {
            const isActive = bucket === b;
            return (
              <button
                key={b}
                type="button"
                onClick={() => setBucket(b)}
                aria-pressed={isActive}
                className={cn(
                  "border-border focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none",
                  isActive
                    ? "bg-foreground text-background border-foreground"
                    : "bg-background text-muted-foreground hover:text-foreground hover:bg-accent/40"
                )}
              >
                <span>{t(`filters.${b}`)}</span>
                <span
                  className={cn(
                    "rounded-full px-1.5 text-[10px] font-semibold tabular-nums",
                    isActive
                      ? "bg-background/15 text-background"
                      : "bg-muted text-foreground/70"
                  )}
                >
                  {counts[b]}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {loading && !data ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : apps.length === 0 ? (
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
      ) : filtered.length === 0 ? (
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
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((app) => (
            <Link
              key={app.id}
              href={`/apps/${app.slug}`}
              className="contents"
            >
              <Card className="hover:bg-accent/30 group transition-colors">
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
                    <p className="text-muted-foreground line-clamp-2 text-sm">
                      {app.description}
                    </p>
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

                  <div className="text-muted-foreground flex items-center justify-between text-xs">
                    <span>
                      {t("branchLabel")}{" "}
                      <span className="font-mono">{app.deployBranch}</span>
                    </span>
                    <span className="group-hover:text-foreground inline-flex items-center gap-1">
                      {t("open")} <ExternalLinkIcon className="size-3" />
                    </span>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </PageShell>
  );
}
