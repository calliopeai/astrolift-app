"use client";

import {
  AlertTriangleIcon,
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
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { Can } from "@/components/Can";
import {
  DataTable,
  DataTablePagination,
  DataTableToolbar,
  type Column,
  type EmptyStateSpec,
  type RowSelection,
} from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { ViewToggle } from "@/components/ViewToggle";
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
import type { AppsListSortKey } from "@/graphql/__generated__/schema";
import type { AstroliftRegisteredApp, ProvisioningStatus } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import { AppFreshnessRow } from "./AppFreshnessRow";
import type { Pill, useAppsList } from "./use-apps-list";

export type AppsListScreenProps = ReturnType<typeof useAppsList>;

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

const PILL_ORDER: Pill[] = ["all", "ok", "degraded", "stale", "never_deployed"];

/**
 * The one sort control (#1232).
 *
 * This page used to stack three: a `<select>` that re-ordered the loaded
 * page, a `sortFn` inside `useListControls`, and sortable column headers
 * visible only in list mode — all three client-side over whichever rows
 * happened to be fetched, so every one of them was wrong at the page
 * boundary. `astroliftAppsPage` has taken a server-side `sortBy` since
 * #729 and keys its cursor to the active sort, so the select now rides
 * in the controller's `variables`: changing it restarts the walk at page
 * one, which is what a re-sorted result set needs.
 *
 * It stays a select rather than `Column.sortKey` headers because
 * DEPLOYED_DESC has no column to hang off and the card view has no
 * headers at all.
 */
const SORT_OPTIONS: { value: AppsListSortKey; label: string }[] = [
  { value: "CREATED_DESC", label: "Recently registered" },
  { value: "DEPLOYED_DESC", label: "Last deployed" },
  { value: "NAME_ASC", label: "Name (A→Z)" },
];

/**
 * DataTable stretches the row's link across the whole row (an ::after on
 * the first cell), and that overlay paints above the un-positioned cells
 * beside it. The selection checkbox and the pin toggle have to be lifted
 * back on top of it or the only thing a click in those cells can do is
 * navigate.
 */
const INTERACTIVE_CELLS =
  "[&>td:has([role=checkbox])]:relative [&>td:has([role=checkbox])]:z-10 " +
  "[&>td:last-child]:relative [&>td:last-child]:z-10";

/** #697 — pin / unpin toggle, shared by the card and the list row. */
function PinButton({
  pinned,
  onToggle,
  className,
}: {
  pinned: boolean;
  onToggle: () => void;
  className?: string;
}) {
  const label = pinned ? "Unpin app" : "Pin to top";
  return (
    <button
      type="button"
      // Both card and row sit inside a link; stop the click before it
      // navigates.
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onToggle();
      }}
      className={cn(
        "text-muted-foreground hover:text-warning-fg size-7 rounded-md p-1 transition-colors",
        className
      )}
      title={label}
      aria-pressed={pinned}
    >
      <StarIcon className={pinned ? "fill-warning text-warning-fg size-4" : "size-4"} aria-hidden />
      <span className="sr-only">{label}</span>
    </button>
  );
}

/** The apps registry: card grid or list, filters, pins, and bulk actions. */
export function AppsListScreen({
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
  pinnedSet,
  togglePin,
  selection,
  bulkBusy,
  handleRollingRestart,
  handlePushSecrets,
  handleResyncManifest,
  pushSecretsOpen,
  setPushSecretsOpen,
}: AppsListScreenProps) {
  const t = useTranslations("apps.list");
  const surfaceRef = useRef<HTMLDivElement>(null);
  const clearSearch = table.clearFilters;

  // `/` global shortcut focuses the search input — but only when the
  // user isn't already typing into a form control / contenteditable,
  // and no modifier key is held (so it doesn't intercept browser
  // shortcuts). DataTable owns the search box, so there is no ref to
  // hand out: the toolbar is the first thing it renders, which makes
  // the surface's first <input> the search field in both view modes.
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
      const box = surfaceRef.current?.querySelector("input");
      if (!box) return;
      e.preventDefault();
      box.focus();
      box.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const empty: EmptyStateSpec = narrowed
    ? {
        icon: <SearchIcon className="size-5" />,
        title: t("noMatch.title"),
        description: t("noMatch.description"),
      }
    : {
        icon: <RocketIcon className="size-5" />,
        title: t("empty.title"),
        description: t("empty.description"),
        actionHref: "/apps/new",
        actionLabel: t("empty.action"),
      };

  const emptyFiltered = {
    title: "No apps match that search",
    description:
      "The search runs on the server across the app name, slug, description, and repo. Try a shorter term, or clear it to see the rest of the registry.",
  };

  const filterControls = (
    <>
      <label className="text-muted-foreground inline-flex items-center gap-2 text-xs">
        <span>Sort</span>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as AppsListSortKey)}
          className="border-border bg-background h-8 rounded-md border px-2 text-xs"
          aria-label="Sort apps"
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
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
    </>
  );

  // #698 — each action fans out server-side; the toast reports the
  // aggregate okCount/total plus a separate destructive toast listing
  // the failed slugs.
  const renderBulkActions = (sel: RowSelection) => (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => handleRollingRestart(sel.selectedIds)}
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
        onClick={() => handleResyncManifest(sel.selectedIds)}
        disabled={bulkBusy}
      >
        <RefreshCwIcon className="size-3.5" />
        Resync manifest
      </Button>
    </>
  );

  const columns: Column<AstroliftRegisteredApp>[] = [
    {
      id: "name",
      header: "Name",
      cell: (app) => (
        <span className="flex items-center gap-2">
          <StatusDot status={statusDot[app.provisioningStatus]} />
          <span className="min-w-0">
            <span className="block truncate font-medium">{app.name}</span>
            <span className="text-muted-foreground block font-mono text-xs">
              {app.teamSlug}/{app.projectSlug}/{app.slug}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      width: "w-40",
      cell: (app) => (
        <Badge variant="outline" className="text-xs">
          {app.provisioningStatus}
        </Badge>
      ),
    },
    {
      id: "image",
      header: "Image",
      width: "w-52",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (app) => app.latestDeployment?.imageTag || "—",
    },
    {
      id: "pin",
      header: <span className="sr-only">Pin</span>,
      width: "w-12",
      align: "right",
      cell: (app) => (
        <PinButton pinned={pinnedSet.has(app.slug)} onToggle={() => togglePin(app.slug)} />
      ),
    },
  ];

  const cardBody = (() => {
    switch (table.state) {
      case "loading":
        // Skeletons keep the grid's geometry, so nothing jumps when the
        // cards land.
        return (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={`app-skeleton-${i}`} className="h-44 w-full" />
            ))}
          </div>
        );

      case "error":
        return (
          <div
            role="alert"
            className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
          >
            <AlertTriangleIcon className="text-danger size-5" />
            <div>
              <p className="font-medium">{t("errorBanner.title")}</p>
              <p className="text-muted-foreground mt-1 max-w-md text-sm">
                {table.error?.message ?? "The request failed."}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={table.retry}>
              <RefreshCcwIcon className="size-3.5" />
              {t("errorBanner.retry")}
            </Button>
          </div>
        );

      case "emptyFiltered":
        return (
          <EmptyState
            icon={<SearchIcon className="size-5" />}
            title={emptyFiltered.title}
            description={emptyFiltered.description}
            // Same words and same effect as DataTable's own
            // filtered-empty state, so the two views cannot disagree.
            secondary={
              <Button size="sm" variant="outline" onClick={clearSearch}>
                Clear search
              </Button>
            }
          />
        );

      case "empty":
        return <EmptyState {...empty} />;

      case "ready":
        return (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {apps.map((app) => {
              const isPinned = pinnedSet.has(app.slug);
              const isSelected = selection.isSelected(app.slug);
              return (
                <Link key={app.slug} href={`/apps/${app.slug}`} className="contents">
                  <Card className="hover:bg-accent/30 group relative transition-colors">
                    {/* #698 — multi-select checkbox. Same stop-propagation
                        pattern as the pin button so a checkbox click
                        never navigates into the app. */}
                    {canDeploy && (
                      <label
                        onClick={(e) => e.stopPropagation()}
                        className="absolute top-3 left-3 z-10 inline-flex cursor-pointer items-center"
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={(e) => {
                            e.stopPropagation();
                            selection.toggle(app.slug);
                          }}
                          aria-label={`Select ${app.name}`}
                        />
                      </label>
                    )}
                    <PinButton
                      pinned={isPinned}
                      onToggle={() => togglePin(app.slug)}
                      className="absolute top-3 right-12 z-10"
                    />
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
        );
    }
  })();

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
      <div ref={surfaceRef} className="flex flex-col gap-3">
        {viewMode === "list" ? (
          <DataTable
            label="Apps"
            controller={pinnedController}
            columns={columns}
            getRowId={(app) => app.slug}
            rowHref={(app) => `/apps/${app.slug}`}
            rowClassName={() => INTERACTIVE_CELLS}
            searchPlaceholder={t("search.placeholder")}
            toolbar={filterControls}
            // Selection drives the bulk bar, which has nothing to offer
            // an operator who cannot deploy.
            selection={canDeploy ? selection : undefined}
            bulkActions={renderBulkActions}
            empty={empty}
            emptyFiltered={emptyFiltered}
          />
        ) : (
          // The card view is the same controller wearing different
          // clothes: DataTable's chrome (toolbar, bulk bar, pagination)
          // around a grid instead of a table, because cards are not rows
          // and a one-column table of cards would be a lie.
          <>
            <DataTableToolbar controller={table} searchPlaceholder={t("search.placeholder")}>
              {filterControls}
            </DataTableToolbar>

            {canDeploy && selection.selectedCount > 0 && (
              <div className="bg-muted/50 flex flex-wrap items-center gap-3 rounded-md border px-3 py-2">
                <span className="text-sm font-medium tabular-nums">
                  {selection.selectedCount} selected
                </span>
                <Button variant="ghost" size="sm" onClick={selection.clear}>
                  Clear
                </Button>
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  {renderBulkActions(selection)}
                </div>
              </div>
            )}

            {/* Cards persist across a refetch rather than blanking, so
                fade them while they answer the previous question. */}
            <div
              className={cn("transition-opacity", table.isStale && "opacity-60")}
              aria-busy={table.isStale || undefined}
            >
              {cardBody}
            </div>

            <DataTablePagination controller={table} />
          </>
        )}
      </div>

      <PushSecretsDialog
        open={pushSecretsOpen}
        onOpenChange={setPushSecretsOpen}
        appCount={selection.selectedCount}
        busy={bulkBusy}
        onSubmit={handlePushSecrets}
      />
    </PageShell>
  );
}

export interface PushSecretsDialogProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  appCount: number;
  busy: boolean;
  onSubmit: (bundleSlug: string, environmentName: string | null) => Promise<void>;
}

// #698 — push-secrets dialog. Bundle slug is required; environment
// name is optional (null fans the bundle out to every environment the
// bundle is bound to on each app).
export function PushSecretsDialog({
  open,
  onOpenChange,
  appCount,
  busy,
  onSubmit,
}: PushSecretsDialogProps) {
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
          <DialogTitle>
            Push secrets to {appCount} app{appCount === 1 ? "" : "s"}
          </DialogTitle>
          <DialogDescription>
            Project the named secret bundle onto every selected app. Leave environment blank to fan
            out to every environment the bundle is bound to.
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
