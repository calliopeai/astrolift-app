"use client";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";

import { MemberRolesPanel } from "./MemberRolesPanel";
import type { useMemberDetail } from "./use-member-detail";

const LIFECYCLE_TONE: Record<string, Dot> = {
  active: "ok",
  invited: "warn",
  suspended: "warn",
  anonymized: "muted",
};

export type MemberDetailProps = ReturnType<typeof useMemberDetail>;

/**
 * Member detail (#1106). A read-only profile -- member mutations stay on the
 * list. Role/team labels come from the role bindings; a richer team roster
 * would need LIST_TEAMS joins.
 */
export function MemberDetail({
  id,
  member: m,
  loading,
  roleBindings,
  rolesLoading,
}: MemberDetailProps) {
  return (
    <EntityDetailShell
      loading={loading}
      notFound={!m}
      breadcrumb={{ label: "Members", href: "/administration/members" }}
      heading={m ? m.user.username : `Member ${id.slice(0, 8)}`}
      status={m?.lifecycle}
      statusTone={m ? (LIFECYCLE_TONE[m.lifecycle] ?? "muted") : undefined}
      createdAt={m?.joinedAt ?? m?.createdAt}
      notFoundLabel="member"
      overview={
        m
          ? [
              {
                term: "Username",
                description: <span className="font-medium">{m.user.username}</span>,
              },
              {
                term: "Email",
                description: <span className="font-mono text-xs">{m.user.email}</span>,
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
                  <span>
                    <Badge variant="outline">{m.scopeKind}</Badge>
                    {m.scopeId ? (
                      <span className="text-muted-foreground ml-2 font-mono text-xs break-all">
                        {m.scopeId}
                      </span>
                    ) : null}
                  </span>
                ),
              },
              { term: "Joined", description: <DetailTimestamp iso={m.joinedAt} /> },
              { term: "Last active", description: <DetailTimestamp iso={m.lastActiveAt} /> },
              { term: "Last seen", description: <DetailTimestamp iso={m.lastSeenAt} /> },
              { term: "Created", description: <DetailTimestamp iso={m.createdAt} /> },
            ]
          : []
      }
    >
      {m ? <MemberRolesPanel bindings={roleBindings} loading={rolesLoading} /> : null}
    </EntityDetailShell>
  );
}
