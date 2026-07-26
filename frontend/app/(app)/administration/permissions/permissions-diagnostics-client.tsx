"use client";

import { useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, XCircleIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import { LIST_ROLE_BINDINGS } from "@/graphql/identity/identity.queries";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import { GET_ME } from "@/graphql/user/user.queries";
import type { CurrentUser } from "@/graphql/user/user.types";
import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";
import { useFormatters } from "@/lib/i18n/formatters";

interface MyPermsResp {
  astroliftMyPermissions: string[];
}
interface BindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}
interface MeResp {
  me: CurrentUser | null;
}

const RESOURCE_TONE: Record<string, string> = {
  app: "bg-info/15 text-info-fg",
  cluster: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  org: "bg-success/15 text-success-fg",
  team: "bg-success/15 text-success-fg",
  project: "bg-success/15 text-success-fg",
  api_token: "bg-warning/15 text-warning-fg",
  deploy_token: "bg-warning/15 text-warning-fg",
  policy: "bg-danger/15 text-danger-fg",
  audit_log: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  billing: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
};

/**
 * Read-only "why do I have this access" body. Rendered as the
 * Diagnostics tab of the Permissions screen (#1206), so it omits its
 * own PageShell — the tabbed client owns the page chrome. Effective
 * permissions come from `astroliftMyPermissions`; the role bindings
 * behind them come from `astroliftRoleBindings`.
 */
export function PermissionsDiagnosticsContent() {
  const fmt = useFormatters();
  const me = useQuery<MeResp>(GET_ME);
  const perms = useQuery<MyPermsResp>(GET_MY_PERMISSIONS);
  const bindings = useQuery<BindingsResp>(LIST_ROLE_BINDINGS);

  const [filter, setFilter] = React.useState("");

  const allPerms = perms.data?.astroliftMyPermissions ?? [];
  const filtered = React.useMemo(() => {
    if (!filter.trim()) return allPerms;
    const needle = filter.toLowerCase();
    return allPerms.filter((p) => p.toLowerCase().includes(needle));
  }, [allPerms, filter]);

  // Group permissions by resource (the "<resource>." prefix) so the
  // operator can scan their access by domain. Unprefixed permissions
  // get an "other" bucket — robust against custom catalogs.
  const grouped = React.useMemo(() => {
    const map = new Map<string, string[]>();
    for (const p of filtered) {
      const dot = p.indexOf(".");
      const resource = dot >= 0 ? p.slice(0, dot) : "other";
      if (!map.has(resource)) map.set(resource, []);
      map.get(resource)!.push(p);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  const myBindings =
    bindings.data?.astroliftRoleBindings.filter((b) => b.user?.id === me.data?.me?.id) ?? [];

  return (
    <>
      <p className="text-muted-foreground max-w-2xl text-sm">
        Your effective permissions in this organization, grouped by resource. Use this to figure out
        why a button is hidden or a mutation rejects.
      </p>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile label="Total permissions" value={allPerms.length} loading={perms.loading} />
        <StatTile
          label="Role bindings on you"
          value={myBindings.length}
          loading={bindings.loading}
        />
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm font-medium">Account</CardTitle>
          </CardHeader>
          <CardContent>
            <DefinitionList
              orientation="stack"
              items={[
                {
                  term: "Username",
                  description: (
                    <span className="font-mono text-sm">
                      {me.data?.me?.profile?.username ?? "—"}
                    </span>
                  ),
                },
                {
                  term: "ID",
                  description: <span className="font-mono text-xs">{me.data?.me?.id ?? "—"}</span>,
                },
              ]}
            />
          </CardContent>
        </Card>
      </div>

      <Section title="Effective permissions">
        <div className="space-y-4">
          <Input
            placeholder="Filter by name (e.g. 'app.deploy')"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="max-w-md"
          />
          {perms.loading ? (
            <Skeleton className="h-32 w-full" />
          ) : grouped.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              {filter
                ? `No permissions match ${JSON.stringify(filter)}.`
                : "You have no granted permissions in this organization."}
            </p>
          ) : (
            grouped.map(([resource, perms]) => (
              <div key={resource}>
                <div className="mb-2 flex items-center gap-2">
                  <Badge variant="secondary" className={RESOURCE_TONE[resource] ?? ""}>
                    {resource}
                  </Badge>
                  <span className="text-muted-foreground text-xs">{perms.length}</span>
                </div>
                <div className="grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-3">
                  {perms.map((p) => (
                    <div
                      key={p}
                      className="bg-muted/40 inline-flex items-center gap-2 rounded px-2 py-1 font-mono text-xs"
                    >
                      <CheckCircle2Icon className="text-success-fg size-3 shrink-0" />
                      {p}
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </Section>

      <Section title="Why these permissions? (Role bindings)">
        {bindings.loading ? (
          <Skeleton className="h-16 w-full" />
        ) : myBindings.length === 0 ? (
          <div className="bg-warning/10 border-warning-border rounded-md border p-3 text-sm">
            <div className="flex items-center gap-2 font-medium">
              <XCircleIcon className="size-4" /> No role bindings on your account
            </div>
            <p className="text-muted-foreground mt-1">
              Either you&apos;re a superuser (in which case all permissions are bypassed at the
              resolver level), or this org hasn&apos;t bound any roles to you yet. An org admin can
              issue an invitation with a role.
            </p>
          </div>
        ) : (
          <div className="divide-y">
            {myBindings.map((b) => (
              <div key={b.id} className="py-3 text-sm first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono">{b.role.slug}</span>
                  <Badge variant="outline" className="text-xs">
                    {b.role.name}
                  </Badge>
                  <Badge variant="secondary" className="text-xs">
                    {b.scopeKind}
                  </Badge>
                  {b.expiresAt && (
                    <span className="text-muted-foreground text-xs">
                      expires {fmt.formatDateTime(b.expiresAt)}
                    </span>
                  )}
                </div>
                <div className="text-muted-foreground mt-1 text-xs">
                  granted {fmt.formatDateTime(b.grantedAt)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
