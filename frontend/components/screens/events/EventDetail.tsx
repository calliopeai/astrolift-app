"use client";

import { ExternalLinkIcon } from "lucide-react";
import Link from "next/link";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { QueryError } from "@/components/QueryError";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DefinitionListItem } from "@/components/ui/definition-list";

import { eventPayloadText, resolveSourceHref } from "./event-source";
import type { useEventDetail } from "./use-events";

export type EventDetailProps = Omit<ReturnType<typeof useEventDetail>, "error" | "onRetry"> & {
  error?: string | null;
  onRetry?: () => void;
};

/** Event detail (#1106) — full payload + metadata for a single platform event. */
export function EventDetail({ id, event: e, loading, error, onRetry }: EventDetailProps) {
  const sourceHref = e
    ? resolveSourceHref(e.resourceKind ?? "", e.resourceId ?? "", e.payload ?? {})
    : null;
  const sourceLabel = e ? `${e.resourceKind || "?"}:${e.resourceId || "?"}` : "";

  const overview: DefinitionListItem[] = e
    ? [
        {
          term: "Event type",
          description: <span className="font-mono text-xs">{e.eventType}</span>,
        },
        { term: "Occurred", description: <DetailTimestamp iso={e.occurredAt} /> },
        {
          term: "Source",
          description:
            !e.resourceKind && !e.resourceId ? (
              <span className="text-muted-foreground">—</span>
            ) : sourceHref ? (
              <Link
                href={sourceHref}
                className="inline-flex items-center gap-1 font-mono text-xs text-[var(--brand-primary)] hover:underline"
              >
                {sourceLabel} <ExternalLinkIcon className="size-3" />
              </Link>
            ) : (
              <span className="font-mono text-xs">{sourceLabel}</span>
            ),
        },
      ]
    : [];

  if (e?.registeredAppId) {
    overview.push({
      term: "App",
      description: <span className="font-mono text-xs break-all">{e.registeredAppId}</span>,
    });
  }
  if (e?.organizationId) {
    overview.push({
      term: "Organization",
      description: <span className="font-mono text-xs break-all">{e.organizationId}</span>,
    });
  }
  if (e?.teamId) {
    overview.push({
      term: "Team",
      description: <span className="font-mono text-xs break-all">{e.teamId}</span>,
    });
  }
  if (e?.projectId) {
    overview.push({
      term: "Project",
      description: <span className="font-mono text-xs break-all">{e.projectId}</span>,
    });
  }

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!e && !error}
      breadcrumb={{ label: "Events", href: "/events" }}
      heading={e ? e.eventType : `Event ${id.slice(0, 8)}`}
      notFoundLabel="event"
      overview={overview}
    >
      {error ? <QueryError title="Could not load detail" error={error} onRetry={onRetry} /> : null}
      {e ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Payload</CardTitle>
          </CardHeader>
          <CardContent>
            {eventPayloadText(e.payload) !== null ? (
              <pre className="bg-muted/40 max-h-[32rem] overflow-auto rounded-md border p-3 font-mono text-xs">
                {JSON.stringify(e.payload, null, 2)}
              </pre>
            ) : (
              <p className="text-muted-foreground text-sm">This event carries no payload.</p>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
