import { ArrowRightIcon, BotIcon, GitForkIcon, UserCheckIcon } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import type { WorkflowTopologyStage } from "@/graphql/workflows/tiered.types";
import { cn } from "@/lib/utils";

export function formatWorkflowModel(model: string): string {
  const normalized = model.replace(/^(?:us|global)\.anthropic\./, "").replace(/^anthropic\./, "");
  const match = normalized.match(/^claude-(haiku|sonnet|opus)-(\d+)-(\d+)/i);
  if (!match) return normalized || "Platform default";
  const tier = match[1].charAt(0).toUpperCase() + match[1].slice(1).toLowerCase();
  return `Claude ${tier} ${match[2]}.${match[3]}`;
}

function stageLabel(stage: WorkflowTopologyStage): string {
  if (stage.role) return stage.role.replace(/[_-]+/g, " ");
  return stage.kind.replace(/[_-]+/g, " ");
}

function StageIcon({ kind }: { kind: string }) {
  if (kind === "human_gate") return <UserCheckIcon className="size-4" />;
  if (kind === "aggregation") return <GitForkIcon className="size-4" />;
  return <BotIcon className="size-4" />;
}

export function WorkflowTopology({
  stages,
  className,
}: {
  stages: WorkflowTopologyStage[];
  className?: string;
}) {
  const ordered = [...stages].sort((a, b) => a.order - b.order);
  if (ordered.length === 0) {
    return <p className="text-muted-foreground text-sm">No stages declared.</p>;
  }

  return (
    <ol
      aria-label="Workflow stage topology"
      className={cn("flex items-stretch gap-2 overflow-x-auto pb-2", className)}
    >
      {ordered.map((stage, index) => {
        const agentSlug = stage.agentSlug || stage.agentRef;
        return (
          <li key={stage.guid} className="flex min-w-0 items-center gap-2">
            <div className="bg-card max-w-64 min-w-52 rounded-lg border p-3 shadow-xs">
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="bg-primary/10 text-primary flex size-7 shrink-0 items-center justify-center rounded-full">
                    <StageIcon kind={stage.kind} />
                  </span>
                  <div className="min-w-0">
                    <p className="text-muted-foreground text-2xs font-medium uppercase">
                      Stage {stage.order + 1}
                    </p>
                    <p className="truncate text-sm font-semibold capitalize">{stageLabel(stage)}</p>
                  </div>
                </div>
                <Badge variant="outline" className="text-2xs shrink-0">
                  {stage.onFailure === "fail" ? "fail closed" : stage.onFailure}
                </Badge>
              </div>

              <div className="mt-3 space-y-1.5 text-xs">
                {agentSlug ? (
                  <div>
                    <span className="text-muted-foreground">Agent </span>
                    <Link
                      href={`/agents/${encodeURIComponent(agentSlug)}/build`}
                      className="font-mono font-medium hover:underline"
                    >
                      {agentSlug}
                    </Link>
                  </div>
                ) : (
                  <div className="text-muted-foreground capitalize">
                    {stage.kind.replace(/_/g, " ")}
                  </div>
                )}
                {stage.resolvedModel && (
                  <div title={stage.resolvedModel}>
                    <span className="text-muted-foreground">Model </span>
                    <span>{formatWorkflowModel(stage.resolvedModel)}</span>
                  </div>
                )}
                {(stage.fanOutDynamic || stage.fanOutCount != null) && (
                  <div>
                    <span className="text-muted-foreground">Fan-out </span>
                    <span>{stage.fanOutDynamic ? "dynamic" : `${stage.fanOutCount} parallel`}</span>
                  </div>
                )}
                {stage.environmentSpecSlug && (
                  <div className="truncate" title={stage.environmentSpecSlug}>
                    <span className="text-muted-foreground">Environment </span>
                    <span className="font-mono">{stage.environmentSpecSlug}</span>
                  </div>
                )}
                {stage.hasPrompt && (
                  <div>
                    <span className="text-muted-foreground">Prompt </span>
                    <span>custom</span>
                  </div>
                )}
                {stage.skillRefs.length > 0 && (
                  <div className="truncate" title={stage.skillRefs.join(", ")}>
                    <span className="text-muted-foreground">Skills </span>
                    <span className="font-mono">{stage.skillRefs.join(", ")}</span>
                  </div>
                )}
                {stage.outputKey && (
                  <div className="truncate" title={stage.outputKey}>
                    <span className="text-muted-foreground">Output </span>
                    <span className="font-mono">{stage.outputKey}</span>
                  </div>
                )}
              </div>
            </div>
            {index < ordered.length - 1 && (
              <ArrowRightIcon aria-hidden className="text-muted-foreground size-4 shrink-0" />
            )}
          </li>
        );
      })}
    </ol>
  );
}
