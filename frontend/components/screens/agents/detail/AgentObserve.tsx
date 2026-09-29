"use client";

import { ScrollTextIcon, TerminalIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Panel } from "@/components/panel/Panel";

import { agentTabHref } from "./agent-tabs-model";
import type { useAgentObserve } from "./use-agent-observe";

export type AgentObserveScreenProps = ReturnType<typeof useAgentObserve> & {
  slug: string;
  /** The latest run's log (a container running useAgentTaskLogs); shown when `latest` is set. */
  logs: ReactNode;
};

/**
 * Logs & metrics › Live runs (spec 44 §5.5): this agent's latest run, its
 * log on LogView, following the end while it runs. Older runs are on the
 * Runs tab, each with its own log on the run page. Pure.
 */
export function AgentObserveScreen({
  slug,
  latest,
  loading,
  error,
  onRetry,
  logs,
}: AgentObserveScreenProps) {
  if (latest) return <>{logs}</>;
  return (
    <Panel
      title="Log"
      icon={<ScrollTextIcon className="size-4" />}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <TerminalIcon className="size-5" />,
        title: "No runs yet",
        description:
          "Use Run now above. The newest run's log streams here; every run is on the Runs tab.",
        actionHref: agentTabHref(slug, "runs"),
        actionLabel: "Runs",
      }}
    />
  );
}
