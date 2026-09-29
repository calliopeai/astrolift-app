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
import { formatRelativeAge } from "@/lib/format";
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
export function runModeLabel(runFamily: string, runMode: string): string {
  const family = titleCase(runFamily);
  const mode = titleCase(runMode);
  return !mode || mode === family ? family : `${family} · ${mode}`;
}

/** The header's one status: running, paused, last run failed, never run, or ready. */
export function agentStatus(agent: AgentFrameAgent): { dot: Dot; label: string } {
  if (agent.runningCount > 0) return { dot: "pending", label: `${agent.runningCount} running` };
  if (agent.runPaused) return { dot: "muted", label: "Paused" };
  if (agent.lastRunStatus && FAILED.has(agent.lastRunStatus.toLowerCase()))
    return { dot: "error", label: "Last run failed" };
  if (!agent.lastRunAt) return { dot: "muted", label: "Never run" };
  return { dot: "ok", label: "Ready" };
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
  const [runOpen, setRunOpen] = React.useState(false);
  const tabs = agentTabs(slug, pathname);
  const agentsCrumb: Crumb = areaSwitcher(NAV, "agents", "agents");
  const pending = loading && !agent;

  if (!agent) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={[agentsCrumb, { label: pending ? slug : "Not found" }]}
          title={pending ? <Skeleton className="h-6 w-48" /> : "Agent not found"}
          tabs={pending ? tabs : undefined}
          tabsAriaLabel="Agent sections"
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
              <p className="font-medium">Could not load this agent</p>
              <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
                {error}
              </p>
            </div>
            {onRetry && (
              <Button size="sm" variant="outline" onClick={onRetry}>
                Retry
              </Button>
            )}
          </div>
        ) : (
          <EmptyState
            icon={<AlertTriangleIcon className="size-5" />}
            title={`No agent with slug ${slug}`}
            description="It may have been deregistered, belong to a different organization, or you may not have permission to view it."
            actionHref="/agents"
            actionLabel="Back to agents"
          />
        )}
      </div>
    );
  }

  const status = agentStatus(agent);
  const mode = runModeLabel(agent.runFamily, agent.runMode);

  const context = (
    <>
      {mode}
      {model && <> · {model}</>}
      {clusterSlug && (
        <>
          {" · "}
          <Link
            href={`/clusters/${clusterSlug}`}
            title={`Open cluster ${clusterSlug} in Admin`}
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
      Run now
    </Button>
  ) : null;

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon" variant="ghost" className="size-8" aria-label="More actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        {agent.sourceUrl && (
          <DropdownMenuItem asChild>
            <a href={agent.sourceUrl} target="_blank" rel="noreferrer">
              <GitBranchIcon className="size-4" />
              Open repository
            </a>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem asChild>
          <Link href={agentTabHref(agent.slug, "configuration")}>
            <SlidersHorizontalIcon className="size-4" />
            Edit configuration
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onCopyId}>
          <CopyIcon className="size-4" />
          Copy agent ID
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild variant="destructive">
          <Link href={agentSectionHref(agent.slug, "settings", "danger-zone")}>
            <Trash2Icon className="size-4" />
            Archive or delete
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
        tabsAriaLabel="Agent sections"
      />

      {failedRun && (
        <div role="alert" className="border-destructive/40 bg-destructive/5 rounded-md border p-3">
          <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p className="text-destructive text-xs font-semibold">
              Last run failed
              {agent.lastRunAt && (
                <span
                  className="text-muted-foreground font-mono font-normal"
                  title={agent.lastRunAt}
                >
                  {" "}
                  {formatRelativeAge(agent.lastRunAt)}
                </span>
              )}
            </p>
            {failedRun.id && (
              <Link
                href={`/agents/runs/${encodeURIComponent(failedRun.id)}`}
                className="text-primary text-xs font-medium hover:underline"
              >
                Open run
              </Link>
            )}
          </div>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {failedRun.reason || "The run reported no reason."}
          </p>
        </div>
      )}

      {children}

      {canRun && (
        <Sheet open={runOpen} onOpenChange={setRunOpen}>
          <SheetContent className="flex flex-col">
            <SheetHeader>
              <SheetTitle className="[overflow-wrap:anywhere]">Run {agent.name} now</SheetTitle>
              <SheetDescription>
                Starts one run with the agent&apos;s saved configuration. It shows under Runs with a
                live status.
              </SheetDescription>
            </SheetHeader>
            <dl className="flex min-w-0 flex-col gap-3 px-4 text-sm">
              <div className="min-w-0">
                <dt className="text-muted-foreground text-xs">Run mode</dt>
                <dd className="font-mono">{mode}</dd>
              </div>
              {clusterSlug && (
                <div className="min-w-0">
                  <dt className="text-muted-foreground text-xs">Cluster</dt>
                  <dd className="font-mono break-all">{clusterSlug}</dd>
                </div>
              )}
            </dl>
            <SheetFooter className="mt-auto flex-row justify-end gap-2">
              <Button variant="outline" onClick={() => setRunOpen(false)}>
                Cancel
              </Button>
              <Button
                disabled={dispatching}
                onClick={async () => {
                  if (await onRun()) setRunOpen(false);
                }}
              >
                <PlayIcon className="size-4" />
                {dispatching ? "Starting…" : "Run now"}
              </Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
