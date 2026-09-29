"use client";

/**
 * ListSummary: the one exception to "one list per screen" (Leo's list rule
 * 3). On an overview, a second list is a count and its top rows, never a
 * second table: a Panel with the title and count, up to five compact rows,
 * and "View all <n>" to the full list route with its filter in the URL.
 *
 *   <ListSummary
 *     title="Failed deploys"
 *     count={page?.totalCount ?? null}
 *     rows={page?.items ?? []}
 *     keyOf={(d) => d.id}
 *     renderRow={(d) => <DeployLine deploy={d} />}
 *     rowHref={(d) => `/apps/${slug}/deployments/${d.id}`}
 *     viewAllHref={`/apps/${slug}/deployments?status=failed`}
 *     loading={loading && !data}
 *     error={error}
 *     onRetry={refetch}
 *     empty={{ icon: <RocketIcon />, title: "No failed deploys" }}
 *   />
 *
 * Its query asks for the top rows only (`first: 5`); the full list fetches
 * its own page when the reader goes there. Pure.
 */

import { ArrowRightIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import type { EmptyStateSpec } from "@/components/data-table";
import { Panel, type PanelSpan, SkeletonRows } from "@/components/panel/Panel";

/** The most rows a summary shows; past it, the reader wants the list. */
export const LIST_SUMMARY_MAX = 5;

export interface ListSummaryProps<T> {
  title: string;
  icon?: React.ReactNode;
  description?: React.ReactNode;
  /** The whole list's size, when the query can say it cheaply. */
  count?: number | null;
  rows: T[];
  keyOf: (row: T) => string;
  /** One compact line: a name, a status, a time. */
  renderRow: (row: T) => React.ReactNode;
  /** Each row links to its detail when set. */
  rowHref?: (row: T) => string;
  /** The full list route, with the summary's filter in the URL. */
  viewAllHref: string;
  /** Rows to show, 1 to 5. Defaults to 5. */
  limit?: number;
  loading?: boolean;
  error?: string | { message: string } | null;
  onRetry?: () => void;
  empty?: EmptyStateSpec | null;
  span?: PanelSpan;
  className?: string;
}

export function ListSummary<T>({
  title,
  icon,
  description,
  count,
  rows,
  keyOf,
  renderRow,
  rowHref,
  viewAllHref,
  limit = LIST_SUMMARY_MAX,
  loading = false,
  error,
  onRetry,
  empty,
  span,
  className,
}: ListSummaryProps<T>) {
  const shown = rows.slice(0, Math.min(Math.max(1, limit), LIST_SUMMARY_MAX));
  const isEmpty = !loading && !error && rows.length === 0;
  const total = count ?? null;
  return (
    <Panel
      title={title}
      icon={icon}
      description={description}
      span={span}
      className={className}
      loading={loading && rows.length === 0}
      skeleton={<SkeletonRows count={Math.min(3, shown.length || 3)} />}
      error={rows.length === 0 ? error : null}
      onRetry={onRetry}
      empty={isEmpty ? (empty ?? null) : null}
      flush
      actions={
        total !== null && (
          <span className="text-muted-foreground font-mono text-xs tabular-nums">
            {total.toLocaleString()}
          </span>
        )
      }
    >
      {isEmpty ? (
        <p className="text-muted-foreground px-4 py-3 text-sm">Nothing here yet.</p>
      ) : (
        <>
          <ul className="min-w-0 divide-y">
            {shown.map((row) => {
              const href = rowHref?.(row);
              const body = <div className="min-w-0 px-4 py-2 text-sm">{renderRow(row)}</div>;
              return (
                <li key={keyOf(row)} className="min-w-0">
                  {href ? (
                    <Link
                      href={href}
                      className="hover:bg-accent/40 focus-visible:ring-ring block min-w-0 transition-colors focus-visible:ring-2 focus-visible:outline-none"
                    >
                      {body}
                    </Link>
                  ) : (
                    body
                  )}
                </li>
              );
            })}
          </ul>
          <div className="border-t px-4 py-2">
            <Link
              href={viewAllHref}
              className="text-primary inline-flex items-center gap-1 text-sm font-medium hover:underline"
            >
              View all
              {total !== null && (
                <span className="font-mono tabular-nums">{total.toLocaleString()}</span>
              )}
              <ArrowRightIcon className="size-3.5" aria-hidden />
            </Link>
          </div>
        </>
      )}
    </Panel>
  );
}
