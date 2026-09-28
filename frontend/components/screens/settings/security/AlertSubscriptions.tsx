"use client";

import { BellRingIcon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { ALERT_KINDS, subKey } from "./alert-kinds";
import type { useAlertSubscriptions } from "./use-alert-subscriptions";

export type AlertSubscriptionsViewProps = ReturnType<typeof useAlertSubscriptions>;

/** Settings > Notifications: which apps and alert kinds reach the in-app channel. */
export function AlertSubscriptionsView({
  table,
  subMap,
  busy,
  onToggle,
  onSubscribeAll,
  onUnsubscribeAll,
}: AlertSubscriptionsViewProps) {
  const [unsubscribeTarget, setUnsubscribeTarget] = React.useState<string | null>(null);

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
                  await onToggle(a.slug, k.value, e.target.checked);
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
              else void onSubscribeAll(a.slug);
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
          if (unsubscribeTarget) await onUnsubscribeAll(unsubscribeTarget);
        }}
      />
    </Section>
  );
}
