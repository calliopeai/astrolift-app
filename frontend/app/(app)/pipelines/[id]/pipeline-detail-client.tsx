"use client";

import { useQuery } from "@apollo/client/react";
import {
  HammerIcon,
  KeyIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_PIPELINE } from "@/graphql/pipelines/pipelines.queries";
import type { AstroliftPipeline } from "@/graphql/pipelines/pipelines.types";
import { cn } from "@/lib/utils";

import { PipelineSecretsTab } from "./secrets-tab";

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

type TabKey = "overview" | "secrets";

interface TabSpec {
  key: TabKey;
  label: string;
  icon: React.ReactNode;
}

const TABS: TabSpec[] = [
  {
    key: "overview",
    label: "Overview",
    icon: <HammerIcon className="size-3.5" />,
  },
  {
    key: "secrets",
    label: "Secrets",
    icon: <KeyIcon className="size-3.5" />,
  },
];

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PipelineResp {
  astroliftPipeline: AstroliftPipeline | null;
}

// ---------------------------------------------------------------------------
// PipelineDetailClient
// ---------------------------------------------------------------------------

export function PipelineDetailClient({ id }: { id: string }) {
  const pathname = usePathname() ?? "";

  // Derive active tab from the URL suffix.
  const activeTab: TabKey = pathname.endsWith("/secrets") ? "secrets" : "overview";

  const { data, loading } = useQuery<PipelineResp>(GET_PIPELINE, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });

  const pipeline = data?.astroliftPipeline;

  return (
    <PageShell
      title={
        loading && !pipeline ? (
          <Skeleton className="h-5 w-48" />
        ) : (
          (pipeline?.name ?? "Pipeline")
        )
      }
      description={pipeline?.description ?? undefined}
    >
      {/* Tab nav */}
      <div className="relative -mx-6">
        <nav
          aria-label="Pipeline tabs"
          className="border-border flex snap-x snap-mandatory gap-1 overflow-x-auto border-b px-6 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {TABS.map((tab) => {
            const isActive = activeTab === tab.key;
            const href =
              tab.key === "overview"
                ? `/pipelines/${id}`
                : `/pipelines/${id}/${tab.key}`;
            return (
              <Link
                key={tab.key}
                href={href}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "relative flex shrink-0 snap-start items-center gap-1.5 px-3 py-2.5 text-sm font-medium transition-colors",
                  isActive
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {tab.icon}
                {tab.label}
                {isActive && (
                  <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-[var(--brand-primary)]" />
                )}
              </Link>
            );
          })}
        </nav>
        <div
          aria-hidden
          className="from-background pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l to-transparent"
        />
      </div>

      {/* Tab content */}
      {activeTab === "overview" && (
        <PipelineOverviewTab pipeline={pipeline} loading={loading} />
      )}
      {activeTab === "secrets" && (
        <PipelineSecretsTab pipelineId={id} />
      )}
    </PageShell>
  );
}

// ---------------------------------------------------------------------------
// PipelineOverviewTab — placeholder content for the overview tab.
// Full overview (runs, jobs, triggers, runners) is tracked in separate
// pipeline frontend tickets (#99).
// ---------------------------------------------------------------------------

function PipelineOverviewTab({
  pipeline,
  loading,
}: {
  pipeline: AstroliftPipeline | null | undefined;
  loading: boolean;
}) {
  if (loading && !pipeline) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (!pipeline) {
    return (
      <p className="text-muted-foreground text-sm">Pipeline not found.</p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="text-muted-foreground rounded-md border p-4 text-sm">
        <p className="font-medium text-foreground">{pipeline.name}</p>
        {pipeline.description && (
          <p className="mt-1">{pipeline.description}</p>
        )}
        <p className="mt-3 text-xs">
          Pipeline runs, jobs, artifacts, triggers, and runner configuration
          will appear here. This view is under active development — see the
          Astrolift roadmap for timelines.
        </p>
      </div>
    </div>
  );
}
