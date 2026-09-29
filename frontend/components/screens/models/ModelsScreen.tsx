"use client";

import { BrainCircuitIcon, RocketIcon } from "lucide-react";
import Link from "next/link";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import {
  gpuLabel,
  isHosted,
  type ModelEndpoint,
  modelId,
  modelStatusDot,
  ownerHref,
  ownerLabel,
} from "./models-list";
import { replicasOf } from "./use-model-replicas";
import type { ModelsState } from "./use-models";

export type { ModelEndpoint } from "./models-list";

export type ModelsScreenProps = ModelsState;

// The row's link is an ::after overlay stretched across the whole row; a
// link in a cell has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

const columns: Column<ModelEndpoint>[] = [
  {
    id: "model",
    header: "Model",
    sortKey: "name",
    cellClassName: "max-w-80",
    cell: (m) => (
      <span className="block min-w-0">
        <span className="block truncate font-medium" title={m.name}>
          {m.name}
        </span>
        <span className="text-muted-foreground block truncate font-mono text-xs" title={modelId(m)}>
          {modelId(m) || "no model id"}
        </span>
      </span>
    ),
  },
  {
    id: "serving",
    header: "Serving",
    sortKey: "variant",
    cell: (m) => <Badge variant={isHosted(m) ? "default" : "secondary"}>{m.variant}</Badge>,
  },
  {
    id: "hardware",
    header: "Hardware",
    cell: (m) => <span className="text-muted-foreground font-mono text-xs">{gpuLabel(m)}</span>,
  },
  {
    id: "owner",
    header: "Owner",
    cellClassName: `${ABOVE_ROW_LINK} max-w-64`,
    cell: (m) => (
      <Link
        className="block truncate font-mono text-xs hover:underline"
        href={ownerHref(m)}
        title={ownerLabel(m)}
      >
        {ownerLabel(m)}
      </Link>
    ),
  },
  {
    id: "status",
    header: "Status",
    sortKey: "status",
    cell: (m) => (
      <span
        className="inline-flex min-w-0 items-center gap-1.5 text-sm"
        title={m.statusError || undefined}
      >
        <StatusDot status={modelStatusDot(m.status)} />
        <span className="truncate">{m.status}</span>
      </span>
    ),
  },
  {
    id: "replicas",
    header: "Replicas",
    align: "right",
    cell: (m) =>
      m.variant === "vllm" ? (
        <span className="font-mono text-xs">
          {replicasOf((m.config ?? {}) as Record<string, unknown>)}
        </span>
      ) : (
        <span className="text-muted-foreground text-xs">provider</span>
      ),
  },
];

/**
 * Agents › Models (spec 44 §4.4, §5.1): every model endpoint the org runs
 * (#2040), hosted on its own GPUs (vLLM, KServe) or served by a cloud
 * (Bedrock, Azure OpenAI, Foundry, Vertex), on the shared list. Each row
 * opens the model, where its replicas and the test prompt are; Deploy model
 * is a stepped page. Pure view; the data half is useModels.
 */
export function ModelsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: ModelsScreenProps) {
  return (
    <ListPage<ModelEndpoint>
      header={{
        crumbs: agentsCrumbs("models"),
        title: "Models",
        context:
          "Hosted on your own GPUs or served by your cloud; every one binds through the same MODEL_* variables.",
        primaryAction: (
          <Button size="sm" asChild>
            <Link href="/models/deploy">
              <RocketIcon className="size-4" />
              Deploy model
            </Link>
          </Button>
        ),
      }}
      list={list}
      label="Models"
      columns={columns}
      rows={rows}
      getRowId={(m) => m.id}
      rowHref={(m) => `/models/${m.id}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BrainCircuitIcon className="size-5" />,
        title: "No models yet",
        description:
          "Add a model_endpoint service to an app or project: vllm to host an open-weight model on your GPUs, or your cloud's managed model service.",
        actionHref: "/models/deploy",
        actionLabel: "Deploy model",
      }}
      totalCount={totalCount}
    />
  );
}
