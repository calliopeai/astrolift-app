"use client";

import { AlertTriangleIcon, BrainCircuitIcon, LayersIcon, ServerCrashIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Skeleton } from "@/components/ui/skeleton";

import {
  gpuLabel,
  isHosted,
  type ModelEndpoint,
  modelId,
  modelStatusDot,
  ownerHref,
  ownerLabel,
} from "./models-list";

export interface ModelDetailScreenProps {
  model: ModelEndpoint | null;
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** Stop, start and scale for a hosted vLLM model (a container with its own mutation). */
  replicas?: React.ReactNode;
  /** The Test button and dialog for a hosted vLLM model (a container with its own mutation). */
  test?: React.ReactNode;
}

const mono = (value: React.ReactNode) => (
  <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">{value}</span>
);

/**
 * Agents › Models › one model (spec 44 §5.2): the title row with its status
 * and Test, a failure's reason first, then the endpoint on one Panel and its
 * replicas on another. Pure view; the data half is useModelDetail, and the
 * replicas and test controls come in as slots.
 */
export function ModelDetailScreen({
  model,
  loading,
  error,
  onRetry,
  replicas,
  test,
}: ModelDetailScreenProps) {
  const t = useTranslations("modelDetail");
  if (!model) {
    const pending = loading && !error;
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={agentsCrumbs("models", { label: pending ? t("loading") : t("notFound") })}
          title={pending ? <Skeleton className="h-6 w-48" /> : t("notFoundTitle")}
        />
        {pending ? (
          <div className="grid min-w-0 grid-cols-12 gap-4" aria-busy>
            <Skeleton className="col-span-12 h-48 w-full xl:col-span-8" />
            <Skeleton className="col-span-12 h-48 w-full xl:col-span-4" />
          </div>
        ) : error ? (
          <div
            role="alert"
            className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
          >
            <ServerCrashIcon className="text-danger size-5" aria-hidden />
            <div className="min-w-0 px-6">
              <p className="font-medium">{t("loadError")}</p>
              <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
                {error.message}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={onRetry}>
              {t("retry")}
            </Button>
          </div>
        ) : (
          <EmptyState
            icon={<AlertTriangleIcon className="size-5" />}
            title={t("notFoundTitle")}
            description={t("notFoundDescription")}
            actionHref="/models"
            actionLabel={t("backToModels")}
          />
        )}
      </div>
    );
  }

  const vllm = model.variant === "vllm";

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs("models", { label: model.name })}
        title={<span title={model.name}>{model.name}</span>}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm">
            <StatusDot status={modelStatusDot(model.status)} />
            {model.status}
          </span>
        }
        context={
          <>
            {model.variant} · <span className="font-mono">{gpuLabel(model)}</span>
          </>
        }
        primaryAction={vllm ? test : undefined}
      />

      <PanelGrid>
        <Panel
          title={t("endpoint")}
          icon={<BrainCircuitIcon className="size-4" />}
          span={8}
          failure={
            model.statusError ? { title: t("endpointFailed"), reason: model.statusError } : null
          }
        >
          <DefinitionList
            items={[
              { term: t("model"), description: mono(modelId(model) || t("none")) },
              {
                term: t("serving"),
                description: (
                  <Badge variant={isHosted(model) ? "default" : "secondary"}>{model.variant}</Badge>
                ),
              },
              { term: t("hardware"), description: mono(gpuLabel(model)) },
              {
                term: t("owner"),
                description: (
                  <Link
                    href={ownerHref(model)}
                    className="min-w-0 font-mono text-xs [overflow-wrap:anywhere] hover:underline"
                  >
                    {ownerLabel(model)}
                  </Link>
                ),
              },
              {
                term: t("cluster"),
                description: model.clusterSlug ? (
                  <Link
                    href={`/clusters/${model.clusterSlug}`}
                    className="min-w-0 font-mono text-xs [overflow-wrap:anywhere] hover:underline"
                  >
                    {model.clusterSlug}
                  </Link>
                ) : (
                  mono(t("none"))
                ),
              },
              {
                term: t("binding"),
                description: mono("MODEL_ENDPOINT_URL · MODEL_API_KEY · MODEL_DEPLOYMENT_NAME"),
              },
            ]}
          />
        </Panel>

        <Panel
          title={t("replicas")}
          icon={<LayersIcon className="size-4" />}
          span={4}
          description={vllm ? t("replicasDescription") : undefined}
        >
          {vllm ? (
            replicas
          ) : (
            <p className="text-muted-foreground text-sm">
              {isHosted(model) ? t("kserveScales") : t("providerScales")}
            </p>
          )}
        </Panel>
      </PanelGrid>
    </div>
  );
}
