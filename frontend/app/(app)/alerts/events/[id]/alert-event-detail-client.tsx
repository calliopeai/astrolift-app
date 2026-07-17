"use client";

import { useQuery } from "@apollo/client/react";
import Link from "next/link";
import * as React from "react";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LIST_ALERT_EVENTS } from "@/graphql/operations/alerts.queries";

interface AlertEvent {
  id: string;
  ruleId: string;
  severity: string;
  firedAt: string;
  resolvedAt?: string | null;
  acknowledgedAt?: string | null;
  summary: string;
  detail: Record<string, unknown>;
}

interface Resp {
  astroliftAlertEvents: AlertEvent[];
}

function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/**
 * Alert event detail (#1106) — the payload/timeline behind a fired alert.
 * Reuses the global LIST_ALERT_EVENTS window (no singular query exists).
 */
export function AlertEventDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<Resp>(LIST_ALERT_EVENTS, {
    variables: { unresolvedOnly: false, limit: 100 },
    fetchPolicy: "cache-and-network",
  });

  const e = React.useMemo(
    () => (data?.astroliftAlertEvents ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  const state = e ? (e.resolvedAt ? "resolved" : e.acknowledgedAt ? "acknowledged" : "firing") : undefined;
  const stateTone: Dot | undefined =
    state === "firing" ? "error" : state === "acknowledged" ? "warn" : state === "resolved" ? "muted" : undefined;

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!e}
      breadcrumb={{ label: "Alerts", href: "/alerts" }}
      heading={e ? `Event ${e.id.slice(0, 8)}` : `Event ${id.slice(0, 8)}`}
      status={state}
      statusTone={stateTone}
      notFoundLabel="alert event"
      overview={
        e
          ? [
              { term: "Summary", description: e.summary || "—" },
              { term: "Severity", description: <span className="capitalize">{e.severity}</span> },
              {
                term: "Rule",
                description: (
                  <Link
                    href={`/alerts/rules/${e.ruleId}`}
                    className="text-[var(--brand-primary)] font-mono text-xs hover:underline"
                  >
                    {e.ruleId}
                  </Link>
                ),
              },
              { term: "Fired", description: <DetailTimestamp iso={e.firedAt} /> },
              { term: "Acknowledged", description: <DetailTimestamp iso={e.acknowledgedAt} /> },
              { term: "Resolved", description: <DetailTimestamp iso={e.resolvedAt} /> },
            ]
          : []
      }
    >
      {e ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Detail</CardTitle>
          </CardHeader>
          <CardContent>
            {Object.keys(e.detail ?? {}).length > 0 ? (
              <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs">
                {prettyJson(e.detail)}
              </pre>
            ) : (
              <p className="text-muted-foreground text-sm">No detail payload recorded for this event.</p>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
