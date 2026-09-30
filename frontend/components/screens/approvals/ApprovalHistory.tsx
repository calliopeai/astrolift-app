"use client";

import {
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
  ClockIcon,
  HistoryIcon,
  XCircleIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftDeploymentApprovalHistoryEntry } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useApprovalHistory } from "./use-approval";

const DECISION_ICONS: Record<string, typeof ClockIcon> = {
  "deployment.approve": CheckCircle2Icon,
  "deployment.approve_by_token": CheckCircle2Icon,
  "deployment.reject": XCircleIcon,
  "deployment.reject_by_token": XCircleIcon,
  "deployment.abort": XCircleIcon,
};

export type ApprovalHistoryPanelProps = ReturnType<typeof useApprovalHistory>;

/** Collapsible side panel listing every approve / reject / abort on a deployment. */
export function ApprovalHistoryPanel({ entries, loading }: ApprovalHistoryPanelProps) {
  const t = useTranslations("lists.approval");
  const [open, setOpen] = React.useState(true);

  return (
    <Card className="self-start lg:sticky lg:top-4">
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span className="inline-flex items-center gap-2">
            <HistoryIcon className="size-4" />
            {t("history.title")}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setOpen((prev) => !prev)}
            aria-expanded={open}
            aria-controls="approval-history-panel"
          >
            {open ? <ChevronUpIcon className="size-4" /> : <ChevronDownIcon className="size-4" />}
          </Button>
        </CardTitle>
      </CardHeader>
      {open && (
        <CardContent id="approval-history-panel" className="space-y-3">
          {loading && entries.length === 0 ? (
            <Skeleton className="h-16 w-full" />
          ) : entries.length === 0 ? (
            <p className="text-muted-foreground text-xs">{t("history.empty")}</p>
          ) : (
            <ul className="space-y-3">
              {entries.map((entry) => (
                <HistoryEntry key={entry.id} entry={entry} />
              ))}
            </ul>
          )}
        </CardContent>
      )}
    </Card>
  );
}

function HistoryEntry({ entry }: { entry: AstroliftDeploymentApprovalHistoryEntry }) {
  const t = useTranslations("lists.approval");
  const fmt = useFormatters();
  const tone = decisionTone(entry.action, entry.decision);
  const Icon = DECISION_ICONS[entry.action] ?? ClockIcon;
  return (
    <li className={`flex gap-3 border-l-2 pl-3 ${tone.border}`}>
      <div className="mt-0.5">
        <Icon className={`size-4 ${tone.icon}`} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2 text-xs">
          <span className="font-medium">{t(`history.action.${actionKey(entry.action)}`)}</span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">
            {entry.actorDisplay || entry.actorKind || t("history.actorUnknown")}
          </span>
        </div>
        <p className="text-muted-foreground text-2xs">{fmt.formatDateTime(entry.occurredAt)}</p>
        {entry.reason && <p className="mt-1 text-xs whitespace-pre-wrap">{entry.reason}</p>}
      </div>
    </li>
  );
}

function actionKey(action: string): string {
  switch (action) {
    case "deployment.start":
      return "started";
    case "deployment.approve":
    case "deployment.approve_by_token":
      return "approved";
    case "deployment.reject":
    case "deployment.reject_by_token":
      return "rejected";
    case "deployment.abort":
      return "aborted";
    default:
      return "other";
  }
}

interface ToneClasses {
  border: string;
  icon: string;
}

function decisionTone(action: string, decision: string): ToneClasses {
  if (action === "deployment.approve" || action === "deployment.approve_by_token") {
    return { border: "border-success-border", icon: "text-success-fg" };
  }
  if (
    action === "deployment.reject" ||
    action === "deployment.reject_by_token" ||
    action === "deployment.abort"
  ) {
    return { border: "border-danger-border", icon: "text-danger-fg" };
  }
  if (decision === "DENY") {
    return { border: "border-danger-border", icon: "text-danger-fg" };
  }
  return { border: "border-border", icon: "text-muted-foreground" };
}
