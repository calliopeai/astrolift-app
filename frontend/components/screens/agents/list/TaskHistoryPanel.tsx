"use client";

import { ClockIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { useAgentTaskHistory } from "./use-agent-tasks";

export type TaskHistoryPanelProps = ReturnType<typeof useAgentTaskHistory>;

/** History tab — completed / terminal agent tasks. */
export function TaskHistoryPanel({ tasks, loading }: TaskHistoryPanelProps) {
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
            icon={<ClockIcon className="size-5" />}
            title="No task history"
            description="No completed agent tasks yet. Tasks will appear here after they finish."
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
              <TableHead>Finished</TableHead>
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
                  <Badge variant={t.status === "completed" ? "default" : "destructive"}>
                    {t.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {t.finishedAt ?? "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
