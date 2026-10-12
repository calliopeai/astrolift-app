"use client";

import { BellRingIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { ALERT_KINDS, subKey } from "./alert-kinds";
import type { useAlertSubscriptions } from "./use-alert-subscriptions";

export type AlertSubscriptionsViewProps = ReturnType<typeof useAlertSubscriptions>;

/**
 * Settings > Notifications: which apps and alert kinds reach the in-app
 * channel, as the section's one embedded list (views All · Mine, filters,
 * cursor pages). Pure; the data half is useAlertSubscriptions.
 */
export function AlertSubscriptionsView({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  subMap,
  busy,
  onToggle,
  onSubscribeAll,
  onUnsubscribeAll,
}: AlertSubscriptionsViewProps) {
  const t = useTranslations("notificationSettings.alerts");
  const [unsubscribeTarget, setUnsubscribeTarget] = React.useState<string | null>(null);

  const columns: Column<AstroliftRegisteredApp>[] = [
    {
      id: "app",
      header: t("app"),
      width: "min-w-40",
      cellClassName: "max-w-64",
      cell: (a) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={a.name}>
            {a.name}
          </span>
          <span className="text-muted-foreground block truncate font-mono text-xs" title={a.slug}>
            {a.slug}
          </span>
        </span>
      ),
    },
    ...ALERT_KINDS.map(
      (k): Column<AstroliftRegisteredApp> => ({
        id: k.value,
        header: t(`kinds.${k.value}`),
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
                aria-label={t("toggleLabel", { kind: t(`kinds.${k.value}`), app: a.slug })}
                className="size-4"
              />
            </label>
          );
        },
      })
    ),
    {
      id: "bulk",
      header: t("bulk"),
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
            {allOn ? t("unsubscribeAll") : t("subscribeAll")}
          </Button>
        );
      },
    },
  ];

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <BellRingIcon className="size-4" /> {t("title")}
        </span>
      }
      description={t("description")}
    >
      <ListPage<AstroliftRegisteredApp>
        embedded
        list={list}
        label={t("title")}
        columns={columns}
        rows={rows}
        getRowId={(a) => a.id}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        nextCursor={nextCursor}
        empty={{
          icon: <BellRingIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
      />

      <div className="text-muted-foreground flex items-center gap-2 text-xs">
        <Badge variant="outline" className="text-2xs">
          {t("inAppChannel")}
        </Badge>
        <span>{t("otherChannels")}</span>
      </div>

      <ConfirmDialog
        open={unsubscribeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setUnsubscribeTarget(null);
        }}
        title={
          unsubscribeTarget
            ? t("unsubscribeTitle", { app: unsubscribeTarget })
            : t("unsubscribeTitleGeneric")
        }
        description={t("unsubscribeDescription", { count: ALERT_KINDS.length })}
        confirmLabel={t("unsubscribeAll")}
        destructive
        onConfirm={async () => {
          if (unsubscribeTarget) await onUnsubscribeAll(unsubscribeTarget);
        }}
      />
    </Section>
  );
}
