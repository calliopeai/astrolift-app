"use client";

import { Loader2Icon, PlayIcon, ScrollTextIcon } from "lucide-react";
import Link from "next/link";

import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

export interface WorkloadRowActionsProps {
  workload: Pick<AstroliftWorkload, "slug" | "kind">;
  /** The workload's logs on the app's Logs & metrics tab. */
  logsHref: string;
  /** Where a CronJob can run now, one item each. */
  environments: { id: string; name: string }[];
  /** The job being dispatched, if any. */
  pendingSlug: string | null;
  /** Without app.deploy there is no Run now. */
  canRun: boolean;
  onRun: (jobSlug: string, environmentName: string) => void;
}

/**
 * A workload row's `⋯` items on the Workloads tab: its logs, and for a
 * CronJob, Run now in each environment (what the scheduled jobs view
 * offered before jobs folded into the tab as a kind).
 */
export function WorkloadRowActions({
  workload: w,
  logsHref,
  environments,
  pendingSlug,
  canRun,
  onRun,
}: WorkloadRowActionsProps) {
  const pending = pendingSlug === w.slug;
  const runnable = w.kind === "cronjob" && canRun;
  return (
    <>
      <DropdownMenuItem asChild>
        <Link href={logsHref}>
          <ScrollTextIcon className="size-4" />
          Open logs
        </Link>
      </DropdownMenuItem>
      {runnable && <DropdownMenuSeparator />}
      {runnable && environments.length === 0 && (
        <DropdownMenuItem disabled>Run now: no environments yet</DropdownMenuItem>
      )}
      {runnable &&
        environments.map((env) => (
          <DropdownMenuItem
            key={env.id}
            disabled={pending}
            onSelect={() => onRun(w.slug, env.name)}
            className="min-w-0"
          >
            {pending ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <PlayIcon className="size-4" />
            )}
            <span className="min-w-0 truncate">
              Run now in <span className="font-mono">{env.name}</span>
            </span>
          </DropdownMenuItem>
        ))}
    </>
  );
}
