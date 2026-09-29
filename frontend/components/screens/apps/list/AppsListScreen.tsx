"use client";

import {
  GitBranchIcon,
  KeyIcon,
  PlusIcon,
  RefreshCwIcon,
  RocketIcon,
  RotateCcwIcon,
  StarIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import type { Column, EmptyStateSpec, RowSelection } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { useFormatters } from "@/lib/i18n/formatters";
import { TOPOLOGY_META } from "@/lib/topology";
import { cn } from "@/lib/utils";

import { AppFreshnessRow } from "./AppFreshnessRow";
import { AppKindGlyph } from "./AppKindGlyph";
import { type AppRow, type AppStatusKey, appStatusKey, appsCrumbs } from "./apps-list";

export interface AppsListScreenProps {
  list: ListStateController;
  /** The page on screen, already filtered, sorted, sliced and pinned first. */
  rows: AppRow[];
  /** Apps matching the view, filters and search, across all pages. */
  totalCount: number;
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** Row selection and the bulk bar are for people who can deploy. */
  canDeploy: boolean;
  pinned: ReadonlySet<string>;
  togglePin: (slug: string) => void;
  bulkBusy: boolean;
  /** Each resolves true once the server answered (the hook toasts the outcome). */
  onRollingRestart: (appSlugs: string[]) => Promise<boolean>;
  onPushSecrets: (
    appSlugs: string[],
    bundleSlug: string,
    environmentName: string | null
  ) => Promise<boolean>;
  onResyncManifest: (appSlugs: string[]) => Promise<boolean>;
}

const DOT: Record<AppStatusKey, "ok" | "warn" | "error" | "muted" | "pending"> = {
  live: "ok",
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
  archived: "muted",
};

// The row's link is an ::after overlay stretched across the whole row; a
// tooltip, link or button in a cell has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

/** #697: pin or unpin, shared by the card and the row. Both sit inside a link. */
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
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onToggle();
      }}
      className={cn(
        "text-muted-foreground hover:text-warning-fg focus-visible:ring-ring inline-flex size-7 shrink-0 items-center justify-center rounded-sm transition-colors focus-visible:ring-2 focus-visible:outline-none",
        className
      )}
      title={label}
      aria-label={label}
      aria-pressed={pinned}
    >
      <StarIcon className={pinned ? "fill-warning text-warning-fg size-4" : "size-4"} aria-hidden />
    </button>
  );
}

function AppStatus({ app }: { app: AppRow }) {
  const t = useTranslations("apps.frame");
  const key = appStatusKey(app);
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-sm">
      <StatusDot status={DOT[key]} />
      <span className="truncate">{t(`status.${key}`)}</span>
    </span>
  );
}

function Clusters({ clusters }: { clusters: string[] }) {
  if (clusters.length === 0) return <span className="text-muted-foreground text-xs">none</span>;
  return (
    <span className="flex min-w-0 items-center gap-1 font-mono text-xs">
      <span className="min-w-0 truncate" title={clusters.join(", ")}>
        {clusters[0]}
      </span>
      {clusters.length > 1 && (
        <span className="text-muted-foreground shrink-0">+{clusters.length - 1}</span>
      )}
    </span>
  );
}

function AppCard({
  app,
  pinned,
  onTogglePin,
}: {
  app: AppRow;
  pinned: boolean;
  onTogglePin: () => void;
}) {
  return (
    <div className="bg-card hover:bg-accent/30 flex h-full min-w-0 flex-col gap-3 rounded-md border p-4 transition-colors">
      <div className="flex min-w-0 items-start gap-3">
        <AppKindGlyph topology={app.topology} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold" title={app.name}>
            {app.name}
          </div>
          <div
            className="text-muted-foreground truncate font-mono text-xs"
            title={`${app.teamSlug}/${app.projectSlug}/${app.slug}`}
          >
            {app.teamSlug}/{app.projectSlug}/{app.slug}
          </div>
        </div>
        <PinButton pinned={pinned} onToggle={onTogglePin} className="-mt-1 -mr-1" />
      </div>

      <AppStatus app={app} />

      {app.description && (
        <p className="text-muted-foreground line-clamp-2 text-sm [overflow-wrap:anywhere]">
          {app.description}
        </p>
      )}

      <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
        {app.sourceRepo && (
          <Badge variant="outline" className="max-w-full min-w-0 gap-1 font-mono">
            <GitBranchIcon className="size-3 shrink-0" />
            <span className="min-w-0 truncate">{app.sourceRepo}</span>
          </Badge>
        )}
        {app.clusters.length > 0 && (
          <Badge variant="secondary" className="max-w-full min-w-0 font-mono">
            <Clusters clusters={app.clusters} />
          </Badge>
        )}
        {/* #696: live previews, hidden at zero to keep the card tight. */}
        {app.activePreviewCount > 0 && (
          <Badge variant="outline" className="font-mono">
            {app.activePreviewCount} preview{app.activePreviewCount === 1 ? "" : "s"}
          </Badge>
        )}
      </div>

      <div className="mt-auto flex min-w-0 flex-wrap items-center justify-between gap-2">
        <AppFreshnessRow
          pulse={app.healthPulse}
          latestDeployment={app.latestDeployment}
          lastDeployedAt={app.lastDeployedAt ?? null}
        />
        {app.latestDeployment?.imageTag && (
          <span
            className="text-muted-foreground min-w-0 truncate font-mono text-xs"
            title={app.latestDeployment.imageTag}
          >
            {app.latestDeployment.imageTag}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Apps › Apps (spec 44 §5.1, §4.4): the registry on the shared list, views
 * All · Mine · Failing · Archived, project, status, last-deploy, kind and
 * cluster filters, numbered pages, list or cards, pins and the bulk actions.
 * Pure view; the data half is useAppsList.
 */
export function AppsListScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  canDeploy,
  pinned,
  togglePin,
  bulkBusy,
  onRollingRestart,
  onPushSecrets,
  onResyncManifest,
}: AppsListScreenProps) {
  const t = useTranslations("apps.list");
  const fmt = useFormatters();
  // The selection the push-secrets dialog applies to, held while it is open.
  const [pushTarget, setPushTarget] = React.useState<RowSelection | null>(null);

  const empty: EmptyStateSpec = {
    icon: <RocketIcon className="size-5" />,
    title: t("empty.title"),
    description: t("empty.description"),
    actionHref: "/apps/new",
    actionLabel: "New app",
    learnMoreHref: "/documentation/get-started",
    learnMoreLabel: "Get started",
  };

  const columns: Column<AppRow>[] = [
    {
      id: "app",
      header: "App",
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (app) => (
        <span className="flex min-w-0 items-center gap-2">
          <AppKindGlyph topology={app.topology} className="size-7" />
          <span className="block min-w-0">
            <span className="block truncate font-medium" title={app.name}>
              {app.name}
            </span>
            <span
              className="text-muted-foreground block truncate font-mono text-xs"
              title={`${app.teamSlug}/${app.projectSlug}/${app.slug}`}
            >
              {app.teamSlug}/{app.projectSlug}/{app.slug}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      sortKey: "status",
      cell: (app) => <AppStatus app={app} />,
    },
    {
      id: "kind",
      header: "Kind",
      cell: (app) => (
        <span className="text-muted-foreground text-xs">
          {app.topology ? TOPOLOGY_META[app.topology].label : "none yet"}
        </span>
      ),
    },
    {
      id: "clusters",
      header: "Cluster",
      cellClassName: "max-w-48",
      cell: (app) => <Clusters clusters={app.clusters} />,
    },
    {
      id: "lastDeploy",
      header: "Last deploy",
      sortKey: "deployed",
      cell: (app) => (
        <span className={cn(ABOVE_ROW_LINK, "flex min-w-0 flex-col gap-1")}>
          <AppFreshnessRow
            pulse={app.healthPulse}
            latestDeployment={app.latestDeployment}
            lastDeployedAt={app.lastDeployedAt ?? null}
          />
          {app.latestDeployment?.imageTag && (
            <span
              className="text-muted-foreground block max-w-52 truncate font-mono text-xs"
              title={app.latestDeployment.imageTag}
            >
              {app.latestDeployment.imageTag}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "registered",
      header: "Registered",
      sortKey: "created",
      cell: (app) => (
        <span className="text-muted-foreground font-mono text-xs">
          {app.createdAt ? fmt.formatDateTime(app.createdAt) : "unknown"}
        </span>
      ),
    },
    {
      id: "pin",
      label: "Pin",
      header: <span className="sr-only">Pin</span>,
      width: "w-12",
      align: "right",
      cellClassName: ABOVE_ROW_LINK,
      cell: (app) => (
        <PinButton pinned={pinned.has(app.slug)} onToggle={() => togglePin(app.slug)} />
      ),
    },
  ];

  // #698: the three fan-out actions over the selected apps.
  const bulkActions = (sel: RowSelection) => (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={bulkBusy}
        onClick={async () => {
          if (await onRollingRestart(sel.selectedIds)) sel.clear();
        }}
      >
        <RotateCcwIcon className="size-3.5" />
        Rolling restart
      </Button>
      <Button size="sm" variant="outline" disabled={bulkBusy} onClick={() => setPushTarget(sel)}>
        <KeyIcon className="size-3.5" />
        Push secrets
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulkBusy}
        onClick={async () => {
          if (await onResyncManifest(sel.selectedIds)) sel.clear();
        }}
      >
        <RefreshCwIcon className="size-3.5" />
        Resync manifest
      </Button>
    </>
  );

  return (
    <>
      <ListPage<AppRow>
        header={{
          crumbs: appsCrumbs(),
          title: t("title"),
          primaryAction: (
            <Can permission="app.create">
              <Button size="sm" asChild>
                <Link href="/apps/new">
                  <PlusIcon className="size-4" />
                  New app
                </Link>
              </Button>
            </Can>
          ),
        }}
        list={list}
        label="Apps"
        columns={columns}
        rows={rows}
        getRowId={(app) => app.slug}
        rowHref={(app) => `/apps/${app.slug}`}
        renderCard={(app) => (
          <AppCard
            app={app}
            pinned={pinned.has(app.slug)}
            onTogglePin={() => togglePin(app.slug)}
          />
        )}
        // Selection drives the bulk bar, which has nothing to offer someone who cannot deploy.
        bulkActions={canDeploy ? bulkActions : undefined}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={empty}
        totalCount={totalCount}
      />

      <PushSecretsDialog
        open={pushTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPushTarget(null);
        }}
        appCount={pushTarget?.selectedCount ?? 0}
        busy={bulkBusy}
        onSubmit={async (bundleSlug, environmentName) => {
          if (!pushTarget) return;
          if (await onPushSecrets(pushTarget.selectedIds, bundleSlug, environmentName)) {
            pushTarget.clear();
            setPushTarget(null);
          }
        }}
      />
    </>
  );
}

export interface PushSecretsDialogProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  appCount: number;
  busy: boolean;
  onSubmit: (bundleSlug: string, environmentName: string | null) => Promise<void>;
}

// #698: push-secrets dialog. Bundle slug is required; environment name is
// optional (null fans the bundle out to every environment the bundle is
// bound to on each app).
export function PushSecretsDialog({
  open,
  onOpenChange,
  appCount,
  busy,
  onSubmit,
}: PushSecretsDialogProps) {
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
        {/* The content unmounts on close, so every open starts with blank fields. */}
        <PushSecretsForm
          appCount={appCount}
          busy={busy}
          onSubmit={onSubmit}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function PushSecretsForm({
  appCount,
  busy,
  onSubmit,
  onCancel,
}: Pick<PushSecretsDialogProps, "appCount" | "busy" | "onSubmit"> & { onCancel: () => void }) {
  const [bundleSlug, setBundleSlug] = React.useState("");
  const [environmentName, setEnvironmentName] = React.useState("");
  return (
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
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || !bundleSlug.trim()}>
          {busy ? "Pushing..." : `Push to ${appCount} app${appCount === 1 ? "" : "s"}`}
        </Button>
      </DialogFooter>
    </form>
  );
}
