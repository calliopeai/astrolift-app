"use client";

import {
  AlertTriangleIcon,
  ArchiveIcon,
  CopyIcon,
  ExternalLinkIcon,
  FileCodeIcon,
  GitBranchIcon,
  MoreHorizontalIcon,
  PlayCircleIcon,
  RocketIcon,
  ServerCrashIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { type Crumb, ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProvisioningStatus } from "@/graphql/registry/registry.types";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import { appTabHref, appTabs, sectionHref, APP_TAB_SECTIONS } from "./app-tabs-model";

/** What the frame's header shows about the app. */
export interface AppFrameApp {
  id: string;
  slug: string;
  name: string;
  status: ProvisioningStatus;
  /** Ready with a managed hostname: traffic is routing. */
  live: boolean;
  archived: boolean;
  /** Why provisioning failed; shown first, above the tab body. */
  failureReason?: string | null;
  /** The environment the header names, and the cluster it runs on. */
  environment?: { name: string; clusterSlug?: string | null } | null;
  /** Environments beyond the one named. */
  moreEnvironments?: number;
  /** The app's public URL, when it has one. */
  appUrl?: string | null;
  repoUrl?: string | null;
}

/** The deployment the header's Deploy re-runs. */
export interface AppFrameDeploy {
  imageTag: string;
  environmentName: string;
}

export interface AppFrameProps {
  slug: string;
  /** `/apps`; the tab links and crumbs are built under it. */
  basePath?: string;
  /** The current pathname: picks the active tab and a nested detail's crumb. */
  pathname: string;
  /** Null while loading, when the load failed, or when no app has this slug. */
  app: AppFrameApp | null;
  loading?: boolean;
  /** The app query failed and nothing is cached. */
  error?: string | null;
  onRetry?: () => void;

  /** Null with no deploy history: Deploy then links to the environments. */
  latestDeploy: AppFrameDeploy | null;
  /** `app.deploy`: gates the redeploy, as the overview header did. */
  canDeploy: boolean;
  deploying?: boolean;
  onDeploy: () => Promise<unknown> | unknown;
  /** `app.update`: gates archive and restore. */
  canUpdate: boolean;
  archiving?: boolean;
  /** Reports its own outcome (toast); the confirm closes either way. */
  onArchive: () => Promise<unknown> | unknown;
  onRestore: () => Promise<unknown> | unknown;
  /** `app.delete`. */
  canDelete: boolean;
  deleting?: boolean;
  /** Throws to keep the confirm open with the error. */
  onDelete: () => Promise<unknown> | unknown;
  onCopyId: () => void;

  /** The tab body; rendered once the app is found. */
  children: React.ReactNode;
}

type Dot = "ok" | "warn" | "error" | "muted" | "pending";

const DOT: Record<ProvisioningStatus, Dot> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

/** `Apps ▾`: the Apps area's functions, read from the rail's own model (§4.4 rule 7). */
function appsCrumb(): Crumb {
  return areaSwitcher(NAV, "apps", "apps");
}

/** A detail nested under a tab (`/workloads/<slug>`) adds its own last crumb. */
function nestedCrumb(basePath: string, slug: string, pathname: string): string | null {
  const prefix = `${basePath}/${slug}/workloads/`;
  if (!pathname.startsWith(prefix)) return null;
  const rest = pathname.slice(prefix.length).split(/[/?#]/)[0];
  return rest ? decodeURIComponent(rest) : null;
}

/**
 * The app detail frame (spec 44 §4.4, §5.2): `Apps ▾ › <app>`, the name with
 * its status and where it runs, Deploy and `⋯`, then the one row of tabs
 * (Overview · Deployments · Workloads · Logs & metrics · Domains · Secrets ·
 * Access · Settings) over the tab body. A failed app's reason sits first,
 * above the body, on every tab. Pure: the route's hook supplies data and
 * callbacks.
 */
export function AppFrame({
  slug,
  basePath = "/apps",
  pathname,
  app,
  loading = false,
  error,
  onRetry,
  latestDeploy,
  canDeploy,
  deploying = false,
  onDeploy,
  canUpdate,
  archiving = false,
  onArchive,
  onRestore,
  canDelete,
  deleting = false,
  onDelete,
  onCopyId,
  children,
}: AppFrameProps) {
  const t = useTranslations("apps.frame");
  const tTabs = useTranslations("apps.tabs");
  const tCommon = useTranslations("apps.common");
  const tDetail = useTranslations("apps.detail");
  const [confirm, setConfirm] = React.useState<"deploy" | "archive" | "delete" | null>(null);
  const close = (open: boolean) => !open && setConfirm(null);

  const tabs = appTabs(basePath, slug, pathname, (k) => tTabs(k));
  const tabsAriaLabel = tTabs("ariaLabel");
  const appHref = appTabHref(basePath, slug, "overview");
  const pending = loading && !app;

  if (!app) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={[appsCrumb(), { label: pending ? slug : tCommon("notFound") }]}
          title={pending ? <Skeleton className="h-6 w-48" /> : tCommon("notFound")}
          tabs={pending ? tabs : undefined}
          tabsAriaLabel={tabsAriaLabel}
        />
        {pending ? (
          <div className="grid min-w-0 grid-cols-12 gap-4" aria-busy>
            <Skeleton className="col-span-12 h-32 w-full" />
            <Skeleton className="col-span-12 h-48 w-full xl:col-span-6" />
            <Skeleton className="col-span-12 h-48 w-full xl:col-span-6" />
          </div>
        ) : error ? (
          <div
            role="alert"
            className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
          >
            <ServerCrashIcon className="text-danger size-5" aria-hidden />
            <div className="min-w-0 px-6">
              <p className="font-medium">{t("loadError")}</p>
              <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
                {error}
              </p>
            </div>
            {onRetry && (
              <Button size="sm" variant="outline" onClick={onRetry}>
                {t("retry")}
              </Button>
            )}
          </div>
        ) : (
          <EmptyState
            icon={<AlertTriangleIcon className="size-5" />}
            title={tCommon("notFoundSlug", { slug })}
            description={tCommon("notFoundDescription")}
            actionHref="/apps"
            actionLabel={tCommon("backToApps")}
          />
        )}
      </div>
    );
  }

  const nested = nestedCrumb(basePath, app.slug, pathname);
  const crumbs: Crumb[] = nested
    ? [appsCrumb(), { label: app.name, href: appHref }, { label: nested }]
    : [appsCrumb(), { label: app.name }];

  const statusKey = app.archived
    ? "archived"
    : app.status === "ready" && app.live
      ? "live"
      : app.status;
  const dot: Dot = app.archived ? "muted" : (DOT[app.status] ?? "muted");

  const env = app.environment;
  const context = env ? (
    <>
      {env.name}
      {env.clusterSlug && (
        <>
          {" · "}
          <Link
            href={`/clusters/${env.clusterSlug}`}
            title={t("clusterTitle", { cluster: env.clusterSlug })}
            className="hover:text-foreground font-mono underline-offset-2 hover:underline"
          >
            {env.clusterSlug}
          </Link>
        </>
      )}
      {(app.moreEnvironments ?? 0) > 0 && (
        <span className="font-mono">
          {" "}
          {t("moreEnvironments", { count: app.moreEnvironments ?? 0 })}
        </span>
      )}
    </>
  ) : undefined;

  const settingsSections = APP_TAB_SECTIONS.settings ?? [];
  const settingsSection = (id: string) =>
    sectionHref(
      basePath,
      app.slug,
      "settings",
      settingsSections.find((s) => s.id === id) ?? settingsSections[0]
    );

  const primaryAction = latestDeploy ? (
    canDeploy ? (
      <Button size="sm" onClick={() => setConfirm("deploy")} disabled={deploying}>
        <RocketIcon className="size-4" />
        {tDetail("actions.deploy")}
      </Button>
    ) : null
  ) : (
    <Button size="sm" asChild>
      <Link href={settingsSection("environments")}>
        <RocketIcon className="size-4" />
        {tDetail("actions.deploy")}
      </Link>
    </Button>
  );

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon" variant="ghost" className="size-8" aria-label={t("menu.label")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        {app.appUrl && (
          <DropdownMenuItem asChild>
            <a href={app.appUrl} target="_blank" rel="noreferrer">
              <ExternalLinkIcon className="size-4" />
              {t("menu.openApp")}
            </a>
          </DropdownMenuItem>
        )}
        {app.repoUrl && (
          <DropdownMenuItem asChild>
            <a href={app.repoUrl} target="_blank" rel="noreferrer">
              <GitBranchIcon className="size-4" />
              {t("menu.openRepo")}
            </a>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem asChild>
          <Link href={settingsSection("configuration")}>
            <FileCodeIcon className="size-4" />
            {t("menu.editConfig")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onCopyId}>
          <CopyIcon className="size-4" />
          {t("menu.copyId")}
        </DropdownMenuItem>
        {(canUpdate || canDelete) && <DropdownMenuSeparator />}
        {canUpdate &&
          (app.archived ? (
            <DropdownMenuItem onSelect={() => void onRestore()} disabled={archiving}>
              <PlayCircleIcon className="size-4" />
              {t("menu.restore")}
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onSelect={() => setConfirm("archive")} disabled={archiving}>
              <ArchiveIcon className="size-4" />
              {t("menu.archive")}
            </DropdownMenuItem>
          ))}
        {canDelete && (
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => setConfirm("delete")}
            disabled={deleting}
          >
            <Trash2Icon className="size-4" />
            {t("menu.delete")}
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={crumbs}
        title={<span title={app.name}>{app.name}</span>}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm">
            <StatusDot status={dot} />
            {t(`status.${statusKey}`)}
          </span>
        }
        context={context}
        primaryAction={primaryAction}
        menu={menu}
        tabs={tabs}
        tabsAriaLabel={tabsAriaLabel}
      />

      {app.status === "failed" && app.failureReason && (
        <div role="alert" className="border-destructive/40 bg-destructive/5 rounded-md border p-3">
          <p className="text-destructive text-xs font-semibold">{t("failed")}</p>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {app.failureReason}
          </p>
        </div>
      )}

      {children}

      {latestDeploy && (
        <ConfirmDialog
          open={confirm === "deploy"}
          onOpenChange={close}
          title={tDetail("headerDeploy.title", {
            tag: latestDeploy.imageTag,
            env: latestDeploy.environmentName,
          })}
          description={tDetail("headerDeploy.description")}
          confirmLabel={tDetail("headerDeploy.confirm")}
          onConfirm={onDeploy}
        />
      )}
      <ConfirmDialog
        open={confirm === "archive"}
        onOpenChange={close}
        title={t("archive.title", { name: app.name })}
        description={t("archive.description")}
        confirmLabel={t("archive.confirm")}
        onConfirm={async () => {
          await onArchive();
        }}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        onOpenChange={close}
        title={tDetail("delete.title", { slug: app.slug })}
        description={tDetail("delete.description")}
        confirmLabel={tDetail("delete.confirm")}
        destructive
        onConfirm={onDelete}
      />
    </div>
  );
}
