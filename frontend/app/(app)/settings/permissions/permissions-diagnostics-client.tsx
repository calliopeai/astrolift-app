"use client";

import { useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, XCircleIcon } from "lucide-react";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
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
  app: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  cluster: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  org: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  team: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  project: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  api_token: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  deploy_token: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  policy: "bg-red-500/15 text-red-700 dark:text-red-300",
  audit_log: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  billing: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
};

export function PermissionsDiagnosticsClient() {
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
    bindings.data?.astroliftRoleBindings.filter((b) => b.user?.id === me.data?.me?.id) ??
    [];

  return (
    <PageShell
      title="Permissions diagnostics"
      description="Your effective permissions in this organization, grouped by resource. Use this to figure out why a button is hidden or a mutation rejects."
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm font-medium">
              Total permissions
            </CardTitle>
          </CardHeader>
          <CardContent>
            {perms.loading ? (
              <Skeleton className="h-8 w-12" />
            ) : (
              <p className="text-2xl font-bold tabular-nums">
                {allPerms.length}
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm font-medium">
              Role bindings on you
            </CardTitle>
          </CardHeader>
          <CardContent>
            {bindings.loading ? (
              <Skeleton className="h-8 w-12" />
            ) : (
              <p className="text-2xl font-bold tabular-nums">
                {myBindings.length}
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm font-medium">
              Account
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="font-mono text-sm">
              {me.data?.me?.profile?.username ?? "—"}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              ID:{" "}
              <span className="font-mono">{me.data?.me?.id ?? "—"}</span>
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Effective permissions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
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
                  <Badge
                    variant="secondary"
                    className={RESOURCE_TONE[resource] ?? ""}
                  >
                    {resource}
                  </Badge>
                  <span className="text-muted-foreground text-xs">
                    {perms.length}
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-3">
                  {perms.map((p) => (
                    <div
                      key={p}
                      className="bg-muted/40 inline-flex items-center gap-2 rounded px-2 py-1 font-mono text-xs"
                    >
                      <CheckCircle2Icon className="text-emerald-600 size-3 shrink-0" />
                      {p}
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Why these permissions? (Role bindings)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {bindings.loading ? (
            <Skeleton className="h-16 w-full" />
          ) : myBindings.length === 0 ? (
            <div className="bg-amber-100 border border-amber-300 rounded-md p-3 text-sm dark:bg-amber-950/40 dark:border-amber-900/60">
              <div className="flex items-center gap-2 font-medium">
                <XCircleIcon className="size-4" /> No role bindings on your account
              </div>
              <p className="text-muted-foreground mt-1">
                Either you&apos;re a superuser (in which case all
                permissions are bypassed at the resolver level), or this
                org hasn&apos;t bound any roles to you yet. An org
                admin can issue an invitation with a role.
              </p>
            </div>
          ) : (
            myBindings.map((b) => (
              <div
                key={b.id}
                className="border-muted rounded-md border p-3 text-sm"
              >
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
            ))
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
