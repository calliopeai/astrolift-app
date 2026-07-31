"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { BellRingIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  DataTable,
  useCursorTable,
  type Column,
  type CursorPage,
} from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CLEAR_ALERT_SUBSCRIPTION,
  SET_ALERT_SUBSCRIPTION,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_MY_ALERT_SUBSCRIPTIONS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftUserAlertSubscription } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

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

interface AppsPageResp {
  astroliftAppsPage: CursorPage<AstroliftRegisteredApp>;
}

interface SubsResp {
  astroliftMyAlertSubscriptions: AstroliftUserAlertSubscription[];
}

// Map keyed by ``{appSlug}|{alertKind}`` so cell lookups are O(1).
function subKey(appSlug: string, alertKind: string) {
  return `${appSlug}|${alertKind}`;
}

export function AlertSubscriptionsCard() {
  // `astroliftAppsPage` takes `search`, `limit` and `cursor` (not `after`),
  // and no sort argument — so the filter is a server argument and no column
  // declares a `sortKey`. The previous version asked for 200 apps and
  // filtered them in the browser, which silently hid app 201.
  const table = useCursorTable<AstroliftRegisteredApp>({
    query: LIST_APPS_PAGE,
    extract: (d) => (d as AppsPageResp | undefined)?.astroliftAppsPage,
    searchVariable: "search",
    cursorVariable: "cursor",
    urlKey: "alerts",
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

  // Also blocked while the subscription list is in flight: a checkbox whose
  // current state hasn't loaded yet would render off and toggle to a value
  // the operator didn't choose.
  const busy = setState.loading || clearState.loading || subs.loading;
  const [unsubscribeTarget, setUnsubscribeTarget] = React.useState<string | null>(null);

  const subMap = React.useMemo(() => {
    const map = new Map<string, AstroliftUserAlertSubscription>();
    for (const s of subs.data?.astroliftMyAlertSubscriptions ?? []) {
      if (s.channel !== CHANNEL) continue;
      map.set(subKey(s.appSlug, s.alertKind), s);
    }
    return map;
  }, [subs.data?.astroliftMyAlertSubscriptions]);

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

  async function subscribeAll(appSlug: string) {
    for (const kind of ALERT_KINDS) {
      const existing = subMap.get(subKey(appSlug, kind.value));
      if (!existing || !existing.enabled) {
        await setEnabled(appSlug, kind.value, true);
      }
    }
    toast.success(`Subscribed to all on ${appSlug}`);
  }

  // Throws on the first failure so ConfirmDialog keeps the dialog open and
  // reports which clear failed, rather than closing on a partial unsubscribe.
  async function unsubscribeAll(appSlug: string) {
    for (const kind of ALERT_KINDS) {
      const existing = subMap.get(subKey(appSlug, kind.value));
      if (!existing) continue;
      const { data } = await clearSubscription({
        variables: { input: { id: existing.id } },
      });
      if (!data?.clearAlertSubscription.ok) {
        throw new Error(
          data?.clearAlertSubscription.errors?.[0]?.message ?? "Unsubscribe failed"
        );
      }
    }
    toast.success(`Unsubscribed from ${appSlug}`);
  }

  const columns: Column<AstroliftRegisteredApp>[] = [
    {
      id: "app",
      header: "App",
      width: "min-w-40",
      cell: (a) => (
        <>
          <div className="font-medium">{a.name}</div>
          <div className="text-muted-foreground font-mono text-xs">{a.slug}</div>
        </>
      ),
    },
    ...ALERT_KINDS.map(
      (k): Column<AstroliftRegisteredApp> => ({
        id: k.value,
        header: k.label,
        align: "center",
        headClassName: "text-xs",
        cell: (a) => {
          const sub = subMap.get(subKey(a.slug, k.value)) ?? null;
          return (
            <label className="inline-flex cursor-pointer items-center">
              <input
                type="checkbox"
                checked={sub?.enabled === true}
                disabled={busy}
                onChange={async (e) => {
                  const next = e.target.checked;
                  if (!next && sub) {
                    await clearSubscription({ variables: { input: { id: sub.id } } });
                  } else {
                    await setEnabled(a.slug, k.value, next);
                  }
                }}
                aria-label={`${k.label} for ${a.slug}`}
                className="size-4"
              />
            </label>
          );
        },
      })
    ),
    {
      id: "bulk",
      header: "Bulk",
      align: "right",
      width: "w-40",
      cell: (a) => {
        const allOn = ALERT_KINDS.every(
          (k) => subMap.get(subKey(a.slug, k.value))?.enabled === true
        );
        return (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (allOn) setUnsubscribeTarget(a.slug);
              else void subscribeAll(a.slug);
            }}
            disabled={busy}
          >
            {allOn ? "Unsubscribe all" : "Subscribe all"}
          </Button>
        );
      },
    },
  ];

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <BellRingIcon className="size-4" /> App alert subscriptions
        </span>
      }
      description="Choose which apps and event types to receive alerts for."
    >
      <DataTable
        label="App alert subscriptions"
        controller={table}
        columns={columns}
        getRowId={(a) => a.id}
        searchPlaceholder="Filter apps"
        empty={{
          icon: <BellRingIcon className="size-5" />,
          title: "No apps registered",
          description: "Register an app to start configuring alerts for it.",
        }}
        emptyFiltered={{
          title: "No matching apps",
          description: "No app matches that filter. The server matches app name and slug.",
        }}
      />

      <div className="text-muted-foreground flex items-center gap-2 text-xs">
        <Badge variant="outline" className="text-2xs">
          In-app channel
        </Badge>
        <span>
          Email/Slack channels surface here once the user-channel preferences are configured.
        </span>
      </div>

      <ConfirmDialog
        open={unsubscribeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setUnsubscribeTarget(null);
        }}
        title={
          unsubscribeTarget
            ? `Unsubscribe from every alert on ${unsubscribeTarget}?`
            : "Unsubscribe from every alert?"
        }
        description={`Clears all ${ALERT_KINDS.length} alert kinds for this app on the in-app channel. You stop hearing about failed deploys and error spikes until you subscribe again.`}
        confirmLabel="Unsubscribe all"
        destructive
        onConfirm={async () => {
          if (unsubscribeTarget) await unsubscribeAll(unsubscribeTarget);
        }}
      />
    </Section>
  );
}
