"use client";

import { AlertTriangleIcon, SearchXIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import {
  DetailStatusBadge,
  DetailTimestamp,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import { Skeleton } from "@/components/ui/skeleton";

import { adminCrumb } from "./admin-crumbs";
import { MemberRolesPanel } from "./MemberRolesPanel";
import type { useMemberDetail } from "./use-member-detail";

const LIFECYCLE_TONE: Record<string, Dot> = {
  active: "ok",
  invited: "warn",
  suspended: "warn",
  anonymized: "muted",
};

const MEMBERS_HREF = "/administration/members";

export type MemberDetailProps = ReturnType<typeof useMemberDetail>;

/**
 * Member detail (#1106), on the detail archetype (spec 44 §5.2): the shared
 * header (breadcrumb, name, lifecycle, email), then panels. A member has two
 * things to show, what they are and what they hold, so the page is one view
 * with no tab row. Read-only: member mutations stay on the list.
 */
export function MemberDetail({
  id,
  member: m,
  loading,
  error,
  onRetry,
  roleBindings,
  rolesLoading,
}: MemberDetailProps) {
  const fallbackName = `Member ${id.slice(0, 8)}`;
  const crumbs = [
    adminCrumb("members"),
    { label: "Members", href: MEMBERS_HREF },
    { label: m?.user.username ?? fallbackName },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={crumbs}
        title={m ? m.user.username : fallbackName}
        status={
          m ? (
            <DetailStatusBadge status={m.lifecycle} tone={LIFECYCLE_TONE[m.lifecycle] ?? "muted"} />
          ) : undefined
        }
        context={m ? <span className="font-mono">{m.user.email}</span> : undefined}
      />

      {loading && !m ? (
        <div className="grid min-w-0 gap-6 lg:grid-cols-12" aria-busy>
          <Skeleton className="h-72 w-full rounded-md lg:col-span-5" />
          <Skeleton className="h-72 w-full rounded-md lg:col-span-7" />
        </div>
      ) : error ? (
        <div className="flex flex-col items-center gap-3 rounded-md border py-10 text-center">
          <AlertTriangleIcon className="text-danger size-5" />
          <div className="min-w-0 px-6">
            <p className="font-medium">Could not load this member</p>
            <p className="text-muted-foreground mt-1 max-w-md text-sm [overflow-wrap:anywhere]">
              {error.message}
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : !m ? (
        <EmptyState
          icon={<SearchXIcon className="size-5" />}
          title="Member not found"
          description="This member may not exist, may have aged out of the recent list, or you may not have access to it."
          actionHref={MEMBERS_HREF}
          actionLabel="Back to Members"
        />
      ) : (
        <div className="grid min-w-0 items-start gap-6 lg:grid-cols-12">
          <Card className="min-w-0 lg:col-span-5">
            <CardHeader>
              <CardTitle className="text-base">Overview</CardTitle>
            </CardHeader>
            <CardContent className="min-w-0">
              <DefinitionList
                items={[
                  {
                    term: "Username",
                    description: (
                      <span className="font-mono [overflow-wrap:anywhere]">{m.user.username}</span>
                    ),
                  },
                  {
                    term: "Email",
                    description: (
                      <span className="font-mono text-xs [overflow-wrap:anywhere]">
                        {m.user.email}
                      </span>
                    ),
                  },
                  {
                    term: "Account",
                    description: m.user.isActive ? (
                      <Badge variant="secondary">Active</Badge>
                    ) : (
                      <Badge variant="outline">Inactive</Badge>
                    ),
                  },
                  {
                    term: "Membership",
                    description: m.isActive ? (
                      <Badge variant="secondary">Active</Badge>
                    ) : (
                      <Badge variant="outline">Inactive</Badge>
                    ),
                  },
                  {
                    term: "Scope",
                    description: (
                      <span className="flex min-w-0 flex-wrap items-center gap-2">
                        <Badge variant="outline" className="font-mono">
                          {m.scopeKind}
                        </Badge>
                        {m.scopeId ? (
                          <span className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                            {m.scopeId}
                          </span>
                        ) : null}
                      </span>
                    ),
                  },
                  { term: "Joined", description: <Mono iso={m.joinedAt} /> },
                  { term: "Last active", description: <Mono iso={m.lastActiveAt} /> },
                  { term: "Last seen", description: <Mono iso={m.lastSeenAt} /> },
                  { term: "Created", description: <Mono iso={m.createdAt} /> },
                ]}
              />
            </CardContent>
          </Card>
          <div className="min-w-0 lg:col-span-7">
            <MemberRolesPanel bindings={roleBindings} loading={rolesLoading} />
          </div>
        </div>
      )}
    </div>
  );
}

function Mono({ iso }: { iso: string | null | undefined }) {
  return (
    <span className="font-mono text-xs">
      <DetailTimestamp iso={iso} />
    </span>
  );
}
