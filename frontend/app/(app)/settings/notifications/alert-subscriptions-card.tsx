"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { BellRingIcon, SearchIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CLEAR_ALERT_SUBSCRIPTION,
  SET_ALERT_SUBSCRIPTION,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_MY_ALERT_SUBSCRIPTIONS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftUserAlertSubscription } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredAppPage } from "@/graphql/registry/registry.types";

// The alert-kind axis the UI exposes. Server-side accepts arbitrary
// strings; this list is the operator-facing subset surfaced as columns
// in the matrix. New kinds added server-side need a label here before
// they render.
const ALERT_KINDS: { value: string; label: string }[] = [
  { value: "deploy_success", label: "Deploy success" },
  { value: "deploy_failure", label: "Deploy failure" },
  { value: "error_spike", label: "Error spike" },
  { value: "preview_created", label: "Preview created" },
  { value: "preview_destroyed", label: "Preview destroyed" },
];

// The in-app/web notifications surface maps to the backend's "web" channel
// (UserAlertSubscription.Channel = email | web | both). "in_app" is not a
// valid value and the subscribe mutation rejects it (#886).
const CHANNEL = "web";

interface AppsResp {
  astroliftAppsPage: AstroliftRegisteredAppPage;
}

interface SubsResp {
  astroliftMyAlertSubscriptions: AstroliftUserAlertSubscription[];
}

// Map keyed by ``{appSlug}|{alertKind}`` so cell lookups are O(1).
function subKey(appSlug: string, alertKind: string) {
  return `${appSlug}|${alertKind}`;
}

export function AlertSubscriptionsCard() {
  const apps = useQuery<AppsResp>(LIST_APPS_PAGE, {
    variables: { limit: 200 },
    fetchPolicy: "cache-and-network",
  });
  const subs = useQuery<SubsResp>(LIST_MY_ALERT_SUBSCRIPTIONS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const [setSubscription, setState] = useMutation<{
    setAlertSubscription: MutationResult<AstroliftUserAlertSubscription>;
  }>(SET_ALERT_SUBSCRIPTION, {
    refetchQueries: [{ query: LIST_MY_ALERT_SUBSCRIPTIONS, variables: { appSlug: null } }],
    awaitRefetchQueries: true,
  });
  const [clearSubscription, clearState] = useMutation<{
    clearAlertSubscription: MutationResult<AstroliftUserAlertSubscription>;
  }>(CLEAR_ALERT_SUBSCRIPTION, {
    refetchQueries: [{ query: LIST_MY_ALERT_SUBSCRIPTIONS, variables: { appSlug: null } }],
    awaitRefetchQueries: true,
  });

  const busy = setState.loading || clearState.loading;
  const [filter, setFilter] = React.useState("");

  const subMap = React.useMemo(() => {
    const map = new Map<string, AstroliftUserAlertSubscription>();
    for (const s of subs.data?.astroliftMyAlertSubscriptions ?? []) {
      if (s.channel !== CHANNEL) continue;
      map.set(subKey(s.appSlug, s.alertKind), s);
    }
    return map;
  }, [subs.data?.astroliftMyAlertSubscriptions]);

  const appsList = React.useMemo(
    () => apps.data?.astroliftAppsPage?.items ?? [],
    [apps.data?.astroliftAppsPage?.items]
  );
  const filteredApps = React.useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return appsList;
    return appsList.filter(
      (a) =>
        a.name.toLowerCase().includes(needle) || a.slug.toLowerCase().includes(needle)
    );
  }, [appsList, filter]);

  async function setEnabled(appSlug: string, alertKind: string, enabled: boolean) {
    const { data } = await setSubscription({
      variables: {
        input: { appSlug, alertKind, channel: CHANNEL, enabled },
      },
    });
    if (!data?.setAlertSubscription.ok) {
      toast.error(data?.setAlertSubscription.errors?.[0]?.message ?? "Save failed");
    }
  }

  async function setAllForApp(appSlug: string, enabled: boolean) {
    for (const kind of ALERT_KINDS) {
      const existing = subMap.get(subKey(appSlug, kind.value));
      if (enabled) {
        if (!existing || !existing.enabled) {
          await setEnabled(appSlug, kind.value, true);
        }
      } else if (existing) {
        const { data } = await clearSubscription({
          variables: { input: { id: existing.id } },
        });
        if (!data?.clearAlertSubscription.ok) {
          toast.error(
            data?.clearAlertSubscription.errors?.[0]?.message ?? "Unsubscribe failed"
          );
          return;
        }
      }
    }
    toast.success(enabled ? `Subscribed to all on ${appSlug}` : `Unsubscribed from ${appSlug}`);
  }

  const loading = apps.loading || subs.loading;

  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <BellRingIcon className="size-4" />
              <h2 className="text-base font-semibold">App alert subscriptions</h2>
            </div>
            <p className="text-muted-foreground mt-1 text-sm">
              Choose which apps and event types to receive alerts for.
            </p>
          </div>
          <div className="relative w-full max-w-xs">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter apps"
              className="pl-8"
            />
          </div>
        </div>

        {loading && appsList.length === 0 ? (
          <Skeleton className="h-32 w-full" />
        ) : appsList.length === 0 ? (
          <EmptyState
            icon={<BellRingIcon className="size-5" />}
            title="No apps registered"
            description="Register an app to start configuring alerts for it."
          />
        ) : (
          <div className="border-border overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="min-w-40">App</TableHead>
                  {ALERT_KINDS.map((k) => (
                    <TableHead key={k.value} className="text-center text-xs">
                      {k.label}
                    </TableHead>
                  ))}
                  <TableHead className="w-40 text-right">Bulk</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredApps.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={ALERT_KINDS.length + 2}
                      className="text-muted-foreground py-6 text-center text-sm"
                    >
                      No apps match the filter.
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredApps.map((a) => {
                    const rowSubs = ALERT_KINDS.map(
                      (k) => subMap.get(subKey(a.slug, k.value)) ?? null
                    );
                    const allOn = rowSubs.every((s) => s?.enabled === true);
                    return (
                      <TableRow key={a.id}>
                        <TableCell>
                          <div className="font-medium">{a.name}</div>
                          <div className="text-muted-foreground font-mono text-xs">
                            {a.slug}
                          </div>
                        </TableCell>
                        {ALERT_KINDS.map((k, i) => {
                          const sub = rowSubs[i];
                          const checked = sub?.enabled === true;
                          return (
                            <TableCell key={k.value} className="text-center">
                              <label className="inline-flex cursor-pointer items-center">
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  disabled={busy}
                                  onChange={async (e) => {
                                    const next = e.target.checked;
                                    if (!next && sub) {
                                      await clearSubscription({
                                        variables: { input: { id: sub.id } },
                                      });
                                    } else {
                                      await setEnabled(a.slug, k.value, next);
                                    }
                                  }}
                                  aria-label={`${k.label} for ${a.slug}`}
                                  className="size-4"
                                />
                              </label>
                            </TableCell>
                          );
                        })}
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setAllForApp(a.slug, !allOn)}
                            disabled={busy}
                          >
                            {allOn ? "Unsubscribe all" : "Subscribe all"}
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        )}

        <div className="text-muted-foreground flex items-center gap-2 text-xs">
          <Badge variant="outline" className="text-[10px]">
            In-app channel
          </Badge>
          <span>
            Email/Slack channels surface here once the user-channel preferences are configured.
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
