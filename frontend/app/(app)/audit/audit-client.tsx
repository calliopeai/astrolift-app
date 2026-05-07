"use client";

import { useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, ScrollTextIcon, XCircleIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_AUDIT_EVENTS } from "@/graphql/operations/operations.queries";
import type { AstroliftAuditEvent } from "@/graphql/operations/operations.types";

interface Resp {
  astroliftAuditEvents: AstroliftAuditEvent[];
}

const decisionStyles: Record<string, { icon: React.ReactNode; cls: string }> = {
  ALLOW: {
    icon: <CheckCircle2Icon className="size-3" />,
    cls: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  },
  DENY: {
    icon: <XCircleIcon className="size-3" />,
    cls: "bg-red-500/15 text-red-700 dark:text-red-300",
  },
  UNKNOWN: {
    icon: null,
    cls: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300",
  },
};

export function AuditClient() {
  const [actionFilter, setActionFilter] = React.useState("");
  const [decisionFilter, setDecisionFilter] = React.useState<string>("");

  const { data, loading } = useQuery<Resp>(LIST_AUDIT_EVENTS, {
    variables: {
      limit: 200,
      action: actionFilter || null,
      decision: decisionFilter || null,
    },
    pollInterval: 5000,
  });

  const list = data?.astroliftAuditEvents ?? [];

  return (
    <PageShell
      title="Audit log"
      description="Every permission decision and every state-changing mutation, append-only. Tenant-scoped at the database layer."
    >
      <div className="flex flex-wrap gap-3">
        <Input
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          placeholder="Filter by action (e.g. team.create)"
          className="max-w-xs"
        />
        <Select
          value={decisionFilter || "ALL"}
          onValueChange={(v) => setDecisionFilter(v === "ALL" ? "" : v)}
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Decision" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">Any decision</SelectItem>
            <SelectItem value="ALLOW">ALLOW</SelectItem>
            <SelectItem value="DENY">DENY</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ScrollTextIcon className="size-5" />}
                title="No audit events yet"
                description="Events appear here as soon as someone makes a mutation against the API."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Decision</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((row) => {
                  const style =
                    decisionStyles[row.decision] ?? decisionStyles.UNKNOWN;
                  return (
                    <TableRow key={row.id}>
                      <TableCell className="font-mono text-xs whitespace-nowrap">
                        {new Date(row.occurredAt).toLocaleString()}
                      </TableCell>
                      <TableCell>
                        <div className="text-sm">
                          {row.actorDisplay || row.actorKind}
                        </div>
                        <div className="text-muted-foreground text-xs">
                          {row.actorKind}
                          {row.actorId ? ` · ${row.actorId}` : ""}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono text-xs">
                          {row.action}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm">
                        {row.targetKind ? (
                          <div className="font-mono text-xs">
                            {row.targetKind}
                            {row.targetSlug ? `:${row.targetSlug}` : ""}
                            {row.targetId ? ` (${row.targetId})` : ""}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge
                          className={style.cls + " gap-1 px-2 py-0.5 text-xs"}
                          variant="secondary"
                        >
                          {style.icon}
                          {row.decision}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
