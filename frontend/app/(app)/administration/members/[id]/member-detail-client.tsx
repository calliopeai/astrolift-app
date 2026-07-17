"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/EmptyState";
import { UsersIcon } from "lucide-react";
import { LIST_MEMBERS, LIST_ROLE_BINDINGS } from "@/graphql/identity/identity.queries";
import type { AstroliftMember, AstroliftRoleBinding } from "@/graphql/identity/identity.types";

interface MembersResp {
  astroliftMembers: AstroliftMember[];
}
interface RoleBindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}

const LIFECYCLE_TONE: Record<string, Dot> = {
  active: "ok",
  invited: "warn",
  suspended: "warn",
  anonymized: "muted",
};

/**
 * Member detail (#1106). Reuses LIST_MEMBERS (no singular query exists) and the
 * already-loaded LIST_ROLE_BINDINGS to surface the member's granted roles. A
 * read-only profile — member mutations stay on the list. Role/team labels come
 * from the role bindings; a richer team roster would need LIST_TEAMS joins.
 */
export function MemberDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<MembersResp>(LIST_MEMBERS, {
    variables: {},
    fetchPolicy: "cache-and-network",
  });
  const bindingsQuery = useQuery<RoleBindingsResp>(LIST_ROLE_BINDINGS, {
    fetchPolicy: "cache-and-network",
  });

  const m = React.useMemo(
    () => (data?.astroliftMembers ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  const roleBindings = React.useMemo(
    () =>
      (bindingsQuery.data?.astroliftRoleBindings ?? []).filter(
        (b) => b.user?.id && m?.user.id && b.user.id === m.user.id
      ),
    [bindingsQuery.data, m]
  );

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!m}
      breadcrumb={{ label: "Members", href: "/administration/members" }}
      heading={m ? m.user.username : `Member ${id.slice(0, 8)}`}
      status={m?.lifecycle}
      statusTone={m ? LIFECYCLE_TONE[m.lifecycle] ?? "muted" : undefined}
      createdAt={m?.joinedAt ?? m?.createdAt}
      notFoundLabel="member"
      overview={
        m
          ? [
              { term: "Username", description: <span className="font-medium">{m.user.username}</span> },
              { term: "Email", description: <span className="font-mono text-xs">{m.user.email}</span> },
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
      {m ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Roles</CardTitle>
          </CardHeader>
          <CardContent className={roleBindings.length === 0 ? "" : "p-0"}>
            {bindingsQuery.loading && roleBindings.length === 0 ? (
              <p className="text-muted-foreground text-sm">Loading roles…</p>
            ) : roleBindings.length === 0 ? (
              <EmptyState
                icon={<UsersIcon className="size-5" />}
                title="No role bindings"
                description="This member has no roles granted directly to their account."
              />
            ) : (
              <ul className="divide-border divide-y">
                {roleBindings.map((b) => (
                  <li key={b.id} className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
                    <Badge variant="secondary">{b.role.name}</Badge>
                    <span className="text-muted-foreground text-xs">
                      {b.sourceScopeLabel || `${b.scopeKind}${b.scopeId ? `:${b.scopeId}` : ""}`}
                    </span>
                    {b.inherits ? (
                      <Badge variant="outline" className="text-2xs">
                        inherited
                      </Badge>
                    ) : null}
                    <span className="text-muted-foreground ml-auto text-xs">
                      granted <DetailTimestamp iso={b.grantedAt} />
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
