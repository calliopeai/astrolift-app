"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  CircleIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import type * as React from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { SCOPE_NOUN, type ScopeRef } from "./access-model";

/** One step of `permissionDiagnose` (core/schema/types/permission_analysis.py). */
export interface DiagnosisStep {
  check: string;
  result: boolean;
  detail: string;
}

export interface Diagnosis {
  username: string;
  permission: string;
  granted: boolean;
  isSuperuser: boolean;
  steps: DiagnosisStep[];
}

/** A `role@SCOPE:id` binding label the diagnosis names, parsed. */
export interface BindingLabel {
  role: string;
  scopeKind: ScopeRef["kind"];
  scopeId: string;
}

export interface AccessExplainerProps {
  diagnosis: Diagnosis | null;
  /** What was asked about: the check runs on it (`scopeType` / `scopeId`), or org-wide when absent. */
  target?: ScopeRef | null;
  loading?: boolean;
  error?: { message: string } | null;
  onRetry?: () => void;
  /** Where a binding the chain names can be changed. */
  bindingHref?: (binding: BindingLabel) => string | undefined;
  className?: string;
}

const STEP_LABEL: Record<string, string> = {
  is_active: "The account is active",
  is_superuser: "Superuser",
  permission_is_declared: "The permission exists",
  has_active_organization: "The request is in an organization",
  role_bindings_in_this_org: "They hold roles in this organization",
  bindings_carrying_this_permission: "A role they hold carries it",
  target_scope: "The object is in this organization",
  idp_groups: "Their IdP groups",
  team_shares: "A team share on the app carries it",
  rbac: "Their roles grant it here",
  abac_policies: "No policy denies it",
  resolver_verdict: "The resolver's answer",
};

/** Steps whose false is information, not the reason for a No. */
const NEUTRAL_WHEN_FALSE = new Set(["is_superuser", "idp_groups", "team_shares"]);

const BINDING = /^([\w.-]+)@(ORG|TEAM|PROJECT|APP):(\S+)$/;

export function parseBindingLabels(detail: string): BindingLabel[] | null {
  const parts = detail.split(/,\s*/).filter(Boolean);
  const parsed = parts.map((p) => BINDING.exec(p.trim()));
  if (parts.length === 0 || parsed.some((m) => !m)) return null;
  return parsed.map((m) => ({
    role: m![1],
    scopeKind: m![2] as BindingLabel["scopeKind"],
    scopeId: m![3],
  }));
}

/**
 * "Can <who> <do what>?" answered with the reasoning (design 3.7): Yes or No
 * in the status tone, then `permissionDiagnose`'s chain in the resolver's
 * order, each step passed, failed or informational, with its detail. Role
 * bindings the chain names become links to where they can be changed. The
 * chain is the resolver's own, policies included (`abac_policies`): a DENY
 * that matches, or a condition the request cannot answer, turns a Yes into
 * a No. Scrolls in its own frame. Pure.
 */
export function AccessExplainer({
  diagnosis,
  target,
  loading = false,
  error,
  onRetry,
  bindingHref,
  className,
}: AccessExplainerProps) {
  if (loading) {
    return (
      <div className={cn("flex min-w-0 flex-col gap-2", className)} aria-busy>
        <Skeleton className="h-10 w-full" />
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div
        role="alert"
        className={cn(
          "border-danger-border bg-danger-bg text-danger-fg flex min-w-0 flex-wrap items-center gap-2 rounded-md border p-3 text-sm",
          className
        )}
      >
        <AlertTriangleIcon className="size-4 shrink-0" />
        <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
          Could not check access: {error.message}
        </span>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    );
  }
  if (!diagnosis) {
    return (
      <p className={cn("text-muted-foreground text-sm", className)}>
        Pick a person and a permission to see whether they have it, and why.
      </p>
    );
  }

  const d = diagnosis;
  return (
    <div className={cn("flex min-w-0 flex-col gap-3", className)}>
      <div
        role="status"
        className={cn(
          "flex min-w-0 items-start gap-3 rounded-md border p-3",
          d.granted
            ? "border-success-border bg-success-bg text-success-fg"
            : "border-danger-border bg-danger-bg text-danger-fg"
        )}
      >
        {d.granted ? (
          <CheckCircle2Icon className="mt-0.5 size-5 shrink-0" />
        ) : (
          <XCircleIcon className="mt-0.5 size-5 shrink-0" />
        )}
        <p className="min-w-0 text-sm [overflow-wrap:anywhere]">
          <span className="font-semibold">{d.granted ? "Yes. " : "No. "}</span>
          <span className="font-mono">{d.username}</span> {d.granted ? "can" : "cannot"}{" "}
          <span className="font-mono">{d.permission}</span>
          {target ? (
            <>
              {" "}
              on {SCOPE_NOUN[target.kind]} <span className="font-mono">{target.name}</span>
            </>
          ) : (
            " in this organization"
          )}
          {d.isSuperuser && ", as a superuser"}.
        </p>
      </div>

      <ol aria-label="Reasoning" className="max-h-96 min-w-0 overflow-auto rounded-md border">
        {d.steps.map((s, i) => {
          const tone = s.result ? "pass" : NEUTRAL_WHEN_FALSE.has(s.check) ? "info" : "fail";
          const bindings = parseBindingLabels(s.detail);
          return (
            <li key={`${s.check}-${i}`} className="flex min-w-0 gap-3 border-b p-3 last:border-b-0">
              <StepIcon tone={tone} />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <p className="text-sm font-medium">
                  {STEP_LABEL[s.check] ?? s.check}
                  <span className="sr-only">
                    : {tone === "pass" ? "yes" : tone === "fail" ? "no" : "no, not needed"}
                  </span>
                </p>
                {bindings ? (
                  <ul className="flex min-w-0 flex-wrap gap-1">
                    {bindings.map((b) => (
                      <li key={`${b.role}@${b.scopeKind}:${b.scopeId}`} className="min-w-0">
                        <BindingChip binding={b} href={bindingHref?.(b)} />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                    {s.detail}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function StepIcon({ tone }: { tone: "pass" | "fail" | "info" | "pending" }) {
  const Icon =
    tone === "pass"
      ? CheckCircle2Icon
      : tone === "fail"
        ? XCircleIcon
        : tone === "info"
          ? CircleIcon
          : CircleDashedIcon;
  return (
    <Icon
      aria-hidden
      className={cn(
        "mt-0.5 size-4 shrink-0",
        tone === "pass" && "text-success",
        tone === "fail" && "text-danger",
        (tone === "info" || tone === "pending") && "text-muted-foreground"
      )}
    />
  );
}

function BindingChip({ binding: b, href }: { binding: BindingLabel; href?: string }) {
  const body: React.ReactNode = (
    <>
      <span className="min-w-0 truncate">{b.role}</span>
      <span className="text-muted-foreground shrink-0">
        @{SCOPE_NOUN[b.scopeKind]}:{b.scopeId}
      </span>
    </>
  );
  const cls =
    "bg-muted inline-flex max-w-full min-w-0 items-center rounded-sm px-1.5 py-0.5 font-mono text-xs";
  return href ? (
    <Link
      href={href}
      className={cn(cls, "focus-visible:ring-ring hover:underline focus-visible:ring-2")}
    >
      {body}
    </Link>
  ) : (
    <span className={cls}>{body}</span>
  );
}

// ---------------------------------------------------------------------------
// Compare two people (permissionCompare)
// ---------------------------------------------------------------------------

export interface Comparison {
  userAUsername: string;
  userBUsername: string;
  onlyA: string[];
  onlyB: string[];
  shared: string[];
}

export interface AccessCompareProps {
  comparison: Comparison | null;
  loading?: boolean;
  error?: { message: string } | null;
  onRetry?: () => void;
  className?: string;
}

/**
 * Two people side by side (design 3.7, `?compare=`): what only the first
 * holds, what only the second holds, and what they share, from
 * `permissionCompare`. Each column scrolls in its own frame. Pure.
 */
export function AccessCompare({
  comparison: c,
  loading = false,
  error,
  onRetry,
  className,
}: AccessCompareProps) {
  if (loading) {
    return (
      <div className={cn("grid min-w-0 gap-3 md:grid-cols-3", className)} aria-busy>
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-40" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div
        role="alert"
        className={cn(
          "border-danger-border bg-danger-bg text-danger-fg flex min-w-0 flex-wrap items-center gap-2 rounded-md border p-3 text-sm",
          className
        )}
      >
        <AlertTriangleIcon className="size-4 shrink-0" />
        <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
          Could not compare: {error.message}
        </span>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    );
  }
  if (!c) {
    return (
      <p className={cn("text-muted-foreground text-sm", className)}>
        Pick two people to compare what they can do.
      </p>
    );
  }
  const cols: { title: React.ReactNode; slugs: string[]; tone?: string }[] = [
    {
      title: (
        <>
          Only <span className="font-mono">{c.userAUsername}</span>
        </>
      ),
      slugs: c.onlyA,
      tone: "text-info-fg",
    },
    {
      title: (
        <>
          Only <span className="font-mono">{c.userBUsername}</span>
        </>
      ),
      slugs: c.onlyB,
      tone: "text-info-fg",
    },
    { title: "Both", slugs: c.shared },
  ];
  return (
    <div className={cn("grid min-w-0 gap-3 md:grid-cols-3", className)}>
      {cols.map((col, i) => (
        <section key={i} className="flex min-w-0 flex-col rounded-md border">
          <h3 className="flex min-w-0 items-baseline gap-2 border-b px-3 py-2 text-sm font-medium">
            <span className="min-w-0 truncate">{col.title}</span>
            <span className="text-muted-foreground ml-auto font-mono text-xs tabular-nums">
              {col.slugs.length}
            </span>
          </h3>
          {col.slugs.length === 0 ? (
            <p className="text-muted-foreground p-3 text-xs">None.</p>
          ) : (
            <ul className="max-h-72 min-w-0 overflow-auto p-2">
              {col.slugs.map((s) => (
                <li
                  key={s}
                  className={cn("min-w-0 truncate px-1 py-0.5 font-mono text-xs", col.tone)}
                  title={s}
                >
                  {s}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
