"use client";

import { BrainCircuitIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DataTable, type Column, type CursorTableController } from "@/components/data-table";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ListModelEndpointsQuery } from "@/graphql/__generated__/operations";

import type { useModels } from "./use-models";

export type ModelEndpoint = ListModelEndpointsQuery["astroliftModelEndpoints"][number];

export type ModelsScreenProps = ReturnType<typeof useModels> & {
  /** Replicas controls for a hosted vLLM row (a container with its own mutation). */
  renderReplicas: (model: ModelEndpoint) => React.ReactNode;
  /** Test dialog for a hosted vLLM row (a container with its own mutation). */
  renderTest: (model: ModelEndpoint) => React.ReactNode;
  /** The Deploy model sheet, open while the Deploy model button's sheet is up. */
  renderDeploySheet: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;
};

// Self-hosted variants run on the org's own GPUs; the rest are cloud-served.
const HOSTED = new Set(["vllm", "kserve"]);

function modelId(config: Record<string, unknown>): string {
  const value = config.model ?? config.model_name ?? config.model_id ?? config.storage_uri;
  return typeof value === "string" ? value : "";
}

function gpuLabel(m: ModelEndpoint): string {
  if (!HOSTED.has(m.variant)) return "cloud";
  const config = (m.config ?? {}) as Record<string, unknown>;
  const gpu = Number(config.gpu ?? (m.variant === "vllm" ? 1 : 0));
  if (!gpu) return "CPU";
  const mig = typeof config.mig_profile === "string" ? ` × ${config.mig_profile}` : "";
  return `${gpu} GPU${gpu === 1 ? "" : "s"}${mig}`;
}

function ownerHref(m: ModelEndpoint): string {
  return m.ownerScope === "project"
    ? `/projects/${m.projectSlug}/resources`
    : `/apps/${m.registeredAppSlug}/managed-services`;
}

const noop = () => {};

// The query returns the org's whole list, so the table gets one page of it.
function singlePage<T>(
  rows: T[],
  loading: boolean,
  error: CursorTableController<T>["error"],
  refetch: () => void
): CursorTableController<T> {
  return {
    rows,
    state:
      loading && rows.length === 0
        ? "loading"
        : error
          ? "error"
          : rows.length === 0
            ? "empty"
            : "ready",
    error,
    retry: refetch,
    refetch,
    totalCount: rows.length,
    pageIndex: 0,
    hasNext: false,
    hasPrev: false,
    next: noop,
    prev: noop,
    pageSize: Math.max(rows.length, 1),
    setPageSize: noop,
    search: "",
    setSearch: noop,
    isStale: false,
    isSearching: false,
    searchEnabled: false,
    sort: undefined,
    toggleSort: noop,
    sortEnabled: false,
    isFiltered: false,
    clearFilters: noop,
  };
}

function columnsFor({
  renderReplicas,
  renderTest,
}: Pick<ModelsScreenProps, "renderReplicas" | "renderTest">): Column<ModelEndpoint>[] {
  return [
    { id: "name", header: "Name", cell: (m) => <span className="font-medium">{m.name}</span> },
    {
      id: "model",
      header: "Model",
      cellClassName: "font-mono text-xs",
      cell: (m) => modelId((m.config ?? {}) as Record<string, unknown>) || "—",
    },
    {
      id: "serving",
      header: "Serving",
      cell: (m) => (
        <Badge variant={HOSTED.has(m.variant) ? "default" : "secondary"}>{m.variant}</Badge>
      ),
    },
    {
      id: "hardware",
      header: "Hardware",
      cellClassName: "text-muted-foreground text-sm",
      cell: gpuLabel,
    },
    {
      id: "owner",
      header: "Owner",
      cell: (m) => (
        <Link className="hover:underline" href={ownerHref(m)}>
          {m.ownerScope === "project"
            ? m.projectSlug
            : `${m.registeredAppSlug} · ${m.environmentName}`}
        </Link>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (m) => (
        <Badge variant="outline" title={m.statusError || undefined}>
          {m.status}
        </Badge>
      ),
    },
    {
      id: "replicas",
      header: "Replicas",
      cell: (m) =>
        m.variant === "vllm" ? (
          renderReplicas(m)
        ) : (
          <span className="text-muted-foreground text-sm">—</span>
        ),
    },
    {
      id: "test",
      header: "Test",
      cell: (m) =>
        m.variant === "vllm" ? (
          renderTest(m)
        ) : (
          <span className="text-muted-foreground text-sm">—</span>
        ),
    },
  ];
}

/**
 * Models: every model endpoint the org runs (#2040), hosted on its own GPUs
 * (vLLM, KServe) or served by a cloud (Bedrock, Azure OpenAI, Foundry,
 * Vertex).
 */
export function ModelsScreen({
  models,
  loading,
  error,
  refetch,
  renderReplicas,
  renderTest,
  renderDeploySheet,
}: ModelsScreenProps) {
  const [deploying, setDeploying] = React.useState(false);
  const table = singlePage(models, loading, error, refetch);

  return (
    <PageShell
      title="Models"
      description="Model endpoints across your apps and projects: hosted on your own GPUs with vLLM or KServe, or served by Bedrock, Azure OpenAI, Azure AI Foundry and Vertex. Every one binds through the same MODEL_* variables."
      actions={<Button onClick={() => setDeploying(true)}>Deploy model</Button>}
    >
      <DataTable
        label="Model endpoints"
        controller={table}
        columns={columnsFor({ renderReplicas, renderTest })}
        getRowId={(m) => m.id}
        empty={{
          icon: <BrainCircuitIcon className="size-5" />,
          title: "No models yet",
          description:
            "Add a model_endpoint service to an app or project: vllm to host an open-weight model on your GPUs, or your cloud's managed model service.",
        }}
        emptyFiltered={{ title: "No matching models", description: "No model matches." }}
      />
      {renderDeploySheet({ open: deploying, onOpenChange: setDeploying })}
    </PageShell>
  );
}
