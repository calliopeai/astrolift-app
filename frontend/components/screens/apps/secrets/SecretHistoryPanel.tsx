"use client";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

import type { useSecretHistory } from "./use-secret-history";

export type SecretHistoryPanelViewProps = ReturnType<typeof useSecretHistory>;

/** #714 — the audit history popover body for one secret key. */
export function SecretHistoryPanelView({
  secretKey,
  entries,
  loading,
  error,
}: SecretHistoryPanelViewProps) {
  return (
    <div className="flex flex-col">
      <div className="border-b px-3 py-2">
        <p className="text-xs font-medium">Audit history</p>
        <p className="text-muted-foreground text-2xs font-mono">{secretKey}</p>
      </div>
      <div className="max-h-80 overflow-y-auto">
        {loading && entries.length === 0 ? (
          <div className="p-3">
            <Skeleton className="mb-2 h-3 w-full" />
            <Skeleton className="mb-2 h-3 w-2/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        ) : error ? (
          <p className="text-muted-foreground p-3 text-xs">Couldn&apos;t load history.</p>
        ) : entries.length === 0 ? (
          <p className="text-muted-foreground p-3 text-xs italic">No audit entries yet.</p>
        ) : (
          <ol className="divide-border divide-y">
            {entries.map((e, i) => (
              <li key={i} className="flex items-start gap-2 px-3 py-2 text-xs">
                <Badge
                  variant={e.success ? "secondary" : "destructive"}
                  className="text-2xs mt-0.5"
                >
                  {e.action}
                </Badge>
                <div className="min-w-0 flex-1">
                  <p className="truncate">
                    {e.actor?.username ?? "system"}
                    {e.sourceIp ? (
                      <span className="text-muted-foreground font-mono"> · {e.sourceIp}</span>
                    ) : null}
                  </p>
                  <p className="text-muted-foreground text-2xs">
                    {new Date(e.timestamp).toLocaleString()}
                    {!e.success && e.errorCode ? ` · ${e.errorCode}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
