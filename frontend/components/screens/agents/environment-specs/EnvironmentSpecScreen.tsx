"use client";

import { BoxesIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { QueryError } from "@/components/QueryError";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Skeleton } from "@/components/ui/skeleton";

import { agentsCrumbs } from "../skills/catalog";
import { type EnvironmentSpec, specOwner } from "./environment-specs";

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
  const title =
    spec?.name ??
    (loading && !error
      ? "Loading environment spec"
      : error
        ? "Environment spec"
        : "Environment spec not found");
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs("environment-specs", { label: title })}
        title={title}
        context={spec?.slug}
      />
      <QueryError title="Couldn't load this environment spec" error={error} onRetry={onRetry} />
      {!spec ? (
        loading && !error ? (
          <Skeleton className="h-48 w-full" aria-label="Loading environment spec" />
        ) : !error ? (
          <EmptyState
            icon={<BoxesIcon className="size-5" />}
            title="Environment spec not found"
            description="This recipe is unavailable or outside your access."
            actionHref="/agents/environment-specs"
            actionLabel="Back to environment specs"
          />
        ) : null
      ) : (
        <PanelGrid>
          <Panel title="Container environment" span={6}>
            <dl className="grid min-w-0 gap-3 text-sm">
              {[
                ["Runtime", spec.runtime || "Workload default"],
                ["Image override", spec.imageTag || "Runtime catalog"],
                ["Agent type", spec.agentType],
                ["Tool preset", spec.toolPreset || "Image defaults"],
                ["Owner", specOwner(spec)],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-muted-foreground text-xs">{label}</dt>
                  <dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title="Capabilities" span={6}>
            <dl className="grid gap-3 text-sm">
              {[
                ["VNC", spec.vncEnabled],
                ["Managed model", spec.managedModel],
                ["Model gateway", spec.modelGateway],
                ["Run as non-root", spec.runAsNonRoot],
                ["Allow tool installation", spec.allowInstall],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex justify-between gap-4">
                  <dt>{String(label)}</dt>
                  <dd>{value ? "Enabled" : "Disabled"}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title="Manifest source" span={12}>
            <dl className="grid min-w-0 gap-3 text-sm">
              {[
                ["Repository", spec.configRepo || "No repository recorded"],
                ["Branch", spec.configBranch],
                ["Manifest path", spec.configManifestPath || "Repository root"],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-muted-foreground text-xs">{label}</dt>
                  <dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel
            title="Secret references"
            description="Reference bindings only. Secret values are resolved when the agent launches."
            span={12}
          >
            <pre className="max-h-96 overflow-auto font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              {JSON.stringify(spec.secretRefs, null, 2)}
            </pre>
          </Panel>
        </PanelGrid>
      )}
    </div>
  );
}
