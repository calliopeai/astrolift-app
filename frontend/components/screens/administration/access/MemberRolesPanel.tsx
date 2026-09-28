"use client";

import { ChevronRightIcon, SearchIcon, UsersIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { DetailTimestamp } from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";

/**
 * A member's roles, grouped by the scope they are held at (#1719).
 *
 * This was one flat list of every binding. On a member with real access it
 * scrolled past the fold with nothing to navigate by and no answer to the
 * question the screen exists for -- what does this person actually hold --
 * because that answer was spread across rows you had to scroll to count.
 *
 * Three things replace it: a summary line, grouping by scope, and a filter.
 * The scope order is ORG -> TEAM -> PROJECT -> APP, matching the order the
 * permission resolver walks, so the widest grants read first: that is the
 * order in which a reader's question ("why can they do that?") gets
 * answered.
 */

const SCOPE_ORDER = ["ORG", "TEAM", "PROJECT", "APP"] as const;

const SCOPE_LABEL: Record<string, string> = {
  ORG: "Organization",
  TEAM: "Team",
  PROJECT: "Project",
  APP: "App",
};

function scopeLabel(binding: AstroliftRoleBinding): string {
  return (
    binding.sourceScopeLabel ||
    `${binding.scopeKind}${binding.scopeId ? `:${binding.scopeId}` : ""}`
  );
}

function matches(binding: AstroliftRoleBinding, needle: string): boolean {
  if (!needle) return true;
  const haystack = `${binding.role.name} ${scopeLabel(binding)} ${binding.scopeKind}`;
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

export function MemberRolesPanel({
  bindings,
  loading,
}: {
  bindings: AstroliftRoleBinding[];
  loading: boolean;
}) {
  const [filter, setFilter] = React.useState("");

  const visible = React.useMemo(
    () => bindings.filter((b) => matches(b, filter.trim())),
    [bindings, filter]
  );

  const grouped = React.useMemo(() => {
    const byScope = new Map<string, AstroliftRoleBinding[]>();
    for (const binding of visible) {
      const key = SCOPE_ORDER.includes(binding.scopeKind as (typeof SCOPE_ORDER)[number])
        ? binding.scopeKind
        : "ORG";
      byScope.set(key, [...(byScope.get(key) ?? []), binding]);
    }
    return SCOPE_ORDER.map((scope) => ({ scope, rows: byScope.get(scope) ?? [] })).filter(
      (group) => group.rows.length > 0
    );
  }, [visible]);

  // Counted over every binding, not the filtered view: a summary that
  // changes as you type is not a summary of what they hold.
  const summary = React.useMemo(() => {
    const roles = new Set(bindings.map((b) => b.role.name));
    const inherited = bindings.filter((b) => b.inherits).length;
    const scopes = SCOPE_ORDER.map((scope) => ({
      scope,
      count: bindings.filter((b) => b.scopeKind === scope).length,
    })).filter((row) => row.count > 0);
    return { roleCount: roles.size, inherited, scopes };
  }, [bindings]);

  return (
    <Card>
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="text-base">Roles</CardTitle>
          {bindings.length > 0 ? (
            <div className="relative w-full sm:w-64">
              <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by role or scope"
                aria-label="Filter roles"
                className="h-8 pl-7 text-sm"
              />
            </div>
          ) : null}
        </div>
        {bindings.length > 0 ? (
          <p className="text-muted-foreground text-xs">
            {summary.roleCount} {summary.roleCount === 1 ? "role" : "roles"} across{" "}
            {bindings.length} {bindings.length === 1 ? "binding" : "bindings"}
            {summary.scopes.length > 0 ? (
              <>
                {" — "}
                {summary.scopes
                  .map((row) => `${row.count} at ${SCOPE_LABEL[row.scope].toLowerCase()} scope`)
                  .join(", ")}
              </>
            ) : null}
            {summary.inherited > 0 ? `, ${summary.inherited} inherited` : null}
          </p>
        ) : null}
      </CardHeader>
      <CardContent className={bindings.length === 0 ? "" : "p-0"}>
        {loading && bindings.length === 0 ? (
          <p className="text-muted-foreground text-sm">Loading roles…</p>
        ) : bindings.length === 0 ? (
          <EmptyState
            icon={<UsersIcon className="size-5" />}
            title="No role bindings"
            description="This member has no roles granted directly to their account."
          />
        ) : grouped.length === 0 ? (
          <p className="text-muted-foreground px-4 py-6 text-sm">
            No role matches “{filter.trim()}”.
          </p>
        ) : (
          <div className="divide-border divide-y">
            {grouped.map((group) => (
              <ScopeGroup key={group.scope} scope={group.scope} rows={group.rows} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ScopeGroup({ scope, rows }: { scope: string; rows: AstroliftRoleBinding[] }) {
  const [open, setOpen] = React.useState(true);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="hover:bg-accent/40 flex w-full items-center gap-2 px-4 py-2 text-left">
        <ChevronRightIcon className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        <span className="text-xs font-semibold tracking-widest uppercase">
          {SCOPE_LABEL[scope] ?? scope}
        </span>
        <span className="text-muted-foreground text-xs">{rows.length}</span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="divide-border/60 divide-y">
          {rows.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center gap-2 px-4 py-3 pl-9 text-sm">
              <Badge variant="secondary">{b.role.name}</Badge>
              <span className="text-muted-foreground text-xs">{scopeLabel(b)}</span>
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
      </CollapsibleContent>
    </Collapsible>
  );
}
