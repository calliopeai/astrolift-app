"use client";

import { ChevronRightIcon, Loader2Icon, SearchXIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionList, type DefinitionListItem } from "@/components/ui/definition-list";
import { formatRelativeAge } from "@/lib/format";

/**
 * Standing list→detail pattern (#1106): the shared chrome for a single
 * entity's drill-in view. Mirrors the agent-run detail (#1105) so every
 * "click a row → open its detail" surface reads the same — breadcrumb title,
 * a status + relative-age subtitle, an Overview definition list, and a slot
 * for entity-specific cards (output, logs, related resources).
 *
 * It owns the three view states so each caller stays thin: a centered spinner
 * while the first fetch is in flight, an EmptyState when the entity resolves
 * to null (including the deep-link-past-the-list-window case for surfaces that
 * reuse a list query in the absence of a singular backend query), and the
 * loaded layout otherwise.
 */

type Dot = "ok" | "warn" | "error" | "muted" | "pending";

// Union of the run/resource status vocabularies across the platform (job runs,
// task runs, command runs, preview environments). Unknown values degrade to a
// muted dot rather than throwing, so a new backend status renders gracefully.
const STATUS_DOT: Record<string, Dot> = {
  running: "pending",
  in_progress: "pending",
  provisioning: "pending",
  building: "pending",
  deploying: "pending",
  queued: "warn",
  pending: "warn",
  active: "ok",
  ready: "ok",
  completed: "ok",
  succeeded: "ok",
  healthy: "ok",
  failed: "error",
  errored: "error",
  timed_out: "error",
  superseded: "muted",
  cancelled: "muted",
  canceled: "muted",
  torn_down: "muted",
  expired: "muted",
  unknown: "muted",
};

export function titleCase(value: string): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

export function DetailStatusBadge({ status }: { status: string }) {
  const dot = STATUS_DOT[status.toLowerCase()] ?? "muted";
  return (
    <Badge variant={dot === "error" ? "destructive" : "secondary"} className="gap-1.5">
      <StatusDot status={dot} />
      {titleCase(status)}
    </Badge>
  );
}

export function DetailTimestamp({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span className="text-muted-foreground">—</span>;
  return <span title={iso}>{formatRelativeAge(iso)}</span>;
}

export interface EntityDetailShellProps {
  /** First fetch in flight and no data yet → centered spinner. */
  loading: boolean;
  /** Resolved to no entity → EmptyState. Ignored while `loading`. */
  notFound: boolean;
  /** Crumb linking back to the originating list. */
  breadcrumb: { label: string; href: string };
  /** Heading shown after the crumb (e.g. a short id or resource name). */
  heading: React.ReactNode;
  /** Raw status string → status badge in the subtitle. Omit when the entity
   *  has no single status axis (e.g. an environment). */
  status?: string | null;
  /** Shown as "created <relative age>" in the subtitle when present. */
  createdAt?: string | null;
  /** Right-aligned page actions. */
  actions?: React.ReactNode;
  /** Entity noun for the not-found copy, e.g. "job run". */
  notFoundLabel: string;
  /** Primary field grid rendered inside the Overview card. */
  overview: DefinitionListItem[];
  /** Entity-specific extra cards (output, logs, related resources). */
  children?: React.ReactNode;
}

export function EntityDetailShell({
  loading,
  notFound,
  breadcrumb,
  heading,
  status,
  createdAt,
  actions,
  notFoundLabel,
  overview,
  children,
}: EntityDetailShellProps) {
  if (loading && notFound) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  if (notFound) {
    return (
      <PageShell title={`${titleCase(notFoundLabel)} not found`}>
        <EmptyState
          icon={<SearchXIcon className="size-5" />}
          title={`${titleCase(notFoundLabel)} not found`}
          description={`This ${notFoundLabel} may not exist, may have aged out of the recent list, or you may not have access to it.`}
          actionHref={breadcrumb.href}
          actionLabel={`Back to ${breadcrumb.label}`}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-2">
          <Link href={breadcrumb.href} className="text-muted-foreground hover:text-foreground">
            {breadcrumb.label}
          </Link>
          <ChevronRightIcon className="text-muted-foreground size-4" />
          <span>{heading}</span>
        </span>
      }
      description={
        status || createdAt ? (
          <span className="flex flex-wrap items-center gap-2">
            {status ? <DetailStatusBadge status={status} /> : null}
            {createdAt ? (
              <span className="text-muted-foreground text-xs">
                created <DetailTimestamp iso={createdAt} />
              </span>
            ) : null}
          </span>
        ) : undefined
      }
      actions={actions}
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Overview</CardTitle>
          </CardHeader>
          <CardContent>
            <DefinitionList items={overview} />
          </CardContent>
        </Card>
        {children}
      </div>
    </PageShell>
  );
}
