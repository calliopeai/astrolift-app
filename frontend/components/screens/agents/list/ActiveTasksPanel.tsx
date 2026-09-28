"use client";

import { BotIcon, MonitorPlayIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { VncViewer } from "@/components/observability/VncViewer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { AgentTask, useActiveAgentTasks } from "./use-agent-tasks";

export type ActiveTasksPanelProps = ReturnType<typeof useActiveAgentTasks>;

// A task is watchable only while RUNNING on a VNC-capable pod with a
// published relay path.
const canWatch = (t: AgentTask) => t.status === "running" && t.vncEnabled && Boolean(t.vncUrl);

/** Active tab — running agent tasks, with a "Watch live" VNC theater. */
export function ActiveTasksPanel({ tasks, loading }: ActiveTasksPanelProps) {
  // The task whose live session is open in the theater modal.
  const [watching, setWatching] = React.useState<AgentTask | null>(null);

  if (loading && tasks.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (tasks.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<BotIcon className="size-5" />}
            title="No active agent tasks"
            description="No agent tasks are currently running. Use the Dispatch tab to launch a task."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Started</TableHead>
              <TableHead className="text-right">Live</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-mono text-xs">
                  <Link
                    href={`/agents/runs/${encodeURIComponent(t.id)}`}
                    className="hover:text-[var(--brand-primary)] hover:underline"
                  >
                    {t.id}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge variant="default">{t.status}</Badge>
                </TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {t.startedAt ?? "—"}
                </TableCell>
                <TableCell className="text-right">
                  {canWatch(t) && (
                    <Button size="sm" variant="outline" onClick={() => setWatching(t)}>
                      <MonitorPlayIcon className="size-4" />
                      Watch live
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>

      <Dialog open={watching !== null} onOpenChange={(open) => !open && setWatching(null)}>
        <DialogContent className="max-w-4xl sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Live agent session</DialogTitle>
            <DialogDescription className="font-mono text-xs">{watching?.id}</DialogDescription>
          </DialogHeader>
          {watching && <VncViewer vncPath={watching.vncUrl} />}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
