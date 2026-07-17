"use client";

import { useQuery } from "@apollo/client/react";
import { ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DefinitionListItem } from "@/components/ui/definition-list";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

import { resolveSourceHref } from "../events-client";

interface Resp {
  astroliftEvents: AstroliftEvent[];
}

/**
 * Event detail (#1106) — full payload + metadata for a single platform event.
 * Reuses the unfiltered LIST_EVENTS window (no singular query exists); a cold
 * deep-link resolves as long as the event is within the recent 200.
 */
export function EventDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<Resp>(LIST_EVENTS, {
    variables: { limit: 200, eventType: null },
    fetchPolicy: "cache-and-network",
  });

  const e = React.useMemo(
    () => (data?.astroliftEvents ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  const sourceHref = e ? resolveSourceHref(e.resourceKind ?? "", e.resourceId ?? "", e.payload ?? {}) : null;
  const sourceLabel = e ? `${e.resourceKind || "?"}:${e.resourceId || "?"}` : "";

  const overview: DefinitionListItem[] = e
    ? [
        { term: "Event type", description: <span className="font-mono text-xs">{e.eventType}</span> },
        { term: "Occurred", description: <DetailTimestamp iso={e.occurredAt} /> },
        {
          term: "Source",
          description:
            !e.resourceKind && !e.resourceId ? (
              <span className="text-muted-foreground">—</span>
            ) : sourceHref ? (
              <Link
                href={sourceHref}
                className="text-[var(--brand-primary)] inline-flex items-center gap-1 font-mono text-xs hover:underline"
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
    overview.push({ term: "App", description: <span className="font-mono text-xs break-all">{e.registeredAppId}</span> });
  }
  if (e?.organizationId) {
    overview.push({ term: "Organization", description: <span className="font-mono text-xs break-all">{e.organizationId}</span> });
  }
  if (e?.teamId) {
    overview.push({ term: "Team", description: <span className="font-mono text-xs break-all">{e.teamId}</span> });
  }
  if (e?.projectId) {
    overview.push({ term: "Project", description: <span className="font-mono text-xs break-all">{e.projectId}</span> });
  }

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!e}
      breadcrumb={{ label: "Events", href: "/events" }}
      heading={e ? e.eventType : `Event ${id.slice(0, 8)}`}
      notFoundLabel="event"
      overview={overview}
    >
      {e ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Payload</CardTitle>
          </CardHeader>
          <CardContent>
            {Object.keys(e.payload ?? {}).length > 0 ? (
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
