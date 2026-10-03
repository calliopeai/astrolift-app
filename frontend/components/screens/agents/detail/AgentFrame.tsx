"use client";

import {
  AlertTriangleIcon,
  CopyIcon,
  GitBranchIcon,
  MoreHorizontalIcon,
  PlayIcon,
  ServerCrashIcon,
  SlidersHorizontalIcon,
  Trash2Icon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
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
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import { agentSectionHref, agentTabHref, agentTabs } from "./agent-tabs-model";

/** What the frame's header shows about the agent: the fleet row's steady fields. */
export type AgentFrameAgent = Pick<
  AstroliftAgentListItem,
  | "id"
  | "slug"
  | "name"
  | "runFamily"
  | "runMode"
  | "runPaused"
  | "runningCount"
  | "lastRunStatus"
  | "lastRunAt"
  | "sourceRepo"
  | "sourceUrl"
>;

export interface AgentFrameProps {
  slug: string;
  /** The current pathname: picks the active tab. */
  pathname: string;
  /** Null while loading, when the load failed, or when no agent has this slug. */
  agent: AgentFrameAgent | null;
  loading?: boolean;
  /** The fleet query failed and nothing is cached. */
  error?: string | null;
  onRetry?: () => void;
  /** How it reaches its model ("managed model", "API key"); absent while unknown. */
  model?: string | null;
  /** The cluster it runs on, linked to Admin. */
  clusterSlug?: string | null;
  /** The latest run, when the last one failed: its reason leads the page. */
  failedRun?: { id: string; reason: string | null } | null;
  /** `agent.dispatch`, the grant the server checks on a run. */
  canRun: boolean;
  dispatching?: boolean;
  /** Starts one run; resolves true once it started, and the sheet closes. */
  onRun: () => Promise<boolean>;
  onCopyId: () => void;
  /** The tab body; rendered once the agent is found. */
  children: React.ReactNode;
}

type Dot = "ok" | "warn" | "error" | "muted" | "pending";

const FAILED = new Set(["failed", "timed_out", "error"]);

function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/** `Task · Once`; one word when the mode repeats the family (`Service`). */
export function runModeLabel(
  runFamily: string,
  runMode: string,
  translate?: (key: string) => string
): string {
  const label = (value: string) =>
    translate
      ? ["task", "service", "once", "loop", "schedule", "trigger", "persistent"].includes(value)
        ? translate(`modes.${value}`)
        : value
      : titleCase(value);
  const family = label(runFamily);
  const mode = label(runMode);
  return !mode || mode === family ? family : `${family} · ${mode}`;
}

/** The header's one status: running, paused, last run failed, never run, or ready. */
export function agentStatus(
  agent: AgentFrameAgent,
  translate?: (key: string, values?: { count: number }) => string
): { dot: Dot; label: string } {
  const label = (key: string, fallback: string, values?: { count: number }) =>
    translate ? translate(key, values) : fallback;
  if (agent.runningCount > 0)
    return {
      dot: "pending",
      label: label("status.running", `${agent.runningCount} running`, {
        count: agent.runningCount,
      }),
    };
  if (agent.runPaused) return { dot: "muted", label: label("status.paused", "Paused") };
  if (agent.lastRunStatus && FAILED.has(agent.lastRunStatus.toLowerCase()))
    return { dot: "error", label: label("status.failed", "Last run failed") };
  if (!agent.lastRunAt) return { dot: "muted", label: label("status.never", "Never run") };
  return { dot: "ok", label: label("status.ready", "Ready") };
}

/**
 * The agent detail frame (spec 44 §4.4, §5.2): `Agents ▾ › <agent>`, the
 * name with its status and context (run mode · model · cluster), Run now and
 * `⋯`, then the one row of tabs (Overview · Runs · Configuration ·
 * Skills & tools · Logs & metrics · Secrets · Access · Settings) over the
 * tab body. When the last run failed, its reason sits first, above the body,
 * on every tab. Run now takes no fields, so it opens a sheet (§5.4). Pure:
 * the route's hook supplies data and callbacks.
 */
export function AgentFrame({
  slug,
  pathname,
  agent,
  loading = false,
  error,
  onRetry,
  model,
  clusterSlug,
  failedRun,
  canRun,
  dispatching = false,
  onRun,
  onCopyId,
  children,
}: AgentFrameProps) {
  const t = useTranslations("agentFrame");
  const format = useFormatters();
  const [runOpen, setRunOpen] = React.useState(false);
  const tabs = agentTabs(slug, pathname, (key) => t(`tabs.${key}`));
  const agentsCrumb: Crumb = { ...areaSwitcher(NAV, "agents", "agents"), label: t("agents") };
  const pending = loading && !agent;

  if (!agent) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={[agentsCrumb, { label: pending ? slug : t("notFound") }]}
          title={pending ? <Skeleton className="h-6 w-48" /> : t("agentNotFound")}
          tabs={pending ? tabs : undefined}
          tabsAriaLabel={t("sections")}
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
            title={t("noSlug", { slug })}
            description={t("missingDescription")}
            actionHref="/agents"
            actionLabel={t("back")}
          />
        )}
      </div>
    );
  }

  const status = agentStatus(agent, (key, values) => t(key, values));
  const mode = runModeLabel(agent.runFamily, agent.runMode, (key) => t(key));

  const context = (
    <>
      {mode}
      {model && <> · {model}</>}
      {clusterSlug && (
        <>
          {" · "}
          <Link
            href={`/clusters/${clusterSlug}`}
            title={t("openCluster", { slug: clusterSlug })}
            className="hover:text-foreground font-mono underline-offset-2 hover:underline"
          >
            {clusterSlug}
          </Link>
        </>
      )}
    </>
  );

  const primaryAction = canRun ? (
    <Button size="sm" onClick={() => setRunOpen(true)} disabled={dispatching}>
      <PlayIcon className="size-4" />
      {t("runNow")}
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
        {agent.sourceUrl && (
          <DropdownMenuItem asChild>
            <a href={agent.sourceUrl} target="_blank" rel="noreferrer">
              <GitBranchIcon className="size-4" />
              {t("openRepo")}
            </a>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem asChild>
          <Link href={agentTabHref(agent.slug, "configuration")}>
            <SlidersHorizontalIcon className="size-4" />
            {t("editConfig")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onCopyId}>
          <CopyIcon className="size-4" />
          {t("copyId")}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild variant="destructive">
          <Link href={agentSectionHref(agent.slug, "settings", "danger-zone")}>
            <Trash2Icon className="size-4" />
            {t("archive")}
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={[agentsCrumb, { label: agent.name }]}
        title={<span title={agent.name}>{agent.name}</span>}
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
              {t("lastFailed")}
              {agent.lastRunAt && (
                <span
                  className="text-muted-foreground font-mono font-normal"
                  title={agent.lastRunAt}
                >
                  {" "}
                  {Number.isFinite(Date.parse(agent.lastRunAt))
                    ? format.formatRelativeTime(agent.lastRunAt)
                    : t("unknownDate")}
                </span>
              )}
            </p>
            {failedRun.id && (
              <Link
                href={`/agents/runs/${encodeURIComponent(failedRun.id)}`}
                className="text-primary text-xs font-medium hover:underline"
              >
                {t("openRun")}
              </Link>
            )}
          </div>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {failedRun.reason || t("noReason")}
          </p>
        </div>
      )}

      {children}

      {canRun && (
        <Sheet open={runOpen} onOpenChange={setRunOpen}>
          <SheetContent className="flex flex-col">
            <SheetHeader>
              <SheetTitle className="[overflow-wrap:anywhere]">
                {t("runTitle", { name: agent.name })}
              </SheetTitle>
              <SheetDescription>{t("runDescription")}</SheetDescription>
            </SheetHeader>
            <dl className="flex min-w-0 flex-col gap-3 px-4 text-sm">
              <div className="min-w-0">
                <dt className="text-muted-foreground text-xs">{t("runMode")}</dt>
                <dd className="font-mono">{mode}</dd>
              </div>
              {clusterSlug && (
                <div className="min-w-0">
                  <dt className="text-muted-foreground text-xs">{t("cluster")}</dt>
                  <dd className="font-mono break-all">{clusterSlug}</dd>
                </div>
              )}
            </dl>
            <SheetFooter className="mt-auto flex-row justify-end gap-2">
              <Button variant="outline" onClick={() => setRunOpen(false)}>
                {t("cancel")}
              </Button>
              <Button
                disabled={dispatching}
                onClick={async () => {
                  if (await onRun()) setRunOpen(false);
                }}
              >
                <PlayIcon className="size-4" />
                {dispatching ? t("starting") : t("runNow")}
              </Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
