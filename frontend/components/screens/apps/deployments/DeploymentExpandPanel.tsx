"use client";

import {
  CheckCircle2Icon,
  ClipboardIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitCommitIcon,
  XCircleIcon,
  XIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
} from "@/graphql/lifecycle/lifecycle.types";
import { cn } from "@/lib/utils";

import { formatDuration, formatLogTime, githubCommitUrl } from "./app-deployments-format";
import type { useDeploymentDetail } from "./use-deployment-detail";

export type DeploymentExpandPanelViewProps = ReturnType<typeof useDeploymentDetail> & {
  deployment: AstroliftDeployment;
  /** The app's `owner/repo`, for commit links; empty when it has none. */
  repoFullName: string;
  onClose: () => void;
};

/**
 * The inline detail under an opened deployment row: outcome banner, build
 * and manifest errors, the applied manifests by kind, the deployment log
 * and the commit / CI metadata. The screen places it in a full-width row.
 */
export function DeploymentExpandPanelView({
  deployment,
  repoFullName,
  onClose,
  logEntries,
  logLoading,
  manifest,
  manifestLoading,
  envUrl,
}: DeploymentExpandPanelViewProps) {
  const d = deployment;
  const success = d.status === "running";
  const failed = d.status === "failed";

  return (
    <div className="border-border/60 space-y-4 border-t px-6 py-4">
      {/* Header bar */}
      <div className="flex flex-wrap items-center gap-3">
        <DeploymentStatusPill status={d.status} />
        <span className="font-mono text-sm">{d.imageTag || d.id.slice(0, 12)}</span>
        <span className="text-muted-foreground text-xs">·</span>
        <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
          <ClockIcon className="size-3" />
          {formatDuration(d.durationSeconds)}
        </span>
        <span className="text-muted-foreground text-xs">·</span>
        <Badge variant="outline" className="font-mono text-xs">
          {d.environmentName}
        </Badge>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={onClose}
          aria-label="Close panel"
        >
          <XIcon className="size-3.5" />
          Close
        </Button>
      </div>

      {/* Success / failure banner */}
      {success && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-[color:var(--brand-primary)]/30 bg-[color:var(--brand-primary)]/10 px-3 py-2 text-sm">
          <CheckCircle2Icon className="size-4 text-[color:var(--brand-primary)]" />
          <span className="font-medium">Deployment successful!</span>
          {envUrl && (
            <a
              href={envUrl}
              target="_blank"
              rel="noreferrer"
              className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-[color:var(--brand-primary)] hover:underline"
            >
              Open app
              <ExternalLinkIcon className="size-3" />
            </a>
          )}
        </div>
      )}
      {failed && (
        <div className="border-destructive/30 bg-destructive/10 flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm">
          <XCircleIcon className="text-destructive size-4" />
          <span className="font-medium">Deployment failed.</span>
          {(d.abortedReason || d.statusReason) && (
            <span className="text-destructive/90 font-mono text-xs [overflow-wrap:anywhere]">
              {d.abortedReason || d.statusReason}
            </span>
          )}
        </div>
      )}
      {!failed && !success && d.statusReason && (
        <div className="border-warning-border bg-warning-bg text-warning-fg flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm">
          <ClockIcon className="size-4" />
          <span className="[overflow-wrap:anywhere]">{d.statusReason}</span>
        </div>
      )}
      {d.buildError && <ErrorBlock title="Build output" body={d.buildError} />}
      {d.manifestResyncError && (
        <ErrorBlock
          title={`Manifest not refreshed (${d.manifestResyncStatus || "error"})`}
          body={d.manifestResyncError}
        />
      )}

      {/* Applied manifests */}
      <ManifestTabs manifest={manifest} loading={manifestLoading} />

      {/* Deployment log */}
      <DeploymentLog entries={logEntries} loading={logLoading} />

      {/* Secondary commit / branch / CI metadata row */}
      <CommitMetaRow deployment={d} repoFullName={repoFullName} />
    </div>
  );
}

function ErrorBlock({ title, body }: { title: string; body: string }) {
  return (
    <section className="space-y-2">
      <h4 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
        {title}
      </h4>
      <pre className="bg-muted/40 max-h-64 overflow-auto rounded-md p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
        {body}
      </pre>
    </section>
  );
}

// ─── Manifest tabs ─────────────────────────────────────────────────────

interface ManifestForTabs {
  resources: unknown;
  error?: string | null;
  errorPath?: string | null;
  errorLine?: number | null;
}

function ManifestTabs({
  manifest,
  loading,
}: {
  manifest: ManifestForTabs | null | undefined;
  loading: boolean;
}) {
  const tabs = React.useMemo(() => buildManifestTabs(manifest?.resources), [manifest?.resources]);
  // Track only the user's tab selection. The effective active tab falls
  // back to the first available kind whenever the user hasn't picked
  // one yet or the manifest shape changed and their selection no longer
  // exists — this avoids a setState-in-effect cascade.
  const [override, setOverride] = React.useState<string | null>(null);
  const activeTab = (override && tabs.find((t) => t.id === override)) || tabs[0] || null;
  const body = activeTab ? JSON.stringify(activeTab.payload, null, 2) : "";

  async function copyBody() {
    if (!body) return;
    try {
      await navigator.clipboard.writeText(body);
      toast.success("Manifest copied to clipboard");
    } catch {
      toast.error("Couldn't copy — clipboard access blocked");
    }
  }

  return (
    <section className="space-y-2">
      <h4 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
        Applied manifests
      </h4>
      {loading && !manifest ? (
        <Skeleton className="h-32 w-full" />
      ) : manifest?.error ? (
        <div className="text-destructive text-xs">
          <p className="font-medium">Manifest could not be rendered.</p>
          <p className="mt-1 font-mono">{manifest.error}</p>
          {manifest.errorPath && (
            <p className="text-muted-foreground mt-1 font-mono">
              {manifest.errorPath}
              {manifest.errorLine != null && ` :${manifest.errorLine}`}
            </p>
          )}
        </div>
      ) : tabs.length === 0 ? (
        <p className="text-muted-foreground text-xs">No manifest available for this deployment.</p>
      ) : (
        <div className="space-y-2">
          <div className="border-border flex flex-wrap items-center gap-1 border-b">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setOverride(t.id)}
                className={cn(
                  "-mb-px border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                  activeTab?.id === t.id
                    ? "border-foreground text-foreground"
                    : "text-muted-foreground hover:text-foreground border-transparent"
                )}
              >
                {t.label}
                {t.count > 1 && (
                  <span className="text-muted-foreground ml-1 tabular-nums">{t.count}</span>
                )}
              </button>
            ))}
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto h-7"
              onClick={copyBody}
              disabled={!body}
            >
              <ClipboardIcon className="size-3.5" />
              Copy
            </Button>
          </div>
          <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs leading-relaxed">
            {body}
          </pre>
        </div>
      )}
    </section>
  );
}

interface ManifestTab {
  id: string;
  label: string;
  count: number;
  payload: unknown;
}

const KNOWN_K8S_KINDS = new Set([
  "Deployment",
  "StatefulSet",
  "DaemonSet",
  "Service",
  "Ingress",
  "ConfigMap",
  "Secret",
  "CronJob",
  "Job",
  "HorizontalPodAutoscaler",
  "ServiceAccount",
  "Role",
  "RoleBinding",
  "ClusterRole",
  "ClusterRoleBinding",
  "PersistentVolumeClaim",
  "NetworkPolicy",
  "PodDisruptionBudget",
]);

/**
 * Group rendered manifest resources by Kubernetes kind into one tab per
 * kind. Handles both shapes the backend produces:
 *   - an array of `{ kind, ...spec }` resources (the typical shape)
 *   - a `Record<kind, spec | spec[]>` keyed by kind (the legacy shape)
 *
 * Anything else falls back to a single "Manifest" tab carrying the raw
 * payload so the operator can still inspect / copy the rendered JSON.
 */
function buildManifestTabs(resources: unknown): ManifestTab[] {
  if (resources == null) return [];
  if (Array.isArray(resources)) {
    const groups = new Map<string, unknown[]>();
    for (const item of resources) {
      if (item && typeof item === "object" && "kind" in (item as Record<string, unknown>)) {
        const kind = String((item as { kind?: unknown }).kind ?? "Manifest");
        const bucket = groups.get(kind) ?? [];
        bucket.push(item);
        groups.set(kind, bucket);
      }
    }
    if (groups.size > 0) {
      return Array.from(groups.entries())
        .map(([kind, items]) => ({
          id: kind,
          label: kind,
          count: items.length,
          payload: items.length === 1 ? items[0] : items,
        }))
        .sort((a, b) => kindOrder(a.id) - kindOrder(b.id) || a.label.localeCompare(b.label));
    }
    // Array of opaque entries — fall through to the catch-all single tab.
    return [{ id: "manifest", label: "Manifest", count: resources.length, payload: resources }];
  }
  if (typeof resources === "object") {
    const record = resources as Record<string, unknown>;
    const keys = Object.keys(record);
    const k8sKeys = keys.filter((k) => KNOWN_K8S_KINDS.has(k));
    if (k8sKeys.length > 0) {
      return k8sKeys
        .map((kind) => {
          const value = record[kind];
          const count = Array.isArray(value) ? value.length : 1;
          return { id: kind, label: kind, count, payload: value };
        })
        .sort((a, b) => kindOrder(a.id) - kindOrder(b.id) || a.label.localeCompare(b.label));
    }
  }
  return [{ id: "manifest", label: "Manifest", count: 1, payload: resources }];
}

const KIND_ORDER: Record<string, number> = {
  Deployment: 0,
  StatefulSet: 1,
  DaemonSet: 2,
  CronJob: 3,
  Job: 4,
  Service: 10,
  Ingress: 11,
  HorizontalPodAutoscaler: 20,
  ConfigMap: 30,
  Secret: 31,
  ServiceAccount: 40,
  Role: 41,
  RoleBinding: 42,
  ClusterRole: 43,
  ClusterRoleBinding: 44,
  PersistentVolumeClaim: 50,
  NetworkPolicy: 60,
  PodDisruptionBudget: 70,
};

function kindOrder(kind: string): number {
  return KIND_ORDER[kind] ?? 100;
}

// ─── Deployment log ────────────────────────────────────────────────────

function DeploymentLog({
  entries,
  loading,
}: {
  entries: AstroliftDeploymentLogEntry[];
  loading: boolean;
}) {
  return (
    <section className="space-y-2">
      <h4 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
        Deployment log
      </h4>
      {loading && entries.length === 0 ? (
        <Skeleton className="h-24 w-full" />
      ) : entries.length === 0 ? (
        <p className="text-muted-foreground text-xs">No log entries for this deployment yet.</p>
      ) : (
        <ol className="bg-muted/40 max-h-64 space-y-1 overflow-auto rounded-md p-3 font-mono text-xs leading-snug">
          {entries.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline gap-2">
              <span className="text-muted-foreground tabular-nums">
                {formatLogTime(e.occurredAt)}
              </span>
              <Badge variant="outline" className="text-2xs capitalize">
                {e.status.replace(/_/g, " ")}
              </Badge>
              <span className="text-foreground break-words">{e.message || "—"}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

// ─── Commit / branch / CI metadata row ─────────────────────────────────

function CommitMetaRow({
  deployment,
  repoFullName,
}: {
  deployment: AstroliftDeployment;
  repoFullName: string;
}) {
  const d = deployment;
  if (!d.commitSha && !d.branch && !d.commitAuthor && !d.ciRunUrl && !d.prNumber && !d.repoUrl) {
    return null;
  }
  const sha = d.commitSha ? d.commitSha.slice(0, 7) : null;
  return (
    <div className="text-muted-foreground text-2xs flex flex-wrap items-center gap-x-4 gap-y-1">
      {sha && (
        <span className="inline-flex items-center gap-1">
          <GitCommitIcon className="size-3" />
          {repoFullName ? (
            <a
              href={githubCommitUrl(repoFullName, d.commitSha)}
              target="_blank"
              rel="noreferrer"
              className="hover:text-foreground font-mono hover:underline"
            >
              {sha}
            </a>
          ) : (
            <span className="font-mono">{sha}</span>
          )}
          {d.branch && <span className="text-muted-foreground/80">· {d.branch}</span>}
        </span>
      )}
      {d.commitAuthor && <span>by {d.commitAuthor}</span>}
      {d.prNumber > 0 && d.prUrl && (
        <a
          href={d.prUrl}
          target="_blank"
          rel="noreferrer"
          className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
        >
          PR #{d.prNumber}
          <ExternalLinkIcon className="size-3" />
        </a>
      )}
      {d.ciRunUrl && (
        <a
          href={d.ciRunUrl}
          target="_blank"
          rel="noreferrer"
          className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
        >
          {d.ciProvider || "ci"} run
          <ExternalLinkIcon className="size-3" />
        </a>
      )}
      {repoFullName && d.repoUrl && (
        <a
          href={d.repoUrl}
          target="_blank"
          rel="noreferrer"
          className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
        >
          <span className="font-mono">{repoFullName}</span>
          <ExternalLinkIcon className="size-3" />
        </a>
      )}
    </div>
  );
}
