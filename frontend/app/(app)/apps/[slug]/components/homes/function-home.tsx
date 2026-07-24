"use client";

import { ActivityIcon, BoltIcon, GlobeIcon, ZapIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

interface FunctionHomeProps {
  slug: string;
  name: string;
  workload: AstroliftWorkload;
  /** Public host when the function is HTTP-triggered; empty otherwise. */
  host: string | null;
}

/**
 * Primitive home for a **function** — serverless framing: how it's triggered
 * (HTTP endpoint or event) up top, then invocation history. Scale-to-zero, no
 * long-running deploy chrome.
 */
export function FunctionHome({ slug, name, workload, host }: FunctionHomeProps) {
  const url = workload.isPublic && host ? `https://${host}` : null;

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <BoltIcon className="text-muted-foreground size-5" />
          </span>
          <span>{name}</span>
          <Badge variant="outline" className="gap-1.5">
            <BoltIcon className="size-3" />
            Function
          </Badge>
        </span>
      }
      description={
        <span className="text-muted-foreground text-xs">
          Serverless — runs on invoke, scales to zero when idle.
        </span>
      }
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ZapIcon className="size-4" />
              Trigger
            </CardTitle>
          </CardHeader>
          <CardContent>
            {url ? (
              <div className="space-y-1">
                <div className="text-muted-foreground text-xs uppercase tracking-wide">
                  HTTP endpoint
                </div>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 font-mono text-sm text-[var(--brand-primary)] hover:underline"
                >
                  <GlobeIcon className="size-3.5" />
                  {host}
                </a>
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">
                Event-triggered (no public HTTP endpoint). Invoked by the platform or an upstream
                producer.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ActivityIcon className="size-4" />
              Invocations
            </CardTitle>
          </CardHeader>
          <CardContent>
            <EmptyState
              icon={<BoltIcon className="size-5" />}
              title="No invocations recorded yet"
              description="Per-invocation count, latency, and errors appear here once the function metrics feed is wired. Open the workload for config + logs in the meantime."
              secondary={
                <Link
                  href={`/apps/${slug}/workloads/${encodeURIComponent(workload.slug)}`}
                  className="text-sm text-[var(--brand-primary)] hover:underline"
                >
                  Open workload
                </Link>
              }
            />
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
