"use client";

import { CheckIcon, ClockIcon, MailIcon, UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftDeploymentApprover } from "@/graphql/lifecycle/lifecycle.types";

interface QuorumWidgetProps {
  /** Total approvals required for this deploy to advance past
   *  ``pending_approval`` (== ``requiredApproverCount`` on the
   *  deployment). */
  requiredApproverCount: number;
  /** Users who have already cast an approve vote — green check overlay
   *  on the avatar stack. */
  approvedBy: AstroliftDeploymentApprover[];
  /** Eligible org / team members who haven't voted yet. Each entry's
   *  ``mailtoUrl`` is rendered as a one-click nudge so operators don't
   *  have to copy emails into their mail client manually. */
  awaitingApprovers: AstroliftDeploymentApprover[];
}

/**
 * Approval quorum surface for ``/approvals/[id]`` (#420). Replaces the
 * count-only "N of M" badge with a richer block:
 *
 * * Header: ``approvalsReceived / requiredApproverCount`` with a
 *   progress hint.
 * * Approved stack: avatars + display names of users who already
 *   voted, with a green checkmark.
 * * Awaiting stack: avatars + mailto: nudge button per eligible
 *   approver who hasn't voted.
 *
 * Hidden entirely when ``requiredApproverCount === 0`` — non-gated
 * deploys don't need the widget.
 */
export function QuorumWidget({
  requiredApproverCount,
  approvedBy,
  awaitingApprovers,
}: QuorumWidgetProps) {
  const t = useTranslations("lists.approval.quorum");

  if (requiredApproverCount <= 0) {
    return null;
  }

  const received = approvedBy.length;
  const remaining = Math.max(requiredApproverCount - received, 0);

  return (
    <section
      aria-labelledby="quorum-widget-heading"
      className="space-y-3 rounded-md border bg-muted/30 p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <h3
          id="quorum-widget-heading"
          className="inline-flex items-center gap-2 text-sm font-medium"
        >
          <UsersIcon className="size-4" />
          {t("header", { received, required: requiredApproverCount })}
        </h3>
        {remaining > 0 ? (
          <Badge variant="secondary" className="text-2xs">
            {t("remainingBadge", { count: remaining })}
          </Badge>
        ) : (
          <Badge
            variant="outline"
            className="border-green-500/40 bg-green-500/10 text-2xs text-green-700 dark:text-green-300"
          >
            {t("metBadge")}
          </Badge>
        )}
      </div>

      <div className="space-y-3">
        <ApproverGroup
          label={t("approvedLabel")}
          empty={t("approvedEmpty")}
          approvers={approvedBy}
          variant="approved"
        />
        <ApproverGroup
          label={t("awaitingLabel")}
          empty={t("awaitingEmpty")}
          approvers={awaitingApprovers}
          variant="awaiting"
        />
      </div>
    </section>
  );
}

function ApproverGroup({
  label,
  empty,
  approvers,
  variant,
}: {
  label: string;
  empty: string;
  approvers: AstroliftDeploymentApprover[];
  variant: "approved" | "awaiting";
}) {
  return (
    <div>
      <div className="text-muted-foreground mb-2 text-2xs font-medium tracking-wide uppercase">
        {label}
      </div>
      {approvers.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{empty}</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {approvers.map((approver) => (
            <li key={approver.userId}>
              <ApproverChip approver={approver} variant={variant} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ApproverChip({
  approver,
  variant,
}: {
  approver: AstroliftDeploymentApprover;
  variant: "approved" | "awaiting";
}) {
  const initials = computeInitials(approver.displayName || approver.email);
  const ring =
    variant === "approved"
      ? "ring-1 ring-green-500/40"
      : "ring-1 ring-amber-500/40";

  return (
    <span
      className={`bg-background inline-flex items-center gap-2 rounded-full border px-2 py-1 text-xs ${ring}`}
      title={`${approver.displayName}${approver.email ? ` <${approver.email}>` : ""}`}
    >
      <span
        aria-hidden
        className="bg-muted text-muted-foreground inline-flex size-6 items-center justify-center rounded-full text-2xs font-medium"
      >
        {initials}
      </span>
      <span className="max-w-[10rem] truncate font-medium">{approver.displayName}</span>
      {variant === "approved" ? (
        <CheckIcon className="size-3 text-green-600 dark:text-green-400" aria-hidden />
      ) : (
        <ClockIcon className="size-3 text-amber-600 dark:text-amber-400" aria-hidden />
      )}
      {variant === "awaiting" && approver.mailtoUrl ? (
        <Button
          asChild
          size="icon"
          variant="ghost"
          className="ml-0.5 size-6 min-h-0 min-w-0 rounded-full"
        >
          <a
            href={approver.mailtoUrl}
            aria-label={`Email ${approver.displayName}`}
            className="text-muted-foreground hover:text-foreground"
          >
            <MailIcon className="size-3" />
          </a>
        </Button>
      ) : null}
    </span>
  );
}

function computeInitials(name: string): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) {
    return parts[0].slice(0, 2).toUpperCase();
  }
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
