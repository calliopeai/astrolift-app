"use client";

import { HistoryIcon } from "lucide-react";

import { Feed } from "@/components/feed/Feed";
import { Badge } from "@/components/ui/badge";

import type { useSecretHistory } from "./use-secret-history";

export type SecretHistoryPanelViewProps = ReturnType<typeof useSecretHistory>;

/**
 * #714: one secret key's audit history, newest first, as a Feed (Leo's list
 * rule 5), grouped by day. The body of the side sheet the Secrets tab opens
 * (spec 44 §5.4); the sheet's header names the key. `astroliftAppSecretHistory`
 * has no cursor yet, so the whole history arrives at once and there is no
 * older page to load.
 */
export function SecretHistoryPanelView({ entries, loading, error }: SecretHistoryPanelViewProps) {
  return (
    <Feed
      label="Audit history"
      items={entries}
      keyOf={(e) => `${e.timestamp}-${e.action}-${e.actor?.id ?? "system"}`}
      groupBy={{ day: (e) => e.timestamp }}
      loading={loading && entries.length === 0}
      error={error ? "Couldn't load history." : null}
      errorTitle="Couldn't load history"
      empty={{ icon: <HistoryIcon className="size-5" />, title: "No audit entries yet" }}
      maxHeight="max-h-full"
      dense
      className="px-3"
      renderItem={(e) => (
        <div className="flex min-w-0 items-start gap-2 text-xs">
          <Badge variant={e.success ? "secondary" : "destructive"} className="text-2xs mt-0.5">
            {e.action}
          </Badge>
          <div className="min-w-0 flex-1">
            <p className="[overflow-wrap:anywhere]">
              {e.actor?.username ?? "system"}
              {e.sourceIp ? (
                <span className="text-muted-foreground font-mono"> · {e.sourceIp}</span>
              ) : null}
            </p>
            <p className="text-muted-foreground text-2xs font-mono">
              {new Date(e.timestamp).toLocaleString()}
              {!e.success && e.errorCode ? ` · ${e.errorCode}` : ""}
            </p>
          </div>
        </div>
      )}
    />
  );
}
