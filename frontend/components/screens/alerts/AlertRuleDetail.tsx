"use client";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { QueryError } from "@/components/QueryError";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { prettyJson } from "./alert-format";
import type { useAlertRuleDetail } from "./use-alert-rule-detail";

const SEVERITY_TONE: Record<string, Dot> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

export type AlertRuleDetailProps = Omit<
  ReturnType<typeof useAlertRuleDetail>,
  "error" | "onRetry"
> & { error?: string | null; onRetry?: () => void };

/** Alert rule detail (#1106): state, target, mute, and the raw predicate/channels. */
export function AlertRuleDetail({ id, rule: r, loading, error, onRetry }: AlertRuleDetailProps) {
  return (
    <EntityDetailShell
      loading={loading}
      notFound={!r && !error}
      breadcrumb={{ label: "Alerts", href: "/alerts" }}
      heading={r ? r.name : `Rule ${id.slice(0, 8)}`}
      status={r?.severity}
      statusTone={r ? (SEVERITY_TONE[r.severity] ?? "muted") : undefined}
      createdAt={r?.createdAt}
      notFoundLabel="alert rule"
      overview={
        r
          ? [
              {
                term: "State",
                description: r.activeMute ? (
                  <Badge variant="secondary">Muted</Badge>
                ) : r.isActive ? (
                  <Badge variant="secondary">Active</Badge>
                ) : (
                  <Badge variant="outline">Inactive</Badge>
                ),
              },
              {
                term: "Target",
                description: (
                  <span>
                    <Badge variant="outline">{r.target}</Badge>
                    {r.targetId ? (
                      <span className="text-muted-foreground ml-2 font-mono text-xs">
                        {r.targetId}
                      </span>
                    ) : null}
                  </span>
                ),
              },
              { term: "Severity", description: <span className="capitalize">{r.severity}</span> },
              {
                term: "Managed service",
                description: r.managedServiceId ? (
                  <span className="font-mono text-xs">{r.managedServiceId}</span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              {
                term: "Organization",
                description: <span className="font-mono text-xs">{r.organizationSlug}</span>,
              },
              { term: "Created", description: <DetailTimestamp iso={r.createdAt} /> },
              { term: "Updated", description: <DetailTimestamp iso={r.updatedAt} /> },
              ...(r.activeMute
                ? [
                    {
                      term: "Muted until",
                      description: <DetailTimestamp iso={r.activeMute.ttlUntil} />,
                    },
                    { term: "Mute reason", description: r.activeMute.reason || "—" },
                    {
                      term: "Muted by",
                      description: (
                        <span className="font-mono text-xs">{r.activeMute.createdBy}</span>
                      ),
                    },
                  ]
                : []),
            ]
          : []
      }
    >
      {error ? <QueryError title="Could not load detail" error={error} onRetry={onRetry} /> : null}
      {r ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Predicate</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs">
                {prettyJson(r.predicate)}
              </pre>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Notify channels</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs">
                {prettyJson(r.notifyChannels)}
              </pre>
            </CardContent>
          </Card>
        </>
      ) : null}
    </EntityDetailShell>
  );
}
