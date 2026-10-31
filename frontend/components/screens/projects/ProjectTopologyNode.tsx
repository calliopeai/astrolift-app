"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { BotIcon, BoxIcon, GitBranchIcon, LayersIcon, WorkflowIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import type { TopologyNodeData } from "./project-topology-model";

const icons = {
  workflow: WorkflowIcon,
  stage: LayersIcon,
  agent: BotIcon,
  app: BoxIcon,
  repository: GitBranchIcon,
};
export function ProjectTopologyNode({ data, selected }: NodeProps<Node<TopologyNodeData>>) {
  const t = useTranslations("projectTopology");
  const Icon = icons[data.kind];
  return (
    <div
      className={cn(
        "bg-card w-56 rounded-lg border p-3 shadow-sm",
        selected && "border-primary ring-primary ring-2"
      )}
    >
      <Handle type="target" position={Position.Left} />
      <div className="text-muted-foreground mb-2 flex items-center gap-2 text-xs">
        <Icon className="size-4" />
        {t(`kind.${data.kind}`)}
      </div>
      <div className="line-clamp-2 text-sm font-semibold" title={data.label}>
        {data.label}
      </div>
      <div
        className={cn(
          "mt-2 flex items-center gap-2 text-xs",
          data.status === "running"
            ? "text-info-fg"
            : data.status === "failed"
              ? "text-danger-fg"
              : "text-muted-foreground"
        )}
      >
        <span
          className={cn(
            "size-1.5 rounded-full bg-current",
            data.status === "running" && "motion-safe:animate-pulse"
          )}
        />
        {data.status}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
