"use client";

import {
  AlertTriangleIcon,
  CopyIcon,
  MoreHorizontalIcon,
  PlayIcon,
  ServerCrashIcon,
  Trash2Icon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

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
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelativeAge } from "@/lib/format";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import { patternLabel, type WorkflowFrameSubject, workflowStatus } from "./workflow-frame-subject";
import { workflowSectionHref, workflowTabHref, workflowTabs } from "./workflow-tabs-model";

export interface WorkflowFrameProps {
  slug: string;
  /** The current pathname: picks the active tab. */
  pathname: string;
  exactDefinitionId?: string | null;
  /** Null while loading, when the load failed, or when nothing has this slug. */
  workflow: WorkflowFrameSubject | null;
  loading?: boolean;
  /** Neither a configured workflow nor a definition loaded, and nothing is cached. */
  error?: string | null;
  onRetry?: () => void;
  /** The latest run, when it failed: its reason leads the page. */
  failedRun?: { href: string; reason: string | null } | null;
  /** The workflows module's run grant (`workflow.trigger`), the one the server checks. */
  canRun: boolean;
  /** The workflow may be deleted by this viewer: the `⋯` menu links its Danger zone. */
  canDelete?: boolean;
  dispatching?: boolean;
  reviewedDefinitionStart?: boolean;
  startDialog?: React.ReactNode;
  /** Starts one run; resolves true once it started, and the sheet closes. */
  onRun: () => Promise<boolean>;
  onCopyId: () => void;
  /** The tab body; rendered once the workflow is found. */
  children: React.ReactNode;
}

/**
 * The workflow detail frame (spec 44 §4.4, §5.2): `Agents ▾ › Workflows ›
 * <workflow>` (Workflows is an Agents function, §4.1), the name with the last
 * run's status and its context (pattern · stages · project), Run and `⋯`,
 * then the one row of tabs (Builder · Runs · Triggers · Settings) over the tab
 * body. When the last run failed, its reason sits first, above the body, on
 * every tab. Run takes no fields, so it opens a sheet (§5.4). Pure: the
 * route's hook supplies data and callbacks.
 */
export function WorkflowFrame({
  slug,
  pathname,
  exactDefinitionId,
  workflow,
  loading = false,
  error,
  onRetry,
  failedRun,
  canRun,
  canDelete = false,
  dispatching = false,
  reviewedDefinitionStart = false,
  startDialog,
  onRun,
  onCopyId,
  children,
}: WorkflowFrameProps) {
  const t = useTranslations("workflowFrame");
  const [runOpen, setRunOpen] = React.useState(false);
  const preserveDefinition = (href: string) =>
    exactDefinitionId
      ? `${href}${href.includes("?") ? "&" : "?"}definitionId=${encodeURIComponent(exactDefinitionId)}`
      : href;
  const tabs = workflowTabs(slug, pathname).map((tab) => ({
    ...tab,
    href: preserveDefinition(tab.href),
  }));
  const areaCrumb: Crumb = areaSwitcher(NAV, "agents", "workflows");
  const listCrumb: Crumb = { label: t("workflows"), href: "/workflows" };
  const pending = loading && !workflow;

  if (!workflow) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={[
            areaCrumb,
            listCrumb,
            { label: pending ? slug : error ? t("unavailableCrumb") : t("notFoundCrumb") },
          ]}
          title={
            pending ? (
              <Skeleton className="h-6 w-48" />
            ) : error ? (
              t("unavailableTitle")
            ) : (
              t("notFoundTitle")
            )
          }
          tabs={pending ? tabs : undefined}
          tabsAriaLabel={t("sections")}
        />
        {pending ? (
          <div className="grid min-w-0 grid-cols-12 gap-4" aria-busy>
            <Skeleton className="col-span-12 h-32 w-full" />
            <Skeleton className="col-span-12 h-64 w-full" />
          </div>
        ) : error ? (
          <div
            role="alert"
            className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
          >
            <ServerCrashIcon className="text-danger size-5" aria-hidden />
            <div className="min-w-0 px-6">
              <p className="font-medium">{t("loadFailed")}</p>
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
            title={t("noWorkflowWithSlug", { slug })}
            description={t("notFoundDescription")}
            actionHref="/workflows"
            actionLabel={t("backToWorkflows")}
          />
        )}
      </div>
    );
  }

  const status = workflowStatus(workflow);
  const pattern = patternLabel(workflow.patternKind);
  const stages =
    workflow.stageCount === null ? null : t("stageCount", { count: workflow.stageCount });

  const context = (
    <>
      {pattern}
      {stages && (
        <>
          {" · "}
          <span className="font-mono">{stages}</span>
        </>
      )}
      {workflow.projectSlug && (
        <>
          {" · "}
          <Link
            href={`/projects/${encodeURIComponent(workflow.projectSlug)}`}
            title={t("openProject", { project: workflow.projectSlug })}
            className="hover:text-foreground font-mono underline-offset-2 hover:underline"
          >
            {workflow.projectSlug}
          </Link>
        </>
      )}
      {workflow.kind === "definition" && ` · ${t("definition")}`}
    </>
  );

  const primaryAction = canRun ? (
    <Button
      size="sm"
      onClick={() => (reviewedDefinitionStart ? void onRun() : setRunOpen(true))}
      disabled={dispatching || !workflow.isEnabled}
      title={workflow.isEnabled ? undefined : t("enableToRun")}
    >
      <PlayIcon className="size-4" />
      {t("run")}
    </Button>
  ) : null;

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon" variant="ghost" className="size-8" aria-label={t("moreActions")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        <DropdownMenuItem asChild>
          <Link href={preserveDefinition(workflowTabHref(workflow.slug, "triggers"))}>
            <ZapIcon className="size-4" />
            {t("editTriggers")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onCopyId}>
          <CopyIcon className="size-4" />
          {t("copyId")}
        </DropdownMenuItem>
        {canDelete && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild variant="destructive">
              <Link
                href={preserveDefinition(
                  workflowSectionHref(workflow.slug, "settings", "danger-zone")
                )}
              >
                <Trash2Icon className="size-4" />
                {t("deleteWorkflow")}
              </Link>
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const failedAt = workflow.lastRun?.startedAt ?? null;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={[areaCrumb, listCrumb, { label: workflow.name }]}
        title={<span title={workflow.name}>{workflow.name}</span>}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm">
            <StatusDot status={status.dot} />
            {status.label}
          </span>
        }
        context={context}
        primaryAction={primaryAction}
        menu={menu}
        tabs={tabs}
        tabsAriaLabel={t("sections")}
      />

      {failedRun && (
        <div role="alert" className="border-destructive/40 bg-destructive/5 rounded-md border p-3">
          <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p className="text-destructive text-xs font-semibold">
              {t("lastRunFailed")}
              {failedAt && (
                <span className="text-muted-foreground font-mono font-normal" title={failedAt}>
                  {" "}
                  {formatRelativeAge(failedAt)}
                </span>
              )}
            </p>
            <Link
              href={failedRun.href}
              className="text-primary text-xs font-medium hover:underline"
            >
              {t("openRun")}
            </Link>
          </div>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {failedRun.reason || t("noReason")}
          </p>
        </div>
      )}

      {children}

      {startDialog}
      {canRun && (
        <Sheet open={runOpen && !reviewedDefinitionStart} onOpenChange={setRunOpen}>
          <SheetContent className="flex flex-col">
            <SheetHeader>
              <SheetTitle className="[overflow-wrap:anywhere]">
                {t("runTitle", { name: workflow.name })}
              </SheetTitle>
              <SheetDescription>{t("runDescription")}</SheetDescription>
            </SheetHeader>
            <dl className="flex min-w-0 flex-col gap-3 px-4 text-sm">
              <div className="min-w-0">
                <dt className="text-muted-foreground text-xs">{t("pattern")}</dt>
                <dd>{pattern}</dd>
              </div>
              {stages && (
                <div className="min-w-0">
                  <dt className="text-muted-foreground text-xs">{t("stages")}</dt>
                  <dd className="font-mono">{workflow.stageCount}</dd>
                </div>
              )}
              {workflow.projectSlug && (
                <div className="min-w-0">
                  <dt className="text-muted-foreground text-xs">{t("project")}</dt>
                  <dd className="font-mono break-all">{workflow.projectSlug}</dd>
                </div>
              )}
            </dl>
            <SheetFooter className="mt-auto flex-row justify-end gap-2">
              <Button variant="outline" onClick={() => setRunOpen(false)}>
                {t("cancel")}
              </Button>
              <Button
                disabled={dispatching || !workflow.isEnabled}
                onClick={async () => {
                  if (await onRun()) setRunOpen(false);
                }}
              >
                <PlayIcon className="size-4" />
                {dispatching ? t("starting") : t("run")}
              </Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
