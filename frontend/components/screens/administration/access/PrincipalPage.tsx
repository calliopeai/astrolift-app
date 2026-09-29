"use client";

import { AlertTriangleIcon, SearchXIcon } from "lucide-react";
import type * as React from "react";

import type { Principal } from "@/components/access/access-model";
import { PrincipalChip } from "@/components/access/PrincipalChip";
import type { DetailTab } from "@/components/DetailPageTabs";
import { EmptyState } from "@/components/EmptyState";
import { type Crumb, ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export interface PrincipalPageProps {
  crumbs: Crumb[];
  /** Null while the principal is still being found. */
  principal: Principal | null;
  /** Shown as the title until the principal resolves. */
  fallbackTitle: string;
  status?: React.ReactNode;
  context?: React.ReactNode;
  primaryAction?: React.ReactNode;
  menu?: React.ReactNode;
  tabs: DetailTab[];
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** Resolved to nobody: the not-found state, with a way back. */
  notFound: boolean;
  notFoundCopy: { title: string; description: string; backHref: string; backLabel: string };
  /** The active tab's body; the route mounts only that one. */
  children: React.ReactNode;
}

/**
 * The detail frame a person, an IdP group and a team share (access UX design
 * 3.2, spec 44 §5.2): breadcrumb, the principal as the title with its status,
 * Grant access as the primary action, one row of tabs (each its own route),
 * then the active tab. Loading, error and not found take the body. Pure.
 */
export function PrincipalPage({
  crumbs,
  principal,
  fallbackTitle,
  status,
  context,
  primaryAction,
  menu,
  tabs,
  loading,
  error,
  onRetry,
  notFound,
  notFoundCopy,
  children,
}: PrincipalPageProps) {
  const resolved = principal !== null;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={crumbs}
        title={
          principal ? (
            <PrincipalChip
              principal={principal}
              className="text-xl"
              showId={principal.kind === "group"}
            />
          ) : (
            fallbackTitle
          )
        }
        status={resolved ? status : undefined}
        context={resolved ? context : undefined}
        primaryAction={resolved ? primaryAction : undefined}
        menu={resolved ? menu : undefined}
        tabs={resolved ? tabs : undefined}
        tabsAriaLabel="Principal"
      />

      {loading && !resolved ? (
        <div className="flex min-w-0 flex-col gap-3" aria-busy>
          <Skeleton className="h-9 w-full rounded-md" />
          <Skeleton className="h-64 w-full rounded-md" />
        </div>
      ) : error && !resolved ? (
        <div className="flex flex-col items-center gap-3 rounded-md border py-10 text-center">
          <AlertTriangleIcon className="text-danger size-5" />
          <div className="min-w-0 px-6">
            <p className="font-medium">Could not load this page</p>
            <p className="text-muted-foreground mt-1 max-w-md text-sm [overflow-wrap:anywhere]">
              {error.message}
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : notFound || !resolved ? (
        <EmptyState
          icon={<SearchXIcon className="size-5" />}
          title={notFoundCopy.title}
          description={notFoundCopy.description}
          actionHref={notFoundCopy.backHref}
          actionLabel={notFoundCopy.backLabel}
        />
      ) : (
        <div className="flex min-w-0 flex-col gap-4">{children}</div>
      )}
    </div>
  );
}
