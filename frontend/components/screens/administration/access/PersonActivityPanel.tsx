"use client";

import { HistoryIcon } from "lucide-react";
import Link from "next/link";

import { Feed } from "@/components/feed/Feed";
import { PermissionNote } from "@/components/settings/Restricted";
import { Badge } from "@/components/ui/badge";
import type { AstroliftAuditEvent } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

export interface PersonActivityPanelProps {
  allowed: boolean;
  items: AstroliftAuditEvent[];
  loading: boolean;
  error: { message: string } | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRetry: () => void;
  /** The audit log, filtered to this actor. */
  auditHref: string;
}

const DECISION_TONE: Record<string, string> = {
  ALLOW: "text-success-fg",
  DENY: "text-danger",
};

/**
 * A person's Activity tab (access UX design 3.2): what they did, from the
 * audit log, as a Feed: its own scroll frame, older events on the cursor,
 * Load older as the fallback. The full, filterable audit is one link away.
 * Pure: data from usePersonActivity.
 */
export function PersonActivityPanel({
  allowed,
  items,
  loading,
  error,
  hasMore,
  loadingMore,
  onLoadMore,
  onRetry,
  auditHref,
}: PersonActivityPanelProps) {
  const fmt = useFormatters();
  if (!allowed) return <PermissionNote permission="audit_log.read" verb="Reading activity" />;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="text-muted-foreground text-xs">
        What they did, newest first.{" "}
        <Link href={auditHref} className="text-foreground underline-offset-2 hover:underline">
          Open in the audit log
        </Link>
      </p>
      <Feed<AstroliftAuditEvent>
        label="Activity"
        items={items}
        keyOf={(e) => e.id}
        groupBy={{ day: (e) => e.occurredAt }}
        loading={loading}
        error={error}
        onRetry={onRetry}
        hasMore={hasMore}
        loadingMore={loadingMore}
        onLoadMore={onLoadMore}
        maxHeight="max-h-[32rem]"
        empty={{
          icon: <HistoryIcon className="size-5" />,
          title: "No activity yet",
          description: "Actions this person takes show here as the audit log records them.",
        }}
        renderItem={(e) => (
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
            <span className="text-muted-foreground shrink-0 font-mono text-xs">
              {fmt.formatDateTime(e.occurredAt)}
            </span>
            <Badge variant="outline" className="max-w-full truncate font-mono text-xs">
              {e.action}
            </Badge>
            {e.targetKind && (
              <span
                className="text-muted-foreground min-w-0 truncate font-mono text-xs"
                title={`${e.targetKind} ${e.targetSlug || e.targetId}`}
              >
                {e.targetKind} {e.targetSlug || e.targetId}
              </span>
            )}
            <span
              className={`ml-auto shrink-0 font-mono text-xs ${DECISION_TONE[e.decision] ?? ""}`}
            >
              {e.decision.toLowerCase()}
            </span>
          </div>
        )}
      />
    </div>
  );
}
