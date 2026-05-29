"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  CloudIcon,
  GlobeIcon,
  Loader2Icon,
  PlayIcon,
  RefreshCcwIcon,
  RocketIcon,
  ShieldIcon,
  Trash2Icon,
  UserIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  CLUSTER_BOOTSTRAP_PLAN,
  CLUSTER_BOOTSTRAP_RUNS,
  DECOMMISSION_CLUSTER,
  INSTALL_CLUSTER_PREREQS,
  LIST_CLUSTERS,
  REFRESH_CLUSTER_MANAGEMENT,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { ClusterTabs } from "../components/cluster-tabs";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

const POLL_INTERVAL_MS = 4000;

type Lifecycle = "registered" | "managing" | "managed" | "error";

const CAPABILITY_KEYS = [
  "cert_manager",
  "ingress",
  "service_mesh",
  "external_dns",
  "storage_classes",
  "metrics_server",
  "prometheus",
] as const;

/**
 * Cluster settings tab — owns every operator mutation that touches
 * cluster lifecycle (Bring into management, Refresh, Re-run preflight,
 * Force retrigger, Install prereqs, Decommission). Also surfaces the
 * Connection card, probed capabilities, last bootstrap, and the
 * driver-tuned bootstrap recipe. Danger-zone decommission lives at
 * the bottom in its own red-bordered section.
 */
export function ClusterSettingsClient({ slug }: { slug: string }) {
  const fmt = useFormatters();
  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);
  const lifecycle = (cluster?.lifecycle as Lifecycle | undefined) ?? "registered";

  React.useEffect(() => {
    if (lifecycle === "managing") {
      startPolling(POLL_INTERVAL_MS);
      return () => stopPolling();
    }
    stopPolling();
    return undefined;
  }, [lifecycle, startPolling, stopPolling]);

  const [bring, { loading: bringing }] = useMutation<{
    bringClusterIntoManagement: MutationResult<AstroliftTenantCluster>;
  }>(BRING_CLUSTER_INTO_MANAGEMENT, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [decommission, { loading: decommissioning }] = useMutation<{
    decommissionCluster: MutationResult<AstroliftTenantCluster>;
  }>(DECOMMISSION_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  // Decommission confirmation modal state. Two-step opt-in: open the
  // modal, then within the modal explicitly check "also delete cloud
  // infrastructure" if the operator wants the destructive path. Default
  // is the safe "just remove platform management" flow.
  const [decommissionOpen, setDecommissionOpen] = React.useState(false);
  const [decommissionDeleteInfra, setDecommissionDeleteInfra] = React.useState(false);

  async function handleDecommission() {
    if (!cluster) return;
    const { data } = await decommission({
      variables: {
        input: {
          clusterId: cluster.id,
          deleteCloudInfra: decommissionDeleteInfra,
        },
      },
    });
    if (data?.decommissionCluster.ok) {
      toast.success(
        decommissionDeleteInfra
          ? `Decommissioning ${cluster.slug} + deleting cloud infrastructure`
          : `Decommissioning ${cluster.slug} (cluster left running)`
      );
      setDecommissionOpen(false);
      setDecommissionDeleteInfra(false);
    } else {
      toast.error(data?.decommissionCluster.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleBring() {
    if (!cluster) return;
    const { data } = await bring({ variables: { input: { clusterId: cluster.id } } });
    if (data?.bringClusterIntoManagement.ok) {
      toast.success(`Bringing ${cluster.slug} into management`);
    } else {
      toast.error(data?.bringClusterIntoManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleRefresh(forcePreflight: boolean) {
    if (!cluster) return;
    const { data } = await refresh({
      variables: { input: { clusterId: cluster.id, forcePreflight } },
    });
    if (data?.refreshClusterManagement.ok) {
      toast.success(
        forcePreflight
          ? `Refreshing ${cluster.slug} (full preflight)`
          : `Refreshing ${cluster.slug}`
      );
    } else {
      toast.error(data?.refreshClusterManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  if (loading && !cluster) {
    return (
      <PageShell title="Cluster settings" description="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!cluster) {
    return (
      <PageShell
        title="Cluster not found"
        description="The cluster doesn't exist or you don't have permission to view it."
      >
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No cluster with slug ${slug}`}
          actionHref="/clusters"
          actionLabel="Back to clusters"
        />
      </PageShell>
    );
  }

  const caps = (cluster.capabilities ?? {}) as Record<string, unknown>;
  const certManager = (caps.cert_manager ?? {}) as { installed?: boolean };
  const ingress = (caps.ingress ?? {}) as { installed?: boolean };
  const missingHeadlinePrereq =
    lifecycle === "managed" && (!certManager.installed || !ingress.installed);

  // Lifecycle-aware management actions block. Mirrors the old overview
  // header — bring/refresh/re-run preflight/force retrigger, with the
  // top-level Decommission still living up here for convenience plus a
  // dedicated Danger zone at the bottom.
  const managementActions: React.ReactNode = (() => {
    if (lifecycle === "managing") {
      return (
        <Can permission="cluster.manage">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" disabled>
              <Loader2Icon className="size-4 animate-spin" />
              Setup in progress…
            </Button>
            <Button variant="outline" onClick={() => handleRefresh(true)} disabled={refreshing}>
              <RefreshCcwIcon className="size-4" />
              Force retrigger
            </Button>
          </div>
        </Can>
      );
    }
    if (lifecycle === "managed") {
      return (
        <Can permission="cluster.manage">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => handleRefresh(false)} disabled={refreshing}>
              <RefreshCcwIcon className="size-4" />
              Refresh setup
            </Button>
            <Button variant="ghost" onClick={() => handleRefresh(true)} disabled={refreshing}>
              <PlayIcon className="size-4" />
              Re-run preflight
            </Button>
          </div>
        </Can>
      );
    }
    if (lifecycle === "error") {
      return (
        <Can permission="cluster.manage">
          <Button onClick={handleBring} disabled={bringing}>
            <PlayIcon className="size-4" />
            Retry bring into management
          </Button>
        </Can>
      );
    }
    return (
      <Can permission="cluster.manage">
        <Button onClick={handleBring} disabled={bringing}>
          <PlayIcon className="size-4" />
          Bring into management
        </Button>
      </Can>
    );
  })();

  return (
    <PageShell
      title={`${cluster.name} · Settings`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
        </span>
      }
    >
      <ClusterTabs slug={slug} active="settings" />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Management</CardTitle>
          <CardDescription>
            Drive the cluster through its lifecycle — bring into management, refresh setup, or
            re-run the preflight probe.
          </CardDescription>
        </CardHeader>
        <CardContent>{managementActions}</CardContent>
      </Card>

      {lifecycle === "error" && cluster.lastManagementError && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              Last management failure
            </CardTitle>
            <CardDescription>
              The workflow recorded this error on its most recent attempt. Fix the underlying issue
              and click Retry to re-run.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="text-destructive text-xs whitespace-pre-wrap">
              {cluster.lastManagementError}
            </pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <GlobeIcon className="size-4" />
            Connection
          </CardTitle>
          <CardDescription>How the platform reaches the cluster&apos;s API server.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          <Field label="API endpoint" mono value={cluster.endpoint} />
          <Field
            label="Auth method"
            value={
              <span className="inline-flex items-center gap-1.5">
                <ShieldIcon className="size-3.5" />
                <span className="font-mono">{cluster.authMethod}</span>
              </span>
            }
          />
          <Field label="Ingress class" mono value={cluster.ingressClass} />
          <Field label="Registered" value={fmt.formatDateTime(cluster.createdAt)} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <CloudIcon className="size-4" />
            Probed capabilities
          </CardTitle>
          <CardDescription>
            Reported by the management workflow on the most recent run. Empty until the cluster is
            brought into management; click Refresh setup to re-probe.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {Object.keys(caps).length === 0 ? (
            !cluster.capabilitiesProbedAt || lifecycle === "managing" ? (
              <div className="p-6">
                <Skeleton className="h-32 w-full" />
              </div>
            ) : (
              <p className="text-muted-foreground p-6 text-sm">
                No capabilities reported yet. Click <strong>Bring into management</strong> to run
                the probe.
              </p>
            )
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Capability</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {CAPABILITY_KEYS.map((key) => (
                  <CapabilityRow key={key} keyName={key} value={caps[key]} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {missingHeadlinePrereq && (
        <div className="border-muted-foreground/30 bg-muted/30 rounded-md border border-dashed p-4 text-sm">
          <p className="font-medium">Missing platform prerequisites</p>
          <p className="text-muted-foreground mt-1">
            One or more headline prereqs (cert-manager, ingress controller) aren&apos;t detected.
            Tenant deploys may fail at TLS / ingress provisioning. Two paths to fix:
          </p>
          <ul className="text-muted-foreground mt-2 ml-4 list-disc space-y-1">
            <li>
              Use the bootstrap recipe card below to apply driver-tuned prereqs via Flux
              (recommended for managed clusters).
            </li>
            <li>
              Run{" "}
              <code className="font-mono text-xs">
                astro cluster bootstrap --cluster-slug {cluster.slug}
              </code>{" "}
              from your terminal for the one-shot CLI path.{" "}
              <Link href="/downloads" className="text-primary underline-offset-4 hover:underline">
                Install the CLI
              </Link>
              .
            </li>
            <li>
              See{" "}
              <Link
                href="/documentation/cluster-prerequisites"
                className="text-primary underline-offset-4 hover:underline"
              >
                cluster prerequisites
              </Link>{" "}
              for manual install commands per controller.
            </li>
          </ul>
        </div>
      )}

      <LastBootstrapCard slug={cluster.slug} run={cluster.lastBootstrapRun ?? null} />

      <BootstrapPlanCard clusterId={cluster.id} />

      <Can permission="cluster.unregister">
        <Card className="border-destructive/40">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              Danger zone
            </CardTitle>
            <CardDescription>
              Decommissioning stops the platform from managing this cluster. Apps already bound
              here must be migrated first; the workflow refuses when bindings are active.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              variant="outline"
              onClick={() => setDecommissionOpen(true)}
              disabled={decommissioning}
              className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2Icon className="size-4" />
              Decommission cluster
            </Button>
          </CardContent>
        </Card>
      </Can>

      <AlertDialog open={decommissionOpen} onOpenChange={setDecommissionOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Decommission {cluster.slug}?</AlertDialogTitle>
            <AlertDialogDescription>
              The platform will stop managing this cluster — its lifecycle flips to{" "}
              <strong>decommissioned</strong> and it&apos;s removed from the active-cluster picker
              for new app deploys. Existing apps already bound to this cluster must be migrated
              first; the workflow refuses when bindings are active.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="border-destructive/30 bg-destructive/5 my-4 rounded-md border p-3">
            <label className="flex cursor-pointer items-start gap-3 text-sm">
              <input
                type="checkbox"
                checked={decommissionDeleteInfra}
                onChange={(e) => setDecommissionDeleteInfra(e.target.checked)}
                className="border-destructive/50 accent-destructive mt-0.5 size-4 cursor-pointer rounded"
              />
              <span>
                <strong className="text-destructive">Also delete cloud infrastructure.</strong>{" "}
                <span className="text-muted-foreground">
                  This calls the {cluster.providerPluginSlug} driver&apos;s{" "}
                  <code className="font-mono text-xs">teardown_cluster</code> and irreversibly
                  deletes the underlying managed cluster (node groups / Fargate profiles
                  cascade-delete). The cloud-controlled VPC / IAM / DNS roots remain operator-owned.
                  Leave unchecked to keep the cluster running.
                </span>
              </span>
            </label>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDecommission}
              disabled={decommissioning}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {decommissionDeleteInfra ? "Decommission + delete cluster" : "Decommission"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageShell>
  );
}

// Per-row capability renderer. Each key in the JSONField shape has
// a small adapter that pulls out the user-facing status string +
// detail line — keeping the table readable without forcing the
// operator to decode the JSON.
function CapabilityRow({ keyName, value }: { keyName: string; value: unknown }) {
  const presentation = formatCapability(keyName, value);
  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{presentation.label}</TableCell>
      <TableCell>
        {presentation.installed ? (
          <Badge variant="default" className="gap-1">
            <CheckCircleIcon className="size-3" />
            Installed
          </Badge>
        ) : (
          <Badge variant="outline" className="gap-1">
            Not detected
          </Badge>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground text-xs">{presentation.detail}</TableCell>
    </TableRow>
  );
}

function formatCapability(
  key: string,
  value: unknown
): { label: string; installed: boolean; detail: string } {
  if (key === "cert_manager") {
    const v = (value ?? {}) as {
      installed?: boolean;
      version?: string | null;
      default_issuer?: string | null;
    };
    return {
      label: "cert-manager",
      installed: !!v.installed,
      detail:
        [v.version && `version ${v.version}`, v.default_issuer && `issuer ${v.default_issuer}`]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  }
  if (key === "ingress") {
    const v = (value ?? {}) as {
      installed?: boolean;
      class?: string | null;
      controller_version?: string | null;
    };
    return {
      label: "ingress controller",
      installed: !!v.installed,
      detail:
        [v.class && `class ${v.class}`, v.controller_version && `version ${v.controller_version}`]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  }
  if (key === "service_mesh") {
    const v = (value ?? {}) as { installed?: boolean; kind?: string | null };
    return {
      label: "service mesh",
      installed: !!v.installed,
      detail: v.kind ?? "—",
    };
  }
  if (key === "external_dns") {
    const v = (value ?? {}) as { installed?: boolean; provider?: string | null };
    return {
      label: "external-dns",
      installed: !!v.installed,
      detail: v.provider ?? "—",
    };
  }
  if (key === "storage_classes") {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return {
      label: "storage classes",
      installed: arr.length > 0,
      detail: arr.length > 0 ? arr.join(", ") : "—",
    };
  }
  if (key === "metrics_server") {
    return {
      label: "metrics-server",
      installed: !!value,
      detail: "—",
    };
  }
  if (key === "prometheus") {
    return {
      label: "Prometheus",
      installed: !!value,
      detail: "—",
    };
  }
  return {
    label: key,
    installed: false,
    detail: typeof value === "object" ? JSON.stringify(value) : String(value),
  };
}

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">{label}</dt>
      <dd className={mono ? "font-mono text-sm break-all" : "text-sm"}>{value}</dd>
    </div>
  );
}

// ─── Bootstrap plan card (#67 + #66) ─────────────────────────────────────
// Reads the driver's bootstrap recipe and renders it as an interactive
// checklist. Operator picks components + option values; clicking Install
// fires InstallClusterPrereqsWorkflow which applies one Flux HelmRelease
// per chosen component to ``astrolift-system``. Idempotent — re-running
// with a different selection converges.

interface BootstrapOptionChoice {
  value: string;
  label: string;
}

interface BootstrapOption {
  key: string;
  label: string;
  default: string;
  choices: BootstrapOptionChoice[];
}

interface BootstrapComponent {
  key: string;
  title: string;
  defaultEnabled: boolean;
  rationale: string;
  requires: string[];
  options: BootstrapOption[];
  helmValues: Record<string, unknown>;
}

interface BootstrapPlan {
  clusterId: string;
  providerPluginSlug: string;
  components: BootstrapComponent[];
}

interface BootstrapPlanResp {
  astroliftClusterBootstrapPlan: BootstrapPlan | null;
}

function BootstrapPlanCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<BootstrapPlanResp>(CLUSTER_BOOTSTRAP_PLAN, {
    variables: { clusterId },
  });
  const plan = data?.astroliftClusterBootstrapPlan ?? null;

  // Local selection state. Defaults derive from the recipe — the operator
  // sees the driver's opinion checked already; they un-check what they
  // don't want and pick non-default option values for what they do.
  const [selected, setSelected] = React.useState<Record<string, boolean>>({});
  const [optionValues, setOptionValues] = React.useState<Record<string, Record<string, string>>>(
    {}
  );

  React.useEffect(() => {
    if (!plan) return;
    const nextSel: Record<string, boolean> = {};
    const nextOpts: Record<string, Record<string, string>> = {};
    for (const c of plan.components) {
      nextSel[c.key] = c.defaultEnabled;
      const opts: Record<string, string> = {};
      for (const o of c.options) opts[o.key] = o.default || o.choices[0]?.value || "";
      nextOpts[c.key] = opts;
    }
    setSelected(nextSel);
    setOptionValues(nextOpts);
  }, [plan]);

  const [install, { loading: installing }] = useMutation<{
    installClusterPrereqs: MutationResult<{ id: string; slug: string }>;
  }>(INSTALL_CLUSTER_PREREQS);

  async function handleInstall() {
    if (!plan) return;
    const selectedComponents = plan.components.filter((c) => selected[c.key]).map((c) => c.key);
    const optionOverrides: { componentKey: string; optionKey: string; value: string }[] = [];
    for (const c of plan.components) {
      if (!selected[c.key]) continue;
      for (const o of c.options) {
        const v = optionValues[c.key]?.[o.key];
        if (v && v !== o.default) {
          optionOverrides.push({ componentKey: c.key, optionKey: o.key, value: v });
        }
      }
    }
    const { data } = await install({
      variables: {
        input: { clusterId, selectedComponents, optionOverrides },
      },
    });
    if (data?.installClusterPrereqs.ok) {
      toast.success(`Installing ${selectedComponents.length} prereq(s)`);
    } else {
      toast.error(data?.installClusterPrereqs.errors?.[0]?.message ?? "Install failed");
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Bootstrap recipe</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={`bootstrap-skel-${i}`}
              className="flex items-start gap-3 rounded-md border p-3"
            >
              <Skeleton className="mt-1 size-4 rounded-sm" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-3/4" />
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }
  if (!plan || plan.components.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Bootstrap recipe</CardTitle>
          <CardDescription>
            No driver recipe available for this provider. Install platform prerequisites manually or
            via the <code className="font-mono text-xs">astro cluster bootstrap</code> CLI.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const selectedCount = Object.values(selected).filter(Boolean).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <PlayIcon className="size-4" />
          Bootstrap recipe
        </CardTitle>
        <CardDescription>
          Driver recipe from{" "}
          <Badge variant="outline" className="mx-1 font-mono text-[10px]">
            {plan.providerPluginSlug || "unknown"}
          </Badge>
          — pre-tuned helm values per component. Re-installing converges via Flux; un-checking a
          previously-installed component deletes its HelmRelease on the next install.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {plan.components.map((c) => (
          <div key={c.key} className="rounded-md border p-3">
            <label className="flex cursor-pointer items-start gap-3 text-sm">
              <input
                type="checkbox"
                checked={!!selected[c.key]}
                onChange={(e) => setSelected((s) => ({ ...s, [c.key]: e.target.checked }))}
                className="mt-1 size-4 cursor-pointer"
              />
              <div className="flex-1">
                <div className="font-medium">{c.title}</div>
                <div className="text-muted-foreground mt-0.5 text-xs">{c.rationale}</div>
                {c.requires.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {c.requires.map((r) => (
                      <Badge key={r} variant="secondary" className="font-mono text-[10px]">
                        requires: {r}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </label>
            {selected[c.key] && c.options.length > 0 && (
              <div className="mt-3 ml-7 space-y-2">
                {c.options.map((o) => (
                  <div key={o.key} className="flex items-center gap-2">
                    <span className="text-muted-foreground w-32 truncate text-xs">{o.label}</span>
                    <select
                      value={optionValues[c.key]?.[o.key] ?? o.default}
                      onChange={(e) =>
                        setOptionValues((prev) => ({
                          ...prev,
                          [c.key]: {
                            ...(prev[c.key] ?? {}),
                            [o.key]: e.target.value,
                          },
                        }))
                      }
                      className="border-input bg-background flex-1 rounded-md border px-2 py-1 text-xs"
                    >
                      {o.choices.map((ch) => (
                        <option key={ch.value} value={ch.value}>
                          {ch.label}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </CardContent>
      <div className="flex items-center justify-between border-t px-6 py-3">
        <span className="text-muted-foreground text-xs">
          {selectedCount} of {plan.components.length} selected
        </span>
        <Can permission="cluster.manage">
          <Button size="sm" onClick={handleInstall} disabled={installing || selectedCount === 0}>
            {installing ? (
              <>
                <Loader2Icon className="size-3 animate-spin" />
                Installing…
              </>
            ) : (
              <>
                <PlayIcon className="size-3" />
                Install / reconcile
              </>
            )}
          </Button>
        </Can>
      </div>
    </Card>
  );
}

// ─── Last bootstrap card (#319) ─────────────────────────────────────
// Surfaces the outcome of the most recent ``astro cluster bootstrap``
// run, fed by recordClusterBootstrapRun (called by the CLI). Status
// pill + relative timestamp + chart version + installed-release
// count + triggering operator. Inline 'View history' disclosure fans
// out CLUSTER_BOOTSTRAP_RUNS for the per-cluster history list.

interface BootstrapRun {
  id: string;
  status: string;
  chartVersion: string;
  installedReleases: unknown;
  cliVersion: string;
  errorMessage: string;
  startedAt: string;
  endedAt: string;
  triggeredByUsername?: string | null;
}

interface BootstrapRunsResp {
  astroliftClusters: { id: string; slug: string; bootstrapRuns: BootstrapRun[] }[];
}

function bootstrapReleaseCount(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function LastBootstrapCard({ slug, run }: { slug: string; run: BootstrapRun | null }) {
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const fmt = useFormatters();

  const cliHint = `astro cluster bootstrap --cluster-slug ${slug}`;

  if (!run) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <RocketIcon className="size-4" />
            Last bootstrap
          </CardTitle>
          <CardDescription>
            No bootstrap runs reported for this cluster yet. Run{" "}
            <code className="font-mono text-xs">{cliHint}</code> from the CLI to install platform
            prerequisites; the outcome will land here automatically.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const succeeded = run.status === "succeeded";
  const releaseCount = bootstrapReleaseCount(run.installedReleases);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <RocketIcon className="size-4" />
          Last bootstrap
        </CardTitle>
        <CardDescription>
          Most recent <code className="font-mono text-xs">astro cluster bootstrap</code> run
          reported by the CLI. Re-runs append; the row never mutates after the CLI submits it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {succeeded ? (
            <Badge variant="default" className="gap-1">
              <CheckCircleIcon className="size-3" />
              Succeeded
            </Badge>
          ) : (
            <Badge variant="destructive" className="gap-1">
              <XCircleIcon className="size-3" />
              Failed
            </Badge>
          )}
          <span className="text-muted-foreground text-xs" title={fmt.formatDateTime(run.endedAt)}>
            {fmt.formatRelativeTime(run.endedAt)}
          </span>
          {run.cliVersion && (
            <Badge variant="outline" className="font-mono text-[10px]">
              {run.cliVersion}
            </Badge>
          )}
        </div>

        <div className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          <Field label="Chart version" mono value={run.chartVersion || "—"} />
          <Field
            label="Installed releases"
            value={
              <span>
                <span className="font-semibold">{releaseCount}</span>{" "}
                <span className="text-muted-foreground text-xs">
                  release{releaseCount === 1 ? "" : "s"}
                </span>
              </span>
            }
          />
          <Field
            label="Triggered by"
            value={
              <span className="inline-flex items-center gap-1.5">
                <UserIcon className="size-3.5" />
                <span>{run.triggeredByUsername || "unknown"}</span>
              </span>
            }
          />
          <Field label="Started" value={fmt.formatDateTime(run.startedAt)} />
        </div>

        {!succeeded && run.errorMessage && (
          <div className="border-destructive/30 bg-destructive/5 rounded-md border p-3">
            <p className="text-destructive text-xs font-medium">Error reported by the CLI</p>
            <pre className="text-destructive mt-1 text-xs whitespace-pre-wrap">
              {run.errorMessage}
            </pre>
          </div>
        )}

        {releaseCount > 0 && Array.isArray(run.installedReleases) && (
          <details className="rounded-md border">
            <summary className="text-muted-foreground cursor-pointer px-3 py-2 text-xs">
              View installed releases
            </summary>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Release</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(run.installedReleases as Array<Record<string, unknown>>).map((r, i) => (
                  <TableRow key={`${r.name ?? "release"}-${i}`}>
                    <TableCell className="font-mono text-xs">{String(r.name ?? "—")}</TableCell>
                    <TableCell className="font-mono text-xs">{String(r.version ?? "—")}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {String(r.status ?? "")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </details>
        )}

        <button
          type="button"
          onClick={() => setHistoryOpen((v) => !v)}
          aria-expanded={historyOpen}
          className="text-primary hover:text-primary inline-flex items-center gap-1 text-xs underline-offset-4 hover:underline"
        >
          <ChevronDownIcon
            className={cn("size-3 transition-transform", historyOpen && "rotate-180")}
          />
          {historyOpen ? "Hide history" : "View history"}
        </button>

        {historyOpen && <BootstrapHistoryList slug={slug} />}
      </CardContent>
    </Card>
  );
}

function BootstrapHistoryList({ slug }: { slug: string }) {
  const { data, loading } = useQuery<BootstrapRunsResp>(CLUSTER_BOOTSTRAP_RUNS, {
    variables: { limit: 10 },
  });
  const fmt = useFormatters();
  const cluster = data?.astroliftClusters.find((c) => c.slug === slug);
  const runs = cluster?.bootstrapRuns ?? [];

  if (loading) {
    return <Skeleton className="h-16 w-full" />;
  }
  if (runs.length === 0) {
    return <p className="text-muted-foreground text-xs">No prior bootstrap runs.</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>When</TableHead>
          <TableHead>Chart</TableHead>
          <TableHead>Releases</TableHead>
          <TableHead>Operator</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((r) => (
          <TableRow key={r.id}>
            <TableCell>
              {r.status === "succeeded" ? (
                <Badge variant="default" className="gap-1">
                  <CheckCircleIcon className="size-3" />
                  Succeeded
                </Badge>
              ) : (
                <Badge variant="destructive" className="gap-1">
                  <XCircleIcon className="size-3" />
                  Failed
                </Badge>
              )}
            </TableCell>
            <TableCell className="text-xs" title={fmt.formatDateTime(r.endedAt)}>
              {fmt.formatRelativeTime(r.endedAt)}
            </TableCell>
            <TableCell className="font-mono text-xs">{r.chartVersion || "—"}</TableCell>
            <TableCell className="text-xs">{bootstrapReleaseCount(r.installedReleases)}</TableCell>
            <TableCell className="text-xs">{r.triggeredByUsername || "unknown"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
