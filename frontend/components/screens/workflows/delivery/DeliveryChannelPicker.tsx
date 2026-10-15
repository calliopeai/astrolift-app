"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useFormatters } from "@/lib/i18n/formatters";

import { channelTarget, type DeliveryChannel } from "./DeliveryChannelsPanel";

/** One audit record: a run's result delivered (or not) to one target. */
export interface DeliveryRecord {
  guid: string;
  channelName?: string | null;
  purpose: string;
  runOutcome: string;
  status: string;
  attempts: number;
  lastError: string;
  createdAt: string;
}

export interface DeliveryChannelPickerProps {
  channels: DeliveryChannel[];
  /** The guids of the chosen channels. */
  value: string[];
  onChange: (next: string[]) => void;
  recent: DeliveryRecord[];
}

const OUTCOMES = new Set(["completed", "failed", "cancelled", "terminated", "timed_out"]);
const STATUS_LABELS = new Set(["pending", "retrying", "delivered", "failed"]);

const STATUS_VARIANT: Record<string, "secondary" | "outline" | "destructive"> = {
  delivered: "secondary",
  failed: "destructive",
};

/**
 * Where a scheduled workflow posts its results (#2244): the org's delivery
 * channels as checkboxes, and the last deliveries with their outcome. A
 * failed run also emails the workflow's owner; that needs no channel.
 * Controlled and pure: the Settings tab's Delivery section saves it.
 */
export function DeliveryChannelPicker({
  channels,
  value,
  onChange,
  recent,
}: DeliveryChannelPickerProps) {
  const t = useTranslations("workflowDelivery");
  const fmt = useFormatters();
  const chosen = new Set(value);
  const toggle = (guid: string, on: boolean) =>
    onChange(on ? [...value, guid] : value.filter((g) => g !== guid));

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {channels.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          {t.rich("pickerEmpty", {
            link: (chunks) => (
              <Link href="/settings/notifications?section=workflow-delivery" className="underline">
                {chunks}
              </Link>
            ),
          })}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {channels.map((c) => (
            <li key={c.guid} className="flex min-w-0 items-start gap-2">
              <Checkbox
                id={`delivery-${c.guid}`}
                checked={chosen.has(c.guid)}
                onCheckedChange={(v) => toggle(c.guid, v === true)}
              />
              <Label
                htmlFor={`delivery-${c.guid}`}
                className="flex min-w-0 flex-col items-start gap-0.5"
              >
                <span className="[overflow-wrap:anywhere]">{c.name}</span>
                <span className="text-muted-foreground text-xs font-normal [overflow-wrap:anywhere]">
                  {channelTarget(c) || c.kind}
                  {c.isActive ? "" : ` · ${t("paused")}`}
                </span>
              </Label>
            </li>
          ))}
        </ul>
      )}
      <p className="text-muted-foreground text-xs">{t("ownerNotice")}</p>
      <div className="flex min-w-0 flex-col gap-2">
        <h3 className="text-sm font-medium">{t("recentTitle")}</h3>
        {recent.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("recentEmpty")}</p>
        ) : (
          <ul className="divide-y rounded-md border text-sm">
            {recent.map((d) => (
              <li key={d.guid} className="flex min-w-0 flex-wrap items-center gap-2 p-2">
                <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
                  {d.purpose === "owner_notice"
                    ? t("ownerEmail")
                    : (d.channelName ?? t("removedChannel"))}
                  <span className="text-muted-foreground">
                    {" "}
                    · {OUTCOMES.has(d.runOutcome)
                      ? t(`outcome.${d.runOutcome}`)
                      : d.runOutcome} · {fmt.formatDateTime(d.createdAt)}
                  </span>
                  {d.lastError && (
                    <span className="text-muted-foreground block text-xs">{d.lastError}</span>
                  )}
                </span>
                <Badge variant={STATUS_VARIANT[d.status] ?? "outline"}>
                  {t(`status.${STATUS_LABELS.has(d.status) ? d.status : "pending"}`)}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
