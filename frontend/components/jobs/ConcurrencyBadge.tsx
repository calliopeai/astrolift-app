"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { ConcurrencyPolicy } from "@/graphql/lifecycle/lifecycle.types";
import { cn } from "@/lib/utils";

/**
 * #427 — surfaces a cronjob's concurrency policy on the job card so
 * an operator can size the workload without reading the K8s spec:
 *
 *   forbid  → red    — blocks the next firing while one is in flight
 *   queue   → amber  — overlapping firings stack (K8s "Allow")
 *   replace → blue   — kills the in-flight run, starts fresh
 *
 * The shadcn badge ships with destructive/secondary/outline variants
 * but no warning/info — we compose Tailwind classes to get the
 * amber + blue surface colours without forking the variant table.
 */

const SURFACE: Record<
  ConcurrencyPolicy,
  { variant: "destructive" | "secondary"; classes: string }
> = {
  forbid: {
    variant: "destructive",
    classes: "",
  },
  queue: {
    variant: "secondary",
    classes: "bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/40",
  },
  replace: {
    variant: "secondary",
    classes: "bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/40",
  },
};

export interface ConcurrencyBadgeProps {
  policy: ConcurrencyPolicy | string;
  className?: string;
}

export function ConcurrencyBadge({ policy, className }: ConcurrencyBadgeProps) {
  const t = useTranslations("jobs.concurrency");
  // Defensive narrowing — the backend types this as ``String!``.
  // An unknown value renders as the muted outline so we never
  // throw at render time on a future enum addition.
  const known = isKnown(policy);
  const surface = known
    ? SURFACE[policy as ConcurrencyPolicy]
    : { variant: "secondary" as const, classes: "" };
  const label = known ? t(`label.${policy as ConcurrencyPolicy}`) : (policy as string);
  const tip = known ? t(`tooltip.${policy as ConcurrencyPolicy}`) : "";

  const badge = (
    <Badge
      variant={surface.variant}
      className={cn("capitalize", surface.classes, className)}
      data-policy={policy}
    >
      {label}
    </Badge>
  );

  if (!tip) return badge;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent>{tip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function isKnown(value: string): value is ConcurrencyPolicy {
  return value === "forbid" || value === "queue" || value === "replace";
}
