"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  CloudIcon,
  CopyIcon,
  GlobeIcon,
  KeyRoundIcon,
  Loader2Icon,
  PencilIcon,
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
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
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
  COGNITO_USER_POOL_CLIENTS,
  COGNITO_USER_POOLS,
  DECOMMISSION_CLUSTER,
  DEPLOY_CLUSTER_AGENT,
  INSTALL_CLUSTER_PREREQS,
  ISSUE_CLUSTER_AGENT_KEY,
  LIST_CLUSTERS,
  RECONCILE_CLUSTER_INGRESSES,
  REFRESH_CLUSTER_MANAGEMENT,
  UPDATE_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type {
  AstroliftTenantCluster,
  ReconcileClusterIngressesResult,
} from "@/graphql/clusters/clusters.types";
import type {
  CognitoUserPoolClientsQuery,
  CognitoUserPoolsQuery,
} from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import type { ClusterHeartbeatFields } from "@/lib/cluster-heartbeat";

import { ClusterTabs } from "../components/cluster-tabs";

// The committed codegen output lags the live backend, so the generated
// AstroliftTenantCluster doesn't yet carry the heartbeat fields the
// LIST_CLUSTERS query now selects (#808). Intersect them in locally.
type ClusterWithHeartbeat = AstroliftTenantCluster & Partial<ClusterHeartbeatFields>;

interface Resp {
  astroliftClusters: ClusterWithHeartbeat[];
}

const POLL_INTERVAL_MS = 4000;

type Lifecycle = "registered" | "managing" | "managed" | "error";

const CAPABILITY_KEYS = [
  "cert_manager",
  "ingress",
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

      <Can permission="cluster.manage">
        <ClusterAgentCard cluster={cluster} />
      </Can>

      <Can permission="cluster.update">
        <IngressAuthCard cluster={cluster} />
      </Can>

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
              Decommissioning stops the platform from managing this cluster. Apps already bound here
              must be migrated first; the workflow refuses when bindings are active.
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
          <Badge variant="outline" className="mx-1 font-mono text-2xs">
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
                      <Badge key={r} variant="secondary" className="font-mono text-2xs">
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
            <Badge variant="outline" className="font-mono text-2xs">
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

// ---- Cluster keep-alive agent card (#808) --------------------------
// Issues (or rotates) the scoped agent key the in-cluster keep-alive
// agent signs its heartbeat with. The raw key is shown EXACTLY ONCE in
// the mutation response — the operator copies it into the agent's
// Secret, then it's unrecoverable (only its hash persists). Also shows
// the live connection status and the install snippet templated with
// this cluster's heartbeat URL.

interface AgentKeyIssuedData {
  clusterId: string;
  agentKey: string;
  intervalSeconds: number;
  heartbeatUrl: string;
  rotated: boolean;
}

interface AgentDeployedData {
  id: string;
  slug: string;
  agentProvisioned: boolean;
  heartbeatStatus: string;
}

function ClusterAgentCard({ cluster }: { cluster: ClusterWithHeartbeat }) {
  const [issue, { loading: issuing }] = useMutation<{
    issueClusterAgentKey: MutationResult<AgentKeyIssuedData>;
  }>(ISSUE_CLUSTER_AGENT_KEY, {
    // The mutation flips agentProvisioned + may change the interval;
    // refetch so the card's "provisioned" state and the live badge stay
    // consistent without a reload.
    refetchQueries: [{ query: LIST_CLUSTERS }],
  });
  const [deploy, { loading: deploying }] = useMutation<{
    deployClusterAgent: MutationResult<AgentDeployedData>;
  }>(DEPLOY_CLUSTER_AGENT, {
    // The deploy lands the agent Deployment; the cluster starts pulsing
    // shortly after, so refetch to let the live badge flip to Connected.
    refetchQueries: [{ query: LIST_CLUSTERS }],
  });
  const [issued, setIssued] = React.useState<AgentKeyIssuedData | null>(null);
  const [keyCopied, copyKey] = useCopyToClipboard();
  const [snippetCopied, copySnippet] = useCopyToClipboard();

  const provisioned = cluster.agentProvisioned ?? false;

  async function handleIssue() {
    const { data } = await issue({
      variables: { input: { clusterId: cluster.id } },
    });
    if (data?.issueClusterAgentKey.ok && data.issueClusterAgentKey.data) {
      setIssued(data.issueClusterAgentKey.data);
      toast.success(
        data.issueClusterAgentKey.data.rotated
          ? "Agent key rotated — the previous key no longer works."
          : "Agent key issued — copy it now, it won't be shown again."
      );
    } else {
      toast.error(data?.issueClusterAgentKey.errors?.[0]?.message ?? "Failed to issue key");
    }
  }

  async function handleDeploy() {
    const { data } = await deploy({
      variables: { input: { clusterId: cluster.id } },
    });
    if (data?.deployClusterAgent.ok) {
      toast.success("Agent deployed — the cluster connects within a couple of heartbeat intervals.");
    } else {
      toast.error(data?.deployClusterAgent.errors?.[0]?.message ?? "Failed to deploy agent");
    }
  }

  // Install snippet: a kubectl one-liner that creates the agent's Secret
  // from the issued key + heartbeat URL. The agent Deployment reads both
  // from this Secret. Path-only URL in local dev (no APP_BASE_URL) — the
  // operator templates the host in then.
  const heartbeatUrl = issued?.heartbeatUrl ?? `/api/clusters/v1/${cluster.id}/heartbeat/`;
  const snippet = issued
    ? [
        "kubectl create secret generic astrolift-agent \\",
        "  --namespace astrolift-system \\",
        `  --from-literal=heartbeat_url='${heartbeatUrl}' \\`,
        `  --from-literal=agent_key='${issued.agentKey}'`,
      ].join("\n")
    : null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ActivityIcon className="size-4" />
              Keep-alive agent
            </CardTitle>
            <CardDescription className="mt-1">
              A lightweight agent in the cluster&apos;s{" "}
              <code className="font-mono text-xs">astrolift-system</code> namespace POSTs a signed
              heartbeat so the platform can show live pod, node, and resource state — and flag the
              cluster offline when it stops. Pulses every {cluster.heartbeatIntervalSeconds ?? 30}s.
            </CardDescription>
          </div>
          {provisioned && (
            <Badge variant="outline" className="shrink-0 gap-1">
              <CheckCircleIcon className="size-3" />
              Key issued
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {issued ? (
          <>
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
                Copy this key now — it won&apos;t be shown again.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <code className="bg-background/60 flex-1 truncate rounded px-2 py-1 font-mono text-xs">
                  {issued.agentKey}
                </code>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => copyKey(issued.agentKey)}
                  className="gap-1.5"
                >
                  <CopyIcon className="size-3.5" />
                  {keyCopied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
            {snippet && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium">Install the agent secret</p>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => copySnippet(snippet)}
                    className="h-7 gap-1.5"
                  >
                    <CopyIcon className="size-3" />
                    {snippetCopied ? "Copied" : "Copy"}
                  </Button>
                </div>
                <pre className="bg-muted/40 overflow-x-auto rounded-md border p-3 font-mono text-xs whitespace-pre">
                  {snippet}
                </pre>
                <p className="text-muted-foreground text-xs">
                  Then deploy the agent (it reads the key + URL from this Secret). The cluster
                  appears as Connected within a couple of heartbeat intervals.
                </p>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={handleDeploy} disabled={deploying} className="gap-1.5">
                {deploying ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RocketIcon className="size-3.5" />
                )}
                Deploy agent to cluster
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setIssued(null)}>
                Done
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              Applies the agent Deployment — make sure you&apos;ve created the{" "}
              <code className="font-mono text-xs">astrolift-agent</code> Secret first using the
              snippet above.
            </p>
          </>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              {provisioned && (
                <Button size="sm" onClick={handleDeploy} disabled={deploying} className="gap-1.5">
                  {deploying ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : (
                    <RocketIcon className="size-3.5" />
                  )}
                  Deploy agent to cluster
                </Button>
              )}
              <Button
                size="sm"
                variant={provisioned ? "outline" : "default"}
                onClick={handleIssue}
                disabled={issuing}
                className="gap-1.5"
              >
                {issuing ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <KeyRoundIcon className="size-3.5" />
                )}
                {provisioned ? "Rotate agent key" : "Issue agent key"}
              </Button>
            </div>
            {provisioned && (
              <p className="text-muted-foreground text-xs">
                Deploying applies the agent Deployment — it reads the key + URL from the{" "}
                <code className="font-mono text-xs">astrolift-agent</code> Secret you created when
                the key was issued. Rotating the key invalidates the old one, so the running agent
                will fail its heartbeat until you redeploy with the new key.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---- Ingress auth card (#851) --------------------------------------

interface IngressAuthConfig {
  user_pool_arn: string;
  user_pool_client_id: string;
  user_pool_domain: string;
}

function isAlbAuthConfig(v: unknown): v is IngressAuthConfig {
  return (
    typeof v === "object" &&
    v !== null &&
    "user_pool_arn" in v &&
    "user_pool_client_id" in v &&
    "user_pool_domain" in v
  );
}

/**
 * Accessible on/off switch. The repo has no shadcn/radix Switch
 * primitive, so this is a small local toggle styled with the same
 * Tailwind tokens the rest of the settings surface uses — not a new
 * shared component. The "on" track is emerald so the enabled state
 * reads as a security control rather than a neutral preference.
 */
function AuthGateToggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-emerald-600" : "bg-muted-foreground/30"
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  );
}

function IngressAuthCard({ cluster }: { cluster: AstroliftTenantCluster }) {
  const existing = isAlbAuthConfig(cluster.albAuthConfig) ? cluster.albAuthConfig : null;
  const enabled = existing !== null;

  const [editing, setEditing] = React.useState(false);
  const [poolArn, setPoolArn] = React.useState(existing?.user_pool_arn ?? "");
  const [clientId, setClientId] = React.useState(existing?.user_pool_client_id ?? "");
  const [domain, setDomain] = React.useState(existing?.user_pool_domain ?? "");
  // Pool id of the currently-picked pool — drives the dependent app-
  // client query. Derived from the picked pool, or parsed out of an
  // existing/pasted ARN (…:userpool/<poolId>) so the client picker
  // works when editing an already-saved config.
  const [poolId, setPoolId] = React.useState(() => poolIdFromArn(existing?.user_pool_arn ?? ""));
  // "Advanced / paste directly" fallback. AWS-only pickers degrade to
  // the original free-text inputs when the operator wants to paste a
  // cross-account pool the cluster's IAM role can't enumerate, or when
  // the Cognito list query errors.
  const [useAdvanced, setUseAdvanced] = React.useState(false);

  // Cognito pool list for the picker (#859). Only fetched for AWS
  // clusters (the resolver returns [] otherwise) and only while the
  // edit form is open — no point querying AWS on every settings view.
  const isAws = cluster.providerPluginSlug === "aws";
  const poolsQuery = useQuery<CognitoUserPoolsQuery>(COGNITO_USER_POOLS, {
    variables: { clusterId: cluster.id },
    skip: !editing || !isAws,
    fetchPolicy: "cache-and-network",
  });
  const pools = poolsQuery.data?.astroliftCognitoUserPools ?? [];

  // Dependent app-client list — fetched once a pool is selected.
  const clientsQuery = useQuery<CognitoUserPoolClientsQuery>(COGNITO_USER_POOL_CLIENTS, {
    variables: { clusterId: cluster.id, poolId },
    skip: !editing || !isAws || !poolId,
    fetchPolicy: "cache-and-network",
  });
  const clients = clientsQuery.data?.astroliftCognitoUserPoolClients ?? [];

  // Surface the live-query failure so the operator knows to fall back to
  // paste mode rather than staring at an empty dropdown.
  const poolsErrored = !!poolsQuery.error;

  const [update, { loading: updating }] = useMutation<{
    updateTenantCluster: MutationResult<AstroliftTenantCluster>;
  }>(UPDATE_TENANT_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [reconcile, { loading: reconciling }] = useMutation<{
    reconcileClusterIngresses: MutationResult<ReconcileClusterIngressesResult>;
  }>(RECONCILE_CLUSTER_INGRESSES);

  const busy = updating || reconciling;

  /**
   * Persist ``config`` (object = enable, null = disable) then push it
   * onto every live managed-subdomain Ingress. The two mutations run in
   * sequence: the save has to land before the reconcile reads the row.
   * The reconcile envelope stays ``ok`` even on partial failure, so we
   * surface the applied count and fold any per-namespace errors into a
   * follow-up warning toast.
   */
  async function persistAndReconcile(config: IngressAuthConfig | null) {
    const { data: updateData } = await update({
      variables: { input: { id: cluster.id, albAuthConfig: config } },
    });
    if (!updateData?.updateTenantCluster.ok) {
      toast.error(updateData?.updateTenantCluster.errors?.[0]?.message ?? "Save failed.");
      return false;
    }

    const { data: reconcileData } = await reconcile({
      variables: { input: { clusterId: cluster.id } },
    });
    const result = reconcileData?.reconcileClusterIngresses;
    if (!result?.ok) {
      toast.error(result?.errors?.[0]?.message ?? "Reconcile failed.");
      return false;
    }

    const count = result.data?.reconciledCount ?? 0;
    const noun = count === 1 ? "app" : "apps";
    if (config) {
      toast.success(`Auth gate applied to ${count} ${noun}.`);
    } else {
      toast.success(`Auth gate removed from ${count} ${noun}.`);
    }
    const reconcileErrors = result.data?.errors ?? [];
    if (reconcileErrors.length > 0) {
      toast.warning(
        `${reconcileErrors.length} ingress(es) could not be reconciled — check cluster events.`
      );
    }
    return true;
  }

  function configFromFields(): IngressAuthConfig | null {
    if (poolArn && clientId && domain) {
      return { user_pool_arn: poolArn, user_pool_client_id: clientId, user_pool_domain: domain };
    }
    return null;
  }

  function openForm() {
    setPoolArn(existing?.user_pool_arn ?? "");
    setClientId(existing?.user_pool_client_id ?? "");
    setDomain(existing?.user_pool_domain ?? "");
    setPoolId(poolIdFromArn(existing?.user_pool_arn ?? ""));
    setUseAdvanced(false);
    setEditing(true);
  }

  // Operator picked a pool from the combobox: fill the ARN + pool id,
  // auto-fill the hosted domain (the whole point of #859 — no more
  // hand-typing it), and reset the app-client selection so the
  // dependent picker re-queries against the new pool.
  function pickPool(pool: CognitoUserPool) {
    setPoolArn(pool.poolArn);
    setPoolId(pool.poolId);
    if (pool.domain) {
      setDomain(pool.domain);
    }
    setClientId("");
  }

  async function handleToggle() {
    if (busy) return;
    if (enabled) {
      // Flip off: clear config + strip annotations from live Ingresses.
      await persistAndReconcile(null);
      setEditing(false);
    } else {
      // Can't enable without config — open the form to collect it.
      openForm();
    }
  }

  // Re-apply the (already saved) config onto the cluster's Ingresses.
  async function handleApply() {
    if (busy) return;
    await persistAndReconcile(existing);
  }

  // Save the form's config then apply it in one go.
  async function handleSaveAndApply() {
    if (busy) return;
    const config = configFromFields();
    if (config === null) {
      toast.error("All three fields are required to enable the auth gate.");
      return;
    }
    const ok = await persistAndReconcile(config);
    if (ok) setEditing(false);
  }

  // Per-provider auth metadata. AWS+ALB is the only fully-supported path
  // today; other providers show a "coming soon" state so the card is honest
  // rather than showing AWS-specific copy on a GKE or AKS cluster.
  const providerAuthMeta: Record<
    string,
    { supported: boolean; label: string; description: string; comingSoon?: string }
  > = {
    aws: {
      supported: cluster.ingressClass === "alb",
      label: "Cognito auth gate",
      description:
        "AWS ALB authenticate-cognito — applied to every managed-subdomain Ingress on this cluster.",
      comingSoon:
        cluster.ingressClass !== "alb"
          ? "ALB Cognito auth requires ingressClass = alb."
          : undefined,
    },
    gcp: {
      supported: false,
      label: "Google IAP",
      description: "Google Identity-Aware Proxy — per-app OAuth gate on GKE Ingress rules.",
      comingSoon: "Google IAP auth gate is not yet supported.",
    },
    azure: {
      supported: false,
      label: "Azure AD",
      description: "Azure Active Directory — per-app auth gate on AKS Application Gateway Ingress.",
      comingSoon: "Azure AD auth gate is not yet supported.",
    },
    k8s_native: {
      supported: false,
      label: "OIDC (oauth2-proxy)",
      description: "Generic OIDC via oauth2-proxy — Dex or any OIDC-compliant IdP.",
      comingSoon: "OIDC auth gate for raw k8s clusters is tracked in #852.",
    },
  };

  const authMeta = providerAuthMeta[cluster.providerPluginSlug] ?? {
    supported: false,
    label: "Auth gate",
    description: "Ingress-level auth gate.",
    comingSoon: `Auth gate is not yet supported for provider ${cluster.providerPluginSlug}.`,
  };

  if (!authMeta.supported) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRoundIcon className="size-4" />
            {authMeta.label}
          </CardTitle>
          <CardDescription className="mt-1">{authMeta.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">{authMeta.comingSoon}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              {enabled ? (
                <ShieldIcon className="size-4 text-emerald-600" />
              ) : (
                <KeyRoundIcon className="size-4" />
              )}
              {authMeta.label}
            </CardTitle>
            <CardDescription className="mt-1">{authMeta.description}</CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <AuthGateToggle
              checked={enabled}
              onChange={handleToggle}
              disabled={busy}
              label={enabled ? "Disable auth gate" : "Enable auth gate"}
            />
            <span
              className={cn(
                "text-xs font-medium",
                enabled ? "text-emerald-600" : "text-muted-foreground"
              )}
            >
              {enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {editing ? (
          <div className="space-y-3">
            {isAws && !useAdvanced ? (
              <>
                <div className="space-y-1">
                  <label className="text-xs font-medium">User pool</label>
                  <CognitoPoolCombobox
                    pools={pools}
                    loading={poolsQuery.loading}
                    errored={poolsErrored}
                    poolArn={poolArn}
                    onPick={pickPool}
                    onFreeText={(v) => {
                      setPoolArn(v);
                      setPoolId(poolIdFromArn(v));
                    }}
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">App client</label>
                  <CognitoClientCombobox
                    clients={clients}
                    loading={clientsQuery.loading}
                    disabled={!poolId}
                    clientId={clientId}
                    onPick={setClientId}
                    onFreeText={setClientId}
                  />
                  {!poolId && (
                    <p className="text-muted-foreground text-xs">Pick a user pool first.</p>
                  )}
                </div>
              </>
            ) : (
              <>
                <div className="space-y-1">
                  <label className="text-xs font-medium">User pool ARN</label>
                  <Input
                    value={poolArn}
                    onChange={(e) => {
                      setPoolArn(e.target.value);
                      setPoolId(poolIdFromArn(e.target.value));
                    }}
                    placeholder="arn:aws:cognito-idp:us-west-2:…"
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">App client ID</label>
                  <Input
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    placeholder="abc123…"
                    className="font-mono text-xs"
                  />
                </div>
              </>
            )}
            <div className="space-y-1">
              <label className="text-xs font-medium">User pool domain</label>
              <Input
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="my-domain (without .auth.region.amazoncognito.com)"
                className="font-mono text-xs"
              />
              {isAws && !useAdvanced && (
                <p className="text-muted-foreground text-xs">
                  Auto-filled from the selected pool; edit to override.
                </p>
              )}
            </div>
            {isAws && (
              <button
                type="button"
                onClick={() => setUseAdvanced((v) => !v)}
                className="text-muted-foreground hover:text-foreground text-xs underline-offset-4 hover:underline"
              >
                {useAdvanced
                  ? "Use the pool picker"
                  : "Advanced: paste ARN / client ID directly (cross-account pools)"}
              </button>
            )}
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={handleSaveAndApply} disabled={busy} className="gap-1.5">
                {busy && <Loader2Icon className="size-3.5 animate-spin" />}
                Save &amp; Apply
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
                Cancel
              </Button>
            </div>
          </div>
        ) : enabled ? (
          <div className="space-y-3">
            <div className="space-y-2 text-sm">
              <Field label="User pool ARN" mono value={existing.user_pool_arn} />
              <Field label="App client ID" mono value={existing.user_pool_client_id} />
              <Field label="Domain" mono value={existing.user_pool_domain} />
            </div>
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={handleApply} disabled={busy} className="gap-1.5">
                {reconciling && <Loader2Icon className="size-3.5 animate-spin" />}
                <RocketIcon className="size-3.5" />
                Apply to cluster
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={openForm}
                disabled={busy}
                className="gap-1.5"
              >
                <PencilIcon className="size-3.5" />
                Edit
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
              <AlertTriangleIcon className="mt-0.5 size-4 shrink-0 text-amber-600" />
              <p className="text-sm text-amber-700 dark:text-amber-400">
                All apps on this cluster are publicly accessible. Enable the auth gate to put every
                managed-subdomain Ingress behind {authMeta.label}.
              </p>
            </div>
            <Button size="sm" onClick={openForm} disabled={busy} className="gap-1.5">
              <ShieldIcon className="size-3.5" />
              Enable
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

type CognitoUserPool = CognitoUserPoolsQuery["astroliftCognitoUserPools"][number];
type CognitoUserPoolClient = CognitoUserPoolClientsQuery["astroliftCognitoUserPoolClients"][number];

// Parse the pool id out of a Cognito user-pool ARN
// (arn:aws:cognito-idp:<region>:<account>:userpool/<poolId>). Returns ""
// when the string isn't a recognizable pool ARN — used so the dependent
// app-client picker can still query when editing an already-saved or
// pasted ARN, without forcing the operator to re-pick the pool.
function poolIdFromArn(arn: string): string {
  const m = arn.match(/userpool\/(.+)$/);
  return m ? m[1] : "";
}

// User-pool picker (#859) — searchable combobox over the cluster's
// Cognito pools with free-text fallback. The displayed value is the
// pool ARN; picking a pool fills the ARN + pool id + auto-fills the
// domain (via onPick), while typing commits a raw ARN (via onFreeText)
// for cross-account pools the cluster's IAM role can't enumerate.
function CognitoPoolCombobox({
  pools,
  loading,
  errored,
  poolArn,
  onPick,
  onFreeText,
}: {
  pools: CognitoUserPool[];
  loading: boolean;
  errored: boolean;
  poolArn: string;
  onPick: (pool: CognitoUserPool) => void;
  onFreeText: (v: string) => void;
}) {
  const selected = pools.find((p) => p.poolArn === poolArn) ?? null;
  return (
    <>
      <Combobox<CognitoUserPool>
        items={pools}
        itemToStringLabel={(p) => p.poolArn}
        value={selected}
        onValueChange={(v) => {
          if (v && typeof v === "object" && "poolArn" in v) {
            onPick(v);
          }
        }}
        inputValue={poolArn}
        onInputValueChange={(v) => onFreeText(v ?? "")}
      >
        <ComboboxInput
          placeholder={loading ? "Loading pools…" : "arn:aws:cognito-idp:us-west-2:…"}
          className="font-mono text-xs"
        />
        <ComboboxContent>
          <ComboboxEmpty>
            {poolArn ? `Use "${poolArn}" (paste ARN directly)` : "No pools found — paste an ARN."}
          </ComboboxEmpty>
          <ComboboxList>
            {(item: CognitoUserPool) => (
              <ComboboxItem key={item.poolId} value={item}>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm">{item.name || item.poolId}</span>
                  <span className="text-muted-foreground truncate font-mono text-2xs">
                    {item.poolId}
                    {item.domain ? ` · ${item.domain}` : ""}
                  </span>
                </div>
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
      {errored && (
        <p className="text-muted-foreground text-xs">
          Couldn&apos;t list pools (the cluster&apos;s role may lack cognito-idp:ListUserPools).
          Paste the ARN directly.
        </p>
      )}
    </>
  );
}

// Dependent app-client picker (#859) — enabled once a pool is selected.
// Same free-text fallback shape as the pool picker.
function CognitoClientCombobox({
  clients,
  loading,
  disabled,
  clientId,
  onPick,
  onFreeText,
}: {
  clients: CognitoUserPoolClient[];
  loading: boolean;
  disabled: boolean;
  clientId: string;
  onPick: (v: string) => void;
  onFreeText: (v: string) => void;
}) {
  const selected = clients.find((c) => c.clientId === clientId) ?? null;
  return (
    <Combobox<CognitoUserPoolClient>
      items={clients}
      itemToStringLabel={(c) => c.clientId}
      value={selected}
      onValueChange={(v) => {
        if (v && typeof v === "object" && "clientId" in v) {
          onPick(v.clientId);
        }
      }}
      inputValue={clientId}
      onInputValueChange={(v) => onFreeText(v ?? "")}
      disabled={disabled}
    >
      <ComboboxInput
        placeholder={loading ? "Loading clients…" : "abc123…"}
        className="font-mono text-xs"
        disabled={disabled}
      />
      <ComboboxContent>
        <ComboboxEmpty>
          {clientId ? `Use "${clientId}" (paste client ID directly)` : "No app clients in this pool."}
        </ComboboxEmpty>
        <ComboboxList>
          {(item: CognitoUserPoolClient) => (
            <ComboboxItem key={item.clientId} value={item}>
              <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <span className="truncate text-sm">{item.clientName || item.clientId}</span>
                <span className="text-muted-foreground truncate font-mono text-2xs">
                  {item.clientId}
                </span>
              </div>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
