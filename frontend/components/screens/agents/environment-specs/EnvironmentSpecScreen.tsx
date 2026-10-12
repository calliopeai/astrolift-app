"use client";

import { BoxesIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { EmptyState } from "@/components/EmptyState";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { QueryError } from "@/components/QueryError";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Skeleton } from "@/components/ui/skeleton";

import { agentsCrumbs } from "../skills/catalog";
import type { EnvironmentSpec } from "./environment-specs";

export interface EnvironmentSpecScreenProps {
  spec: EnvironmentSpec | null;
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
}

export function EnvironmentSpecScreen({
  spec,
  loading,
  error,
  onRetry,
}: EnvironmentSpecScreenProps) {
  const t = useTranslations("agentEnvSpecs");
  const title =
    spec?.name ?? (loading && !error ? t("loading") : error ? t("envSpec") : t("notFound"));
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs("environment-specs", { label: title })}
        title={title}
        context={spec?.slug}
      />
      <QueryError title={t("loadError")} error={error} onRetry={onRetry} />
      {!spec ? (
        loading && !error ? (
          <Skeleton className="h-48 w-full" aria-label={t("loading")} />
        ) : !error ? (
          <EmptyState
            icon={<BoxesIcon className="size-5" />}
            title={t("notFound")}
            description={t("notFoundDescription")}
            actionHref="/agents/environment-specs"
            actionLabel={t("back")}
          />
        ) : null
      ) : (
        <PanelGrid>
          <Panel title={t("container")} span={6}>
            <dl className="grid min-w-0 gap-3 text-sm">
              {[
                [t("runtime"), spec.runtime || t("workloadDefault")],
                [t("imageOverride"), spec.imageTag || t("runtimeCatalog")],
                [t("agentType"), spec.agentType],
                [t("toolPreset"), spec.toolPreset || t("imageDefaults")],
                [
                  t("owner"),
                  t(`owners.${spec.projectId ? "project" : spec.teamId ? "team" : "org"}`),
                ],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-muted-foreground text-xs">{label}</dt>
                  <dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title={t("capabilities")} span={6}>
            <dl className="grid gap-3 text-sm">
              {[
                [t("vnc"), spec.vncEnabled],
                [t("managedModel"), spec.managedModel],
                [t("modelGateway"), spec.modelGateway],
                [t("nonRoot"), spec.runAsNonRoot],
                [t("allowInstall"), spec.allowInstall],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex justify-between gap-4">
                  <dt>{String(label)}</dt>
                  <dd>{value ? t("enabled") : t("disabled")}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title={t("manifestSource")} span={12}>
            <dl className="grid min-w-0 gap-3 text-sm">
              {[
                [t("repository"), spec.configRepo || t("noRepository")],
                [t("branch"), spec.configBranch],
                [t("manifestPath"), spec.configManifestPath || t("repositoryRoot")],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-muted-foreground text-xs">{label}</dt>
                  <dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title={t("secretRefs")} description={t("secretRefsDescription")} span={12}>
            <pre className="max-h-96 overflow-auto font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              {JSON.stringify(spec.secretRefs, null, 2)}
            </pre>
          </Panel>
        </PanelGrid>
      )}
    </div>
  );
}
