"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  ChevronRightIcon,
  FileCodeIcon,
  FlameIcon,
  GlobeIcon,
  KeyIcon,
  LineChartIcon,
  Loader2Icon,
  LockIcon,
  PauseIcon,
  PlayCircleIcon,
  PlayIcon,
  PlugIcon,
  RefreshCwIcon,
  TimerIcon,
  Trash2Icon,
  UsersIcon,
  WebhookIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DEREGISTER_APP,
  FORCE_REDEPLOY,
  PAUSE_APP_INGRESS,
  RESUME_APP_INGRESS,
  RUN_JOB_ONCE,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { RESYNC_MANIFEST_FROM_REPO } from "@/graphql/registry/registry.mutations";
import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { AppTabs } from "../components/app-tabs";
import { AssignProjectCard } from "../components/assign-project-card";
import { CiSetupSection } from "../components/ci-setup-section";
import { ControlsSection } from "../components/controls-section";
import { TeamsCard } from "../components/teams-card";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface LinkSection {
  key: string;
  i18nKey: string;
  href: (slug: string) => string;
  icon: typeof KeyIcon;
}

/** Ordered list of link cards that compose the settings landing. Inline
 *  controls (deploys, ingress) live above; danger zone lives below. */
const LINK_SECTIONS: LinkSection[] = [
  {
    key: "deploy-strategy",
    i18nKey: "deployStrategy",
    href: (s) => `/apps/${s}/config`,
    icon: FileCodeIcon,
  },
  {
    key: "deploy-tokens",
    i18nKey: "deployTokens",
    href: (s) => `/apps/${s}/tokens`,
    icon: KeyIcon,
  },
  {
    key: "secrets",
    i18nKey: "secrets",
    href: (s) => `/apps/${s}/secrets`,
    icon: LockIcon,
  },
  {
    key: "managed-services",
    i18nKey: "managedServices",
    href: (s) => `/apps/${s}/managed-services`,
    icon: PlugIcon,
  },
  {
    key: "domains",
    i18nKey: "domains",
    href: (s) => `/apps/${s}/domains`,
    icon: GlobeIcon,
  },
  {
    key: "webhooks",
    i18nKey: "webhooks",
    href: (s) => `/apps/${s}/webhooks`,
    icon: WebhookIcon,
  },
  {
    key: "members",
    i18nKey: "members",
    href: (s) => `/apps/${s}/members`,
    icon: UsersIcon,
  },
  {
    key: "observability",
    i18nKey: "observability",
    href: (s) => `/apps/${s}/observability`,
    icon: LineChartIcon,
  },
];

export function SettingsClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.settings");
  const app = useQuery<AppResp>(GET_APP, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });

  if (app.loading && !app.data) {
    return (
      <PageShell title={t("title")} description={t("loading")}>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  const a = app.data?.astroliftApp;
  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title")}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t.rich("description", {
            slug: () => (
              <span className="text-foreground">{a.slug}</span>
            ),
          })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="settings" />

      <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />

      <ResyncSourceSection appSlug={a.slug} lastResyncAt={a.lastResyncAt ?? null} />

      <IngressControlsSection appSlug={a.slug} />

      <AssignProjectCard
        appSlug={a.slug}
        currentProjectId={a.projectId ?? null}
        currentProjectName={a.projectName}
        currentTeamName={a.teamName}
      />

      <TeamsCard appSlug={a.slug} appId={a.id} homeTeamSlug={a.teamSlug} />

      <div className="flex flex-col gap-3">
        {LINK_SECTIONS.map((s) => {
          const card = <SettingsLinkCard key={s.key} section={s} slug={a.slug} />;
          // CI-setup section slots between Deploy strategy and Deploy
          // tokens (#382). Hidden when the app has no source repo —
          // there's nothing to wire up to until registration captures
          // one. Sibling-friendly: appending a fragment under the
          // deploy-strategy row, no surrounding reformatting.
          if (s.key === "deploy-strategy" && a.sourceRepo) {
            return (
              <React.Fragment key="deploy-strategy-with-ci">
                {card}
                <CiSetupSection
                  appSlug={a.slug}
                  ecrRepoUri={a.ecrRepoUri}
                  ecrPushRoleArn={a.ecrPushRoleArn}
                  sourceWebhookInstalledAt={a.sourceWebhookInstalledAt ?? null}
                />
                <ForceRedeploySection appSlug={a.slug} />
              </React.Fragment>
            );
          }
          // Apps without a source repo still benefit from the force-
          // redeploy card — the cancel + delete steps don't depend on
          // the CI pipeline. Slot it directly under deploy-strategy
          // when the CI block is hidden so the visual hierarchy
          // matches the with-repo path.
          if (s.key === "deploy-strategy" && !a.sourceRepo) {
            return (
              <React.Fragment key="deploy-strategy-with-force-redeploy">
                {card}
                <ForceRedeploySection appSlug={a.slug} />
              </React.Fragment>
            );
          }
          return card;
        })}
      </div>

      <RunScheduledJobCard appSlug={a.slug} />

      <DangerZoneCard appSlug={a.slug} appName={a.name} />
    </PageShell>
  );
}

// ─── resync from source ───────────────────────────────────────────────────────

/**
 * "Resync from source" button for the Settings landing (#386).
 *
 * Re-fetches `astrolift.toml` from the deploy branch and reconciles
 * workloads / env / managed services / schedules. The mutation is
 * non-destructive on staged drafts — if the operator has local edits
 * pending, the backend refuses with a CONFLICT and surfaces that as an
 * error toast so the draft survives.
 *
 * The relative "Last resynced …" timestamp re-renders whenever the
 * mutation completes (we refetch `GET_APP`).
 */
interface ResyncResp {
  resyncAstroliftManifestFromRepo: MutationResult<{
    syncState: string;
    summary: string;
    workloadsAdded: string[];
    workloadsRemoved: string[];
    workloadsChanged: string[];
    managedServicesAdded: string[];
    managedServicesRemoved: string[];
    envKeysChanged: number;
    schedulesChanged: number;
  }>;
}

function ResyncSourceSection({
  appSlug,
  lastResyncAt,
}: {
  appSlug: string;
  lastResyncAt: string | null;
}) {
  const t = useTranslations("apps.settings.resync");
  const fmt = useFormatters();
  const [resync, { loading }] = useMutation<ResyncResp>(RESYNC_MANIFEST_FROM_REPO, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug } }],
    awaitRefetchQueries: true,
  });

  async function handleResync() {
    try {
      const { data } = await resync({ variables: { input: { appSlug } } });
      const env = data?.resyncAstroliftManifestFromRepo;
      if (!env) {
        toast.error("Resync failed: no response from backend.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Resync failed.");
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error("Resync returned no payload.");
        return;
      }
      if (payload.syncState === "in_sync") {
        toast.success("Already in sync.");
      } else {
        toast.success(payload.summary);
      }
    } catch (err) {
      // Apollo network error / unexpected throw — surface verbatim so
      // the operator can copy/paste into a ticket.
      toast.error(err instanceof Error ? err.message : "Resync failed.");
    }
  }

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-muted-foreground mt-0.5 text-xs">{t("description")}</p>
        </div>
        <Can permission="app.update">
          <Button size="sm" variant="outline" onClick={handleResync} disabled={loading}>
            {loading ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            {loading ? t("syncing") : t("button")}
          </Button>
        </Can>
      </div>
      <p className="text-muted-foreground text-[11px]">
        {lastResyncAt ? (
          <>
            {t("last")}{" "}
            <span className="text-foreground">{fmt.formatRelativeTime(lastResyncAt)}</span>.
          </>
        ) : (
          t("never")
        )}
      </p>
    </section>
  );
}

// ─── ingress controls ─────────────────────────────────────────────────────────

interface PauseIngressResp {
  pauseAppIngress: MutationResult<AstroliftAppEnvironment>;
}
interface ResumeIngressResp {
  resumeAppIngress: MutationResult<AstroliftAppEnvironment>;
}

/**
 * Per-environment ingress pause/resume — independent of the deploy-pause
 * toggle in `ControlsSection`. Pausing ingress takes the env offline at
 * the edge without stopping the workloads (handy for maintenance windows
 * where you still want pods running for in-flight DB writes).
 */
function IngressControlsSection({ appSlug }: { appSlug: string }) {
  const t = useTranslations("apps.settings.ingress");
  const { data, loading } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-4">
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-muted-foreground mt-0.5 text-xs">{t("description")}</p>
      </div>

      {loading && envs.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">
          {t("emptyEnvs")}
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {envs.map((env) => (
            <IngressRow key={env.id} env={env} appSlug={appSlug} />
          ))}
        </div>
      )}
    </section>
  );
}

function IngressRow({ env, appSlug }: { env: AstroliftAppEnvironment; appSlug: string }) {
  const t = useTranslations("apps.settings.ingress");
  const refetch = [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }];
  const [pause, { loading: pausing }] = useMutation<PauseIngressResp>(PAUSE_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [resume, { loading: resuming }] = useMutation<ResumeIngressResp>(RESUME_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const busy = pausing || resuming;
  const paused = env.ingressPaused;

  async function handleToggle() {
    if (paused) {
      const { data } = await resume({ variables: { input: { id: env.id } } });
      if (data?.resumeAppIngress.ok) {
        toast.success(`Ingress resumed for ${env.name}.`);
      } else {
        toast.error(data?.resumeAppIngress.errors?.[0]?.message ?? "Resume failed.");
      }
    } else {
      const { data } = await pause({ variables: { input: { id: env.id } } });
      if (data?.pauseAppIngress.ok) {
        toast.success(`Ingress paused for ${env.name}.`);
      } else {
        toast.error(data?.pauseAppIngress.errors?.[0]?.message ?? "Pause failed.");
      }
    }
  }

  return (
    <div className="bg-card flex items-center justify-between gap-3 rounded-md border p-4">
      <div className="flex items-center gap-2">
        <StatusDot status={paused ? "warn" : "ok"} />
        <span className="text-sm font-medium capitalize">{env.name}</span>
        {paused ? (
          <Badge
            variant="outline"
            className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
          >
            <PauseIcon className="size-3" />
            {t("paused")}
          </Badge>
        ) : (
          <Badge
            variant="outline"
            className="border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          >
            <PlayIcon className="size-3" />
            {t("live")}
          </Badge>
        )}
      </div>
      <Can permission="app.deploy">
        <Button
          size="sm"
          variant={paused ? "default" : "outline"}
          onClick={handleToggle}
          disabled={busy}
        >
          {busy ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : paused ? (
            <PlayIcon className="size-3.5" />
          ) : (
            <PauseIcon className="size-3.5" />
          )}
          {paused ? t("resume") : t("pause")}
        </Button>
      </Can>
    </div>
  );
}

// ─── link card ────────────────────────────────────────────────────────────────

function SettingsLinkCard({ section, slug }: { section: LinkSection; slug: string }) {
  const t = useTranslations("apps.settings.links");
  const Icon = section.icon;
  return (
    <Link href={section.href(slug)} className="group block">
      <Card className="hover:bg-accent/40 transition-colors">
        <CardContent className="flex items-center gap-4 p-5">
          <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{t(`${section.i18nKey}.title`)}</p>
            <p className="text-muted-foreground mt-0.5 text-xs">
              {t(`${section.i18nKey}.description`)}
            </p>
          </div>
          <ChevronRightIcon className="text-muted-foreground group-hover:text-foreground size-5 shrink-0 transition-colors" />
        </CardContent>
      </Card>
    </Link>
  );
}

// ─── force redeploy recovery (#389) ───────────────────────────────────────────

interface ForceRedeployResp {
  forceAstroliftRedeploy: MutationResult<{
    deploymentsCancelled: number;
    k8sObjectsDeleted: number;
    workflowDispatched: boolean;
    runUrl: string | null;
    dispatchMessage: string | null;
  }>;
}

/**
 * Destructive recovery card for wedged apps (#389).
 *
 * Three steps run in order: cancel in-flight Deployment rows, delete
 * the per-workload k8s objects (Deployment / Service / Ingress /
 * CronJob plus bare-slug fallbacks), then re-dispatch the deploy CI
 * workflow. Permission gate is `app.deploy` plus `app.update` —
 * matching the backend's stacked-permission resolver.
 *
 * Confirmation modal requires the operator to type the app's slug,
 * matching the backend's `confirmSlug` muscle-memory guard. The
 * `Continue` button only enables once the typed slug equals the
 * app's slug exactly.
 */
function ForceRedeploySection({ appSlug }: { appSlug: string }) {
  const t = useTranslations("apps.settings.forceRedeploy");
  const [open, setOpen] = React.useState(false);
  const [typed, setTyped] = React.useState("");
  const [forceRedeploy, { loading }] = useMutation<ForceRedeployResp>(FORCE_REDEPLOY);

  // Reset the typed slug each time the modal closes so a reopened
  // dialog starts blank — the confirmation is per-attempt.
  React.useEffect(() => {
    if (!open) {
      setTyped("");
    }
  }, [open]);

  const slugMatches = typed.trim() === appSlug;

  async function handleConfirm() {
    if (!slugMatches) return;
    try {
      const { data } = await forceRedeploy({
        variables: { input: { appSlug, confirmSlug: typed.trim() } },
      });
      const env = data?.forceAstroliftRedeploy;
      if (!env) {
        toast.error("Force redeploy failed: no response from backend.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Force redeploy failed.");
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error("Force redeploy returned no payload.");
        return;
      }
      const counts =
        `Cancelled ${payload.deploymentsCancelled} deploy(s), deleted ${payload.k8sObjectsDeleted}` +
        " k8s object(s).";
      if (payload.workflowDispatched) {
        const tail = payload.runUrl ? ` Watch run: ${payload.runUrl}` : "";
        toast.success(`${counts} Workflow dispatched.${tail}`, { duration: 8000 });
      } else {
        // Partial success — cancellation + delete still landed even
        // though the CI dispatch failed. The operator can re-fire the
        // dispatch via the CI-setup section once the host is reachable.
        toast.warning(
          `${counts} Workflow dispatch failed: ${payload.dispatchMessage ?? "unknown error"}`,
          { duration: 10000 }
        );
      }
      setOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Force redeploy failed.");
    }
  }

  return (
    <Card className="border-destructive/40">
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="bg-destructive/10 text-destructive shrink-0 rounded-md p-2.5">
          <FlameIcon className="size-5" />
        </div>
        <div className="flex-1">
          <CardTitle className="text-base">{t("title")}</CardTitle>
          <CardDescription className="mt-1">{t("description")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <Can permission="app.deploy">
          <Can permission="app.update">
            <Button variant="destructive" onClick={() => setOpen(true)} disabled={loading}>
              {loading ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <FlameIcon className="size-4" />
              )}
              {t("button")}
            </Button>
          </Can>
        </Can>
        <p className="text-muted-foreground mt-2 text-[11px]">{t("hint")}</p>
      </CardContent>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <FlameIcon className="text-destructive size-4" />
              {t("confirmTitle", { slug: appSlug })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("confirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="grid gap-2 py-2">
            <Label htmlFor="force-redeploy-confirm" className="text-xs">
              {t.rich("typeToConfirm", {
                slug: () => (
                  <span className="text-foreground font-mono text-xs">{appSlug}</span>
                ),
              })}
            </Label>
            <Input
              id="force-redeploy-confirm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={appSlug}
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={loading}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleConfirm();
              }}
              disabled={!slugMatches || loading}
              className="bg-destructive hover:bg-destructive/90 text-white"
            >
              {loading ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {t("button")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

// ─── danger zone ──────────────────────────────────────────────────────────────

/**
 * Hard-deregister + full teardown surface (#392).
 *
 * Fires `DeregisterAppWorkflow` with a deterministic workflow id so
 * re-firing the mutation (same `app_slug`) joins the existing run via
 * Temporal de-dup — partial-failure resume is a one-click retry from
 * the same surface.
 *
 * The destructive button stays disabled until the operator types the
 * app's name verbatim (muscle-memory guard) — the backend enforces
 * the same check server-side via the `confirm_name` field on
 * `DeregisterAppInput`.
 */
const TEARDOWN_RESOURCE_LABELS: { key: string; label: string; detail: string }[] = [
  {
    key: "k8s_namespace",
    label: "Kubernetes namespace",
    detail: "Deployments, Services, Ingresses, ConfigMaps, PVCs, Pods.",
  },
  {
    key: "managed_services",
    label: "Managed services",
    detail:
      "Postgres / Redis / object storage — irreversibly deleted with delete_data + force_destroy.",
  },
  {
    key: "registry_repo",
    label: "Image registry repo",
    detail: "Archives the per-app repo + clears the stored URI.",
  },
  {
    key: "identity_role",
    label: "Workload identity role",
    detail: "Deletes the IRSA / WI / FI role bound to the app's ServiceAccount.",
  },
  {
    key: "materialized_secrets",
    label: "Cluster secrets",
    detail: "Drops materialized k8s Secrets + revokes the app's secret-bundle refs.",
  },
  {
    key: "source_webhook",
    label: "Source-host webhook",
    detail: "Removes the push-event hook the platform installed on the source repo.",
  },
  {
    key: "deploy_tokens",
    label: "Deploy tokens",
    detail: "Revokes every active deploy token so CI / cron pointed at the app fails loudly.",
  },
];

interface DeregisterResp {
  deregisterAstroliftApp: MutationResult<{
    workflowId: string;
    stillLiveResources: string[];
  }>;
}

function DangerZoneCard({ appSlug, appName }: { appSlug: string; appName: string }) {
  const t = useTranslations("apps.settings.dangerZone");
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [confirm, setConfirm] = React.useState("");
  const [stillLive, setStillLive] = React.useState<string[]>([]);
  const [deregister, { loading }] = useMutation<DeregisterResp>(DEREGISTER_APP);

  // Reset transient state whenever the modal closes so a re-open is
  // fresh; preserve `stillLive` between resume attempts so the
  // operator can see what's outstanding without reopening clean.
  React.useEffect(() => {
    if (!open) {
      setConfirm("");
    }
  }, [open]);

  const armed = confirm.trim() === appName && !loading;

  async function handleConfirm() {
    if (!armed) return;
    try {
      const { data } = await deregister({
        variables: { input: { appSlug, confirmName: confirm.trim() } },
      });
      const env = data?.deregisterAstroliftApp;
      if (!env) {
        toast.error("Deregister failed: no response from backend.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Deregister failed.");
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error("Deregister returned no payload.");
        return;
      }
      // Partial-failure resume returns the still-live list; the
      // mutation itself accepts the resume so a retry from this
      // surface is the recovery path. Initial kickoff returns an
      // empty list — happy-path redirect.
      if (payload.stillLiveResources.length > 0) {
        setStillLive(payload.stillLiveResources);
        toast.warning(
          `Resume pending — ${payload.stillLiveResources.length} resource class(es) still live.`,
        );
        return;
      }
      toast.success(
        `Deregistering — workflow ${payload.workflowId}. Check the app's Workflows tab for progress.`,
      );
      setOpen(false);
      router.push("/apps");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Deregister failed.");
    }
  }

  return (
    <>
      <Card className="border-destructive/40">
        <CardHeader className="flex flex-row items-start gap-3 space-y-0">
          <div className="bg-destructive/10 text-destructive shrink-0 rounded-md p-2.5">
            <Trash2Icon className="size-5" />
          </div>
          <div className="flex-1">
            <CardTitle className="flex flex-wrap items-center gap-2 text-base">
              {t("title")}
            </CardTitle>
            <CardDescription className="mt-1">{t("description")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <Can permission="app.delete">
            <Button variant="destructive" onClick={() => setOpen(true)}>
              <Trash2Icon className="size-4" />
              {t("button")}
            </Button>
          </Can>
          {stillLive.length > 0 ? (
            <div className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs">
              <p className="text-amber-700 dark:text-amber-400">
                {t("stillLive", { count: stillLive.length })}
              </p>
              <ul className="text-foreground mt-1 list-disc pl-5 font-mono text-[11px]">
                {stillLive.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              <Can permission="app.delete">
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  onClick={() => setOpen(true)}
                  disabled={loading}
                >
                  {t("retry")}
                </Button>
              </Can>
            </div>
          ) : (
            <p className="text-muted-foreground mt-2 text-[11px]">{t("softHint")}</p>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangleIcon className="text-destructive size-5" />
              {t("confirmTitle", { name: appName })}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm">
                <p>{t("confirmIntro")}</p>
                <ul className="bg-muted/40 space-y-2 rounded-md border p-3 text-xs">
                  {TEARDOWN_RESOURCE_LABELS.map((r) => (
                    <li key={r.key} className="flex flex-col gap-0.5">
                      <span className="text-foreground font-medium">{r.label}</span>
                      <span className="text-muted-foreground">{r.detail}</span>
                    </li>
                  ))}
                </ul>
                <div className="space-y-1.5">
                  <Label htmlFor="deregister-confirm" className="text-xs">
                    {t("typeToConfirm")}{" "}
                    <span className="font-mono">{appName}</span>
                  </Label>
                  <Input
                    id="deregister-confirm"
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    disabled={loading}
                  />
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={loading}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleConfirm();
              }}
              disabled={!armed}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {loading ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <Trash2Icon className="size-4" />
              )}
              {loading ? t("deregistering") : t("button")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ─── run scheduled job once (#390) ────────────────────────────────────────────

interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

interface EnvsListResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface RunJobOnceResp {
  runAstroliftJobOnce: MutationResult<{
    runName: string;
    namespace: string;
    logsUrl: string | null;
  }>;
}

/**
 * "Run scheduled job once" card on the settings landing (#390).
 *
 * Lists the app's manifest-declared cronjob workloads and dispatches
 * a single ad-hoc k8s Job built from the selected workload's
 * jobTemplate. The schedule is untouched — recurring runs continue
 * on their own. The card hides entirely when the manifest has zero
 * cronjob workloads so the surface stays uncluttered for apps that
 * don't ship any.
 *
 * Permission gate matches the backend: ``app.deploy`` is required to
 * land the Job on the tenant cluster. On success, the toast carries
 * the freshly-applied Job name and a link to the scheduled-job-runs
 * page where the run materializes once the cluster picks it up.
 */
function RunScheduledJobCard({ appSlug }: { appSlug: string }) {
  const t = useTranslations("apps.settings.runJob");
  const router = useRouter();
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = useQuery<EnvsListResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const [runJobOnce, { loading }] = useMutation<RunJobOnceResp>(RUN_JOB_ONCE);

  const cronJobs = (workloads.data?.astroliftWorkloads ?? []).filter(
    (w) => w.kind === "cronjob",
  );
  const environments = envs.data?.astroliftEnvironments ?? [];

  const [jobSlugOverride, setJobSlugOverride] = React.useState<string | null>(null);
  const [envNameOverride, setEnvNameOverride] = React.useState<string | null>(null);
  // Default to the first entry of each list — keeps the card actionable
  // for the common single-cronjob, single-env app without an extra
  // click. The operator's explicit selection (when one exists) wins.
  const jobSlug = jobSlugOverride ?? cronJobs[0]?.slug ?? "";
  const envName = envNameOverride ?? environments[0]?.name ?? "";

  // Hide the card while the workloads query is still in flight on
  // the first load — flashing an empty card then a populated one
  // is worse than waiting a tick. Hide it permanently when the
  // manifest has zero cronjob workloads.
  if (workloads.loading && !workloads.data) {
    return null;
  }
  if (cronJobs.length === 0) {
    return null;
  }

  async function handleRun() {
    if (!jobSlug || !envName) return;
    try {
      const { data } = await runJobOnce({
        variables: {
          input: {
            appSlug,
            environmentName: envName,
            jobSlug,
          },
        },
      });
      const env = data?.runAstroliftJobOnce;
      if (!env) {
        toast.error("Run failed: no response from backend.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Run failed.");
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error("Run returned no payload.");
        return;
      }
      const message = `Started job ${payload.runName} in ${payload.namespace}.`;
      const logsUrl = payload.logsUrl;
      if (logsUrl) {
        toast.success(message, {
          duration: 8000,
          action: {
            label: t("viewRuns"),
            onClick: () => router.push(logsUrl),
          },
        });
      } else {
        toast.success(message);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Run failed.");
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
          <TimerIcon className="size-5" />
        </div>
        <div className="flex-1">
          <CardTitle className="text-base">{t("title")}</CardTitle>
          <CardDescription className="mt-1">{t("description")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <div className="grid gap-1.5">
            <Label htmlFor="run-job-job-slug" className="text-xs">
              {t("job")}
            </Label>
            <Select value={jobSlug} onValueChange={setJobSlugOverride}>
              <SelectTrigger id="run-job-job-slug" className="w-full">
                <SelectValue placeholder={t("selectJob")} />
              </SelectTrigger>
              <SelectContent>
                {cronJobs.map((w) => (
                  <SelectItem key={w.slug} value={w.slug}>
                    <span className="font-mono text-xs">{w.slug}</span>
                    {w.schedule ? (
                      <span className="text-muted-foreground ml-2 text-[11px]">{w.schedule}</span>
                    ) : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="run-job-env" className="text-xs">
              {t("environment")}
            </Label>
            <Select value={envName} onValueChange={setEnvNameOverride}>
              <SelectTrigger id="run-job-env" className="w-full">
                <SelectValue placeholder={t("selectEnv")} />
              </SelectTrigger>
              <SelectContent>
                {environments.map((e) => (
                  <SelectItem key={e.id} value={e.name}>
                    {e.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Can permission="app.deploy">
            <Button
              onClick={handleRun}
              disabled={loading || !jobSlug || !envName}
              className="sm:self-end"
            >
              {loading ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayCircleIcon className="size-4" />
              )}
              {t("run")}
            </Button>
          </Can>
        </div>
        <p className="text-muted-foreground mt-3 text-[11px]">
          {t("footer")}{" "}
          <Link href={`/apps/${appSlug}/jobs`} className="underline">
            {t("jobsPage")}
          </Link>
          .
        </p>
      </CardContent>
    </Card>
  );
}
