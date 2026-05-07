"use client";

import { useQuery } from "@apollo/client/react";
import { ActivityIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

interface Resp {
  astroliftEvents: AstroliftEvent[];
}

export function EventsClient() {
  const [filter, setFilter] = React.useState("");
  const { data, loading } = useQuery<Resp>(LIST_EVENTS, {
    variables: { limit: 200, eventType: filter || null },
    pollInterval: 5000,
  });
  const list = data?.astroliftEvents ?? [];

  return (
    <PageShell
      title="Events"
      description="Append-only platform fact log: every state change of interest to dashboards, webhooks, and event-driven workflows."
    >
      <Input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter by event type (e.g. APP_REGISTERED)"
        className="max-w-xs"
      />

      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ActivityIcon className="size-5" />}
                title="Quiet feed"
                description="No events match the current filter. Try clearing it, or trigger a mutation to see one land."
              />
            </div>
          ) : (
            <ul className="divide-y">
              {list.map((e) => (
                <li key={e.id} className="flex items-start gap-4 p-4">
                  <div className="bg-primary/10 text-primary mt-0.5 rounded-md p-2">
                    <ActivityIcon className="size-4" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="font-mono text-xs">
                        {e.eventType}
                      </Badge>
                      <span className="text-muted-foreground text-xs">
                        {new Date(e.occurredAt).toLocaleString()}
                      </span>
                    </div>
                    {Object.keys(e.payload).length > 0 && (
                      <pre className="bg-muted text-muted-foreground mt-2 overflow-x-auto rounded-md p-2 text-xs">
                        {JSON.stringify(e.payload, null, 2)}
                      </pre>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
