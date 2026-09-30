"use client";

import { QueryError } from "@/components/QueryError";

import * as React from "react";
import Link from "next/link";
import { ArrowLeftIcon, BotIcon, Loader2Icon, MonitorPlayIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { VncViewer } from "@/components/observability/VncViewer";

import type { AgentVncPopoutState, VncPopoutTask } from "./use-agent-vnc-popout";

export type AgentVncPopoutProps = AgentVncPopoutState;

// A task is watchable only while RUNNING on a VNC-capable pod that has
// published its relay path — mirrors the gallery + relay gate.
function isWatchable(t: VncPopoutTask): boolean {
  return t.status === "running" && t.vncEnabled && Boolean(t.vncUrl);
}

/**
 * Full-height single-session noVNC viewer for the pop-out tab. When the task
 * stops running the viewer flips to a clear "session ended" state instead of
 * leaving a dead canvas mounted.
 */
export function AgentVncPopoutScreen({
  taskId,
  task,
  loading,
  error,
  onRetry,
}: AgentVncPopoutProps) {
  let body: React.ReactNode;
  if (loading && !task) {
    body = (
      <div className="flex flex-1 items-center justify-center">
        <Loader2Icon className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  } else if (error && !task) {
    body = (
      <div className="flex flex-1 items-center justify-center">
        <QueryError title="Could not load session" error={error} onRetry={onRetry} />
      </div>
    );
  } else if (!task) {
    body = (
      <div className="flex flex-1 items-center justify-center">
        <EmptyState
          icon={<BotIcon className="size-5" />}
          title="Agent not found"
          description="This agent task doesn't exist or you don't have access to it."
        />
      </div>
    );
  } else if (!isWatchable(task)) {
    body = (
      <div className="flex flex-1 items-center justify-center">
        <EmptyState
          icon={<BotIcon className="size-5" />}
          title="Session not available"
          description={
            task.status === "running"
              ? "This agent isn't running a VNC-capable session."
              : "This agent is no longer running, so its live session has closed."
          }
          actionHref="/agents?tab=theatre"
          actionLabel="Back to theatre"
        />
      </div>
    );
  } else {
    body = <VncViewer vncPath={task.vncUrl} className="flex-1" />;
  }

  return (
    <div className="flex h-[calc(100svh-3.5rem)] flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <MonitorPlayIcon className="size-4" />
          <h1 className="text-sm font-semibold">Live agent session</h1>
          <span className="text-muted-foreground font-mono text-xs">{taskId}</span>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/agents?tab=theatre">
            <ArrowLeftIcon className="size-3.5" />
            Theatre
          </Link>
        </Button>
      </div>
      {body}
    </div>
  );
}
