"use client";

import {
  AlertTriangleIcon,
  BotIcon,
  GitBranchIcon,
} from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { formatRelativeAge } from "@/lib/format";

import { AgentTabs } from "./agent-tabs";
import { useAgent } from "./use-agent";

// Run-family / run-mode label maps — kept in sync with the registry list
// (`agents-client.tsx`). Free `String!` fields on the schema, so we
// title-case unknown values to degrade gracefully.
const RUN_FAMILY_LABELS: Record<string, string> = {
  task: "Task",
  service: "Service",
};
const RUN_MODE_LABELS: Record<string, string> = {
  once: "Once",
  loop: "Loop",
  schedule: "Schedule",
  trigger: "Trigger",
  service: "Service",
};

function titleCase(value: string): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function formatRunMode(runFamily: string, runMode: string): string {
  const family = RUN_FAMILY_LABELS[runFamily.toLowerCase()] ?? titleCase(runFamily);
  const mode = RUN_MODE_LABELS[runMode.toLowerCase()] ?? titleCase(runMode);
  if (!mode || mode === family) return family || "—";
  return `${family} · ${mode}`;
}

// Live-status pill, derived from the list row's own runningCount/runPaused.
// (The volatile agentLiveStatus poll is the registry list's concern; the
// detail header reads the steady fields off the resolved row.)
function LiveStatusBadge({ agent }: { agent: AstroliftAgentListItem }) {
  if (agent.runningCount > 0) {
    return (
      <Badge variant="default" className="gap-1.5">
        <StatusDot status="pending" />
        {agent.runningCount} running
      </Badge>
    );
  }
  if (agent.runPaused) {
    return (
      <Badge variant="outline" className="gap-1.5">
        <StatusDot status="muted" />
        Paused
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-1.5">
      <StatusDot status="muted" />
      Idle
    </Badge>
  );
}

export interface AgentDetailRenderProps {
  agent: AstroliftAgentListItem;
  orgId: string;
}

interface AgentDetailShellProps {
  agentSlug: string;
  /** Renders the active tab's content once the agent has resolved. */
  children: (props: AgentDetailRenderProps) => React.ReactNode;
}

/**
 * Shared chrome for every `/agents/[agentSlug]/<pillar>` page: resolves the
 * agent, renders the identity header (name · run-mode · live-status · repo)
 * and the BROCS `AgentTabs`, then slots the active tab's content. Mirrors how
 * `AppDetailClient` wraps the app-detail surface, but as a render-prop shell
 * so each pillar route owns only its own content.
 */
export function AgentDetailShell({ agentSlug, children }: AgentDetailShellProps) {
  const { agent, loading, notFound, orgId } = useAgent(agentSlug);

  if (loading) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (notFound || !agent) {
    return (
      <PageShell title="Agent not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No agent with slug ${agentSlug}`}
          description="It may have been deregistered, belong to a different organization, or you may not have permission to view it."
          actionHref="/agents?tab=registry"
          actionLabel="Back to agents"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <BotIcon className="text-muted-foreground size-5" />
          </span>
          <span>{agent.name}</span>
          <Badge variant="outline">{formatRunMode(agent.runFamily, agent.runMode)}</Badge>
          <LiveStatusBadge agent={agent} />
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground font-mono text-xs">
            {agent.projectSlug}/{agent.appSlug}/{agent.slug}
          </span>
          {agent.sourceRepo ? (
            agent.sourceUrl ? (
              <a
                href={agent.sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
              >
                <GitBranchIcon className="size-3" />
                {agent.sourceRepo}
              </a>
            ) : (
              <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
                <GitBranchIcon className="size-3" />
                {agent.sourceRepo}
              </span>
            )
          ) : null}
          {agent.lastRunAt ? (
            <span className="text-muted-foreground text-xs" title={agent.lastRunAt}>
              last run {formatRelativeAge(agent.lastRunAt)}
            </span>
          ) : (
            <span className="text-muted-foreground text-xs">never run</span>
          )}
        </span>
      }
    >
      <AgentTabs agentSlug={agent.slug} />
      {children({ agent, orgId })}
    </PageShell>
  );
}
