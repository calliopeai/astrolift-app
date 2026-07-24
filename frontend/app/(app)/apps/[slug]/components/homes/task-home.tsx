"use client";

import { CheckCircle2Icon, CircleIcon, ListChecksIcon, ScrollTextIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

interface TaskHomeProps {
  slug: string;
  name: string;
  workload: AstroliftWorkload;
}

// The one-off execution lifecycle, shown as a checklist — a task runs once,
// front to back, rather than staying up.
const STAGES = ["Queued", "Provisioning", "Running", "Completed"] as const;

/**
 * Primitive home for a **task** — a one-off run, presented as a checklist. The
 * execution reads as a top-to-bottom list of stages rather than the always-on
 * app chrome. Per-run output + re-run land here once the task-run feed is wired.
 */
export function TaskHome({ slug, name, workload }: TaskHomeProps) {
  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <ListChecksIcon className="text-muted-foreground size-5" />
          </span>
          <span>{name}</span>
          <Badge variant="outline" className="gap-1.5">
            <ListChecksIcon className="size-3" />
            Task
          </Badge>
        </span>
      }
      description={<span className="text-muted-foreground text-xs">Runs once, on demand.</span>}
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Execution checklist</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="space-y-3">
              {STAGES.map((stage, i) => (
                <li key={stage} className="flex items-center gap-3">
                  {i === STAGES.length - 1 ? (
                    <CheckCircle2Icon className="text-muted-foreground/40 size-5 shrink-0" />
                  ) : (
                    <CircleIcon className="text-muted-foreground/40 size-5 shrink-0" />
                  )}
                  <span className={cn("text-sm", "text-muted-foreground")}>{stage}</span>
                </li>
              ))}
            </ol>
            <p className="text-muted-foreground mt-4 text-xs">
              A one-off task moves through these stages each time it runs. The most recent run&rsquo;s
              status + output will light this up once the task-run feed lands.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ScrollTextIcon className="size-4" />
              Output
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              No run recorded yet.{" "}
              <Link
                href={`/apps/${slug}/workloads/${encodeURIComponent(workload.slug)}`}
                className="text-[var(--brand-primary)] hover:underline"
              >
                Open the workload
              </Link>{" "}
              for its manifest, config, and pod logs.
            </p>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
