"use client";

/**
 * Waiting on you (spec 44 §4.3, decision 4): what the viewer can decide,
 * in every layout near the top. Deploy gates, workflow gates and secret
 * requests in one ListSummary, each with its action in place: a deploy is
 * approved from the row (behind a confirm); a workflow gate and a secret
 * request open where they are decided, because deciding one means reading
 * the stage output or the diff first.
 */

import { CheckIcon, GitBranchIcon, KeyRoundIcon, RocketIcon, ShieldCheckIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ListSummary } from "@/components/list/ListSummary";
import { Button } from "@/components/ui/button";
import { formatRelativeAge } from "@/lib/format";

import type { HomePanelProps } from "../registry";
import type { HomeRead } from "./home-reads";
import { newestFirst } from "./apps-agents-model";
import { useWaiting } from "./use-waiting";

export type WaitingKind = "deploy" | "gate" | "secret";

export interface WaitingItem {
  key: string;
  kind: WaitingKind;
  /** What waits: the app and environment, the workflow and its stage. */
  title: string;
  /** One line more: the image, the approvals so far, who asked. */
  detail: string;
  /** ISO time it started waiting. */
  at: string;
  /** Where it is decided, and the row's link. */
  href: string;
  /** The deploy id, when the row can approve it in place. */
  approveId?: string;
}

export interface WaitingPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  items: WaitingItem[];
  approving: boolean;
  /** Resolves when approved; throws to keep the confirm open with the reason. */
  onApprove: (deploymentId: string) => Promise<void>;
}

const KIND: Record<WaitingKind, { label: string; icon: React.ReactNode; action: string }> = {
  deploy: { label: "Deploy", icon: <RocketIcon className="size-3.5" />, action: "Approve" },
  gate: { label: "Workflow gate", icon: <GitBranchIcon className="size-3.5" />, action: "Review" },
  secret: {
    label: "Secret request",
    icon: <KeyRoundIcon className="size-3.5" />,
    action: "Review",
  },
};

function WaitingLine({
  item,
  busy,
  onApprove,
}: {
  item: WaitingItem;
  busy: boolean;
  onApprove: () => void;
}) {
  const kind = KIND[item.kind];
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span className="text-muted-foreground shrink-0" title={kind.label} aria-hidden>
        {kind.icon}
      </span>
      <div className="min-w-0 flex-1">
        <Link
          href={item.href}
          className="block truncate font-medium hover:underline"
          title={item.title}
        >
          {item.title}
        </Link>
        <p className="text-muted-foreground truncate text-xs" title={item.detail}>
          <span className="sr-only">{kind.label}: </span>
          {item.detail}
        </p>
      </div>
      <span className="text-muted-foreground hidden shrink-0 font-mono text-xs sm:inline">
        {formatRelativeAge(item.at)}
      </span>
      {item.approveId ? (
        <Button
          size="sm"
          variant="outline"
          className="shrink-0"
          disabled={busy}
          onClick={onApprove}
        >
          <CheckIcon className="size-3.5" aria-hidden />
          {kind.action}
        </Button>
      ) : (
        <Button asChild size="sm" variant="outline" className="shrink-0">
          <Link href={item.href}>{kind.action}</Link>
        </Button>
      )}
    </div>
  );
}

/** Pure. */
export function WaitingPanelView({
  panel,
  items,
  loading,
  error,
  onRetry,
  approving,
  onApprove,
}: WaitingPanelViewProps) {
  const [confirming, setConfirming] = React.useState<WaitingItem | null>(null);
  const sorted = newestFirst(items, (i) => i.at);
  return (
    <>
      <ListSummary
        title={panel.title}
        icon={<ShieldCheckIcon className="size-4" />}
        span={panel.span}
        count={loading || error ? null : items.length}
        rows={sorted}
        keyOf={(i) => i.key}
        renderRow={(i) => (
          <WaitingLine item={i} busy={approving} onApprove={() => setConfirming(i)} />
        )}
        viewAllHref={panel.href}
        loading={loading}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ShieldCheckIcon />,
          title: "Nothing waiting on you",
          description: "Approvals you can decide show up here.",
        }}
      />
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) setConfirming(null);
        }}
        title="Approve this deploy?"
        description={
          confirming ? `${confirming.title}. ${confirming.detail}.` : "Approve the deploy."
        }
        confirmLabel="Approve"
        onConfirm={async () => {
          if (confirming?.approveId) await onApprove(confirming.approveId);
        }}
      />
    </>
  );
}

/** Registered on Home as `waiting`. */
export function WaitingPanel({ panel }: HomePanelProps) {
  return <WaitingPanelView panel={panel} {...useWaiting()} />;
}
