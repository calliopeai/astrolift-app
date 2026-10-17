"use client";

import {
  AppWindowIcon,
  ArchiveIcon,
  ArchiveRestoreIcon,
  ExternalLinkIcon,
  Loader2Icon,
  LockIcon,
  MoreHorizontalIcon,
  PlugIcon,
  RefreshCwIcon,
  SearchXIcon,
  ServerCrashIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PermissionNote } from "@/components/settings/Restricted";
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
import { useFormatters } from "@/lib/i18n/formatters";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import type { BrainConnection } from "../brain-connection";
import { ConnectBrainDialog } from "../ConnectBrainDialog";
import type { BrainCan, FramedBrain } from "./brain-detail-model";
import { brainHref, brainSectionHref, brainTabs } from "./brain-tabs-model";

export interface BrainFrameProps {
  slug: string;
  /** The current pathname: picks the active tab. */
  pathname: string;
  /** Null while loading, when the read failed, or when no layer has this slug. */
  brain: FramedBrain | null;
  loading: boolean;
  /** The read failed; `denied` when the server refused it. */
  error: { message: string; denied: boolean } | null;
  onRetry: () => void;
  /** The slug is an archived semantic layer: restore it to bring it back. */
  archived: { name: string; archivedAt: string | null } | null;
  restoring: boolean;
  onRestore: () => void;
  can: BrainCan;
  connecting: boolean;
  onConnect: () => void;
  connection: BrainConnection | null;
  onDismissConnection: () => void;
  /** The tab body; rendered once the layer is found. */
  children: React.ReactNode;
}

/**
 * A semantic layer's frame (spec 44 §5.2, #2427): `Apps ▾ › Semantic layers
 * › <layer>`, the name with where it is served and who owns it, Connect and
 * `⋯`, then the one row of tabs over the tab body. When the slug is an
 * archived layer the frame offers Restore (restoreApp) instead of tabs; a
 * layer the viewer cannot read and one that does not exist read the same.
 * Pure: the route's hook supplies data and callbacks.
 */
export function BrainFrame({
  slug,
  pathname,
  brain,
  loading,
  error,
  onRetry,
  archived,
  restoring,
  onRestore,
  can,
  connecting,
  onConnect,
  connection,
  onDismissConnection,
  children,
}: BrainFrameProps) {
  const t = useTranslations("brains.frame");
  const tabLabel = useTranslations("brains.tabs");
  const fmt = useFormatters();
  const tabs = brainTabs(slug, pathname, (key) => tabLabel(key));
  const areaCrumb: Crumb = areaSwitcher(NAV, "apps", "brains");
  const listCrumb: Crumb = { label: t("listCrumb"), href: "/brains" };
  const pending = loading && !brain;

  if (!brain) {
    const title = pending
      ? null
      : archived
        ? t("archivedTitle", { name: archived.name })
        : error?.denied
          ? t("deniedTitle")
          : error
            ? t("unavailableTitle")
            : t("notFoundTitle");
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={[areaCrumb, listCrumb, { label: archived?.name ?? slug }]}
          title={pending ? <Skeleton className="h-6 w-48" /> : title}
          tabs={pending ? tabs : undefined}
          tabsAriaLabel={t("sections")}
        />
        {pending ? (
          <div className="grid min-w-0 grid-cols-12 gap-4" aria-busy>
            <Skeleton className="col-span-12 h-32 w-full xl:col-span-6" />
            <Skeleton className="col-span-12 h-32 w-full xl:col-span-6" />
            <Skeleton className="col-span-12 h-48 w-full" />
          </div>
        ) : archived ? (
          <div className="flex max-w-2xl min-w-0 flex-col gap-4 rounded-md border p-6">
            <div className="flex min-w-0 items-start gap-3">
              <ArchiveIcon className="text-muted-foreground mt-0.5 size-5 shrink-0" aria-hidden />
              <div className="min-w-0">
                <p className="font-medium">{t("archivedHeading")}</p>
                <p className="text-muted-foreground mt-1 text-sm">
                  {archived.archivedAt
                    ? t("archivedAt", { at: fmt.formatDateTime(archived.archivedAt) })
                    : t("archivedNoTime")}{" "}
                  {t("archivedDescription")}
                </p>
              </div>
            </div>
            {can.restore ? (
              <div>
                <Button size="sm" onClick={onRestore} disabled={restoring}>
                  {restoring ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <ArchiveRestoreIcon className="size-4" />
                  )}
                  {t("restore")}
                </Button>
              </div>
            ) : (
              <PermissionNote permission="app.update" verb={t("restoring")} />
            )}
          </div>
        ) : error && !error.denied ? (
          <div
            role="alert"
            className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
          >
            <ServerCrashIcon className="text-danger size-5" aria-hidden />
            <div className="min-w-0 px-6">
              <p className="font-medium">{t("loadFailed")}</p>
              <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
                {error.message}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={onRetry}>
              {t("retry")}
            </Button>
          </div>
        ) : (
          <EmptyState
            icon={
              error?.denied ? <LockIcon className="size-5" /> : <SearchXIcon className="size-5" />
            }
            title={error?.denied ? t("deniedTitle") : t("noLayer", { slug })}
            description={error?.denied ? t("deniedDescription") : t("notFoundDescription")}
            actionHref="/brains"
            actionLabel={t("backToList")}
          />
        )}
      </div>
    );
  }

  const served = brain.environments.length > 0;
  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon" variant="ghost" className="size-8" aria-label={t("moreActions")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-52">
        {can.rebuild && (
          <DropdownMenuItem asChild>
            <Link href={brainSectionHref(brain.slug, "settings", "rebuild")}>
              <RefreshCwIcon className="size-4" />
              {t("rebuild")}
            </Link>
          </DropdownMenuItem>
        )}
        {can.host && (
          <DropdownMenuItem asChild>
            <Link href={brainHref(brain.slug, "apps")}>
              <AppWindowIcon className="size-4" />
              {t("hostApp")}
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem asChild>
          <Link href={`/apps/${encodeURIComponent(brain.slug)}`}>
            <ExternalLinkIcon className="size-4" />
            {t("openApp")}
          </Link>
        </DropdownMenuItem>
        {can.archive && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild variant="destructive">
              <Link href={brainSectionHref(brain.slug, "settings", "danger-zone")}>
                <ArchiveIcon className="size-4" />
                {t("archive")}
              </Link>
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={[areaCrumb, listCrumb, { label: brain.name || brain.slug }]}
        title={<span title={brain.name}>{brain.name || brain.slug}</span>}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm">
            <StatusDot status={served ? "ok" : "muted"} />
            {served ? t("served", { count: brain.environments.length }) : t("notServed")}
          </span>
        }
        context={
          <span className="font-mono">
            {brain.slug} · {brain.team || t("none")}/{brain.project || t("none")}
          </span>
        }
        primaryAction={
          <Button
            size="sm"
            onClick={onConnect}
            disabled={!served || connecting}
            title={served ? undefined : t("notServed")}
          >
            <PlugIcon className="size-4" />
            {t("connect")}
          </Button>
        }
        menu={menu}
        tabs={tabs}
        tabsAriaLabel={t("sections")}
      />
      {children}
      <ConnectBrainDialog connection={connection} onDismiss={onDismissConnection} />
    </div>
  );
}
