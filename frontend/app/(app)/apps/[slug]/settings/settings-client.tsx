"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  ArchiveIcon,
  BoxIcon,
  ChevronRightIcon,
  DatabaseIcon,
  FileCodeIcon,
  FlameIcon,
  GlobeIcon,
  KeyIcon,
  LineChartIcon,
  Loader2Icon,
  LockIcon,
  PauseIcon,
  PencilIcon,
  PlayCircleIcon,
  PlayIcon,
  PlugIcon,
  PlusIcon,
  RefreshCwIcon,
  ShieldIcon,
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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Section } from "@/components/ui/section";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  CANCEL_DEREGISTER,
  CLEAR_ENVIRONMENT_SETTING,
  DEREGISTER_APP,
  FORCE_REDEPLOY,
  PAUSE_APP_INGRESS,
  RESUME_APP_INGRESS,
  RUN_JOB_ONCE,
  SET_ENVIRONMENT_SETTING,
} from "@/graphql/lifecycle/lifecycle.mutations";
import {
  LIST_ENVIRONMENTS,
  PREVIEW_DEREGISTER_APP,
  PREVIEW_FORCE_REDEPLOY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeregisterPreview,
  AstroliftEnvironmentSetting,
  AstroliftForceRedeployPreview,
} from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ARCHIVE_APP,
  PAUSE_APP_WEBHOOK_DEPLOYS,
  RESTORE_APP,
  RESUME_APP_WEBHOOK_DEPLOYS,
  RESYNC_MANIFEST_FROM_REPO,
  SET_RETENTION_POLICY,
} from "@/graphql/registry/registry.mutations";
import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftRetentionPolicy,
  AstroliftWorkload,
} from "@/graphql/registry/registry.types";
import {
  REPROVISION_MANAGED_SERVICE,
  UPDATE_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import type { AstroliftManagedService } from "@/graphql/services/services.types";
import { formatRelativeAge } from "@/lib/format";
import { useFormatters } from "@/lib/i18n/formatters";

import { AppTabs } from "../components/app-tabs";
import { AssignProjectCard } from "../components/assign-project-card";
import { CiSetupSection } from "../components/ci-setup-section";
import { ControlsSection } from "../components/controls-section";
import { DeployStrategyCard } from "../components/deploy-strategy-card";
import {
  DeregisterPendingBanner,
  recordDeregisterPending,
} from "../components/deregister-pending-banner";
import { ManagedServicesSummaryCard } from "../components/managed-services-summary-card";
import { TeamsCard } from "../components/teams-card";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

/** Field name on ``AstroliftAppSettingsLastModified`` whose timestamp
 *  drives this card's "Modified N ago" caption (#454). The wrapper is
 *  keyed by section because each card needs to know which sub-resource
 *  owns its freshness signal (deploy-tokens → DeployToken.updatedAt,
 *  secrets → AppSecretBundleRef.updatedAt, etc.). The backend exposes
 *  ``deployStrategy`` as the on-row proxy for the deploy-strategy card
 *  since that section has no separate resource. */
type SettingsSectionTimestampKey =
  | "deployStrategy"
  | "deployTokens"
  | "secrets"
  | "managedServices"
  | "domains"
  | "webhooks"
  | "members"
  | "observability";

interface LinkSection {
  key: string;
  i18nKey: string;
  href: (slug: string) => string;
  icon: typeof KeyIcon;
  lastModifiedKey: SettingsSectionTimestampKey;
}

/** Ordered list of link cards that compose the settings landing. Inline
 *  controls (deploys, ingress) live above; danger zone lives below.
 *
 *  ``lastModifiedKey`` ties each card to the matching field on
 *  ``app.settingsLastModified`` (#454) so the caption reflects the
 *  staleness of the section's primary resource rather than the whole
 *  app's ``updatedAt``. */
const LINK_SECTIONS: LinkSection[] = [
  // #400: Deploy strategy now renders as an inline DeployStrategyCard
  // (badge + edit affordance) above this list; the "Edit manifest TOML"
  // route is preserved as its own link card so operators can still drop
  // into the full TOML editor for advanced changes.
  {
    key: "manifest",
    i18nKey: "manifestEditor",
    href: (s) => `/apps/${s}/config`,
    icon: FileCodeIcon,
    lastModifiedKey: "deployStrategy",
  },
  {
    key: "deploy-tokens",
    i18nKey: "deployTokens",
    href: (s) => `/apps/${s}/tokens`,
    icon: KeyIcon,
    lastModifiedKey: "deployTokens",
  },
  {
    key: "secrets",
    i18nKey: "secrets",
    href: (s) => `/apps/${s}/secrets`,
    icon: LockIcon,
    lastModifiedKey: "secrets",
  },
  {
    key: "managed-services",
    i18nKey: "managedServices",
    href: (s) => `/apps/${s}/managed-services`,
    icon: PlugIcon,
    lastModifiedKey: "managedServices",
  },
  {
    key: "domains",
    i18nKey: "domains",
    href: (s) => `/apps/${s}/domains`,
    icon: GlobeIcon,
    lastModifiedKey: "domains",
  },
  {
    key: "webhooks",
    i18nKey: "webhooks",
    href: (s) => `/apps/${s}/webhooks`,
    icon: WebhookIcon,
    lastModifiedKey: "webhooks",
  },
  {
    key: "members",
    i18nKey: "members",
    href: (s) => `/apps/${s}/members`,
    icon: UsersIcon,
    lastModifiedKey: "members",
  },
  {
    key: "observability",
    i18nKey: "observability",
    href: (s) => `/apps/${s}/observability`,
    icon: LineChartIcon,
    lastModifiedKey: "observability",
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
            slug: () => <span className="text-foreground">{a.slug}</span>,
          })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="settings" />

      {/* Grace-period cancel banner (#436 B). Mounts above the
          inline controls so the operator sees the countdown
          immediately after kicking off a deregister, regardless of
          which subpage they navigate to next. */}
      <DeregisterPendingBanner appSlug={a.slug} />

      <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />

      <ResyncSourceSection appSlug={a.slug} lastResyncAt={a.lastResyncAt ?? null} />

      <WebhookDeploysPauseSection
        appSlug={a.slug}
        paused={a.webhookDeploysPaused}
        pausedAt={a.webhookDeploysPausedAt ?? null}
        pausedByEmail={a.webhookDeploysPausedByEmail ?? null}
        pauseReason={a.webhookDeploysPauseReason ?? ""}
      />

      <IngressControlsSection appSlug={a.slug} />

      <AssignProjectCard
        appSlug={a.slug}
        currentProjectId={a.projectId ?? null}
        currentProjectName={a.projectName}
        currentTeamName={a.teamName}
      />

      <TeamsCard appSlug={a.slug} appId={a.id} homeTeamSlug={a.teamSlug} />

      <ManagedServicesSummaryCard appSlug={a.slug} />

      <ManagedServicesAdminSection appSlug={a.slug} />

      <RetentionPolicySection appSlug={a.slug} policies={a.retentionPolicies ?? []} />

      <EnvironmentSettingsSection appSlug={a.slug} />

      <ArchiveSection
        appSlug={a.slug}
        appName={a.name}
        isArchived={a.isArchived}
        archivedAt={a.archivedAt ?? null}
      />

      {/* Inline deploy-strategy badge + edit affordance (#400). Replaces
          the link-card to /config for the strategy itself — the manifest
          editor is still reachable via the "Edit manifest TOML" card in
          the LINK_SECTIONS list below for advanced edits. */}
      <DeployStrategyCard app={a} />

      <div className="flex flex-col gap-3">
        {LINK_SECTIONS.map((s) => {
          // Per-section "Modified N ago" (#454). Each card pulls its
          // own timestamp off the resolver-derived rollup so the
          // staleness cue is accurate per surface (deploy-tokens
          // freshness vs secrets freshness vs domains freshness) rather
          // than the whole-app proxy that #437 scope E shipped with.
          // Null means the section has no underlying rows yet — the
          // helper hides the caption rather than rendering a default.
          const sectionLastModified = a.settingsLastModified?.[s.lastModifiedKey] ?? null;
          const card = (
            <SettingsLinkCard
              key={s.key}
              section={s}
              slug={a.slug}
              lastModifiedAt={sectionLastModified}
            />
          );
          // CI-setup section slots between the manifest-editor link
          // and Deploy tokens (#382). Hidden when the app has no source
          // repo — there's nothing to wire up to until registration
          // captures one. Sibling-friendly: appending a fragment under
          // the manifest row, no surrounding reformatting.
          // (#400 renamed the section key from "deploy-strategy" →
          // "manifest" when the inline DeployStrategyCard moved above
          // the link list; the CI block still anchors to the same
          // visual slot.)
          if (s.key === "manifest" && a.sourceRepo) {
            return (
              <React.Fragment key="manifest-with-ci">
                {card}
                <CiSetupSection
                  appSlug={a.slug}
                  registryUri={a.ecrRepoUri}
                  pushCredentialRef={a.ecrPushRoleArn}
                  providerPluginSlug={a.providerPluginSlug}
                  sourceWebhookInstalledAt={a.sourceWebhookInstalledAt ?? null}
                />
                <ForceRedeploySection appSlug={a.slug} />
              </React.Fragment>
            );
          }
          // Apps without a source repo still benefit from the force-
          // redeploy card — the cancel + delete steps don't depend on
          // the CI pipeline. Slot it directly under the manifest row
          // when the CI block is hidden so the visual hierarchy
          // matches the with-repo path.
          if (s.key === "manifest" && !a.sourceRepo) {
            return (
              <React.Fragment key="manifest-with-force-redeploy">
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
    <Section
      title={t("title")}
      description={t("description")}
      action={
        <Can permission="app.update">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button size="sm" variant="outline" onClick={handleResync} disabled={loading}>
                {loading ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCwIcon className="size-3.5" />
                )}
                {loading ? t("syncing") : t("button")}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">{t("buttonTooltip")}</TooltipContent>
          </Tooltip>
        </Can>
      }
    >
      <p className="text-muted-foreground text-2xs">
        {lastResyncAt ? (
          <>
            {t("last")}{" "}
            <span className="text-foreground">{fmt.formatRelativeTime(lastResyncAt)}</span>.
          </>
        ) : (
          t("never")
        )}
      </p>
    </Section>
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
    <Section title={t("title")} description={t("description")}>
      {loading && envs.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{t("emptyEnvs")}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {envs.map((env) => (
            <IngressRow key={env.id} env={env} appSlug={appSlug} />
          ))}
        </div>
      )}
    </Section>
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
            className="border-warning-border bg-warning/10 text-warning-fg"
          >
            <PauseIcon className="size-3" />
            {t("paused")}
          </Badge>
        ) : (
          <Badge
            variant="outline"
            className="border-success-border bg-success/10 text-success-fg"
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

// ─── webhook deploys pause (#399) ─────────────────────────────────────────────

interface PauseWebhookDeploysResp {
  pauseAstroliftAppWebhookDeploys: MutationResult<{
    id: string;
    slug: string;
    webhookDeploysPaused: boolean;
    webhookDeploysPausedAt: string | null;
    webhookDeploysPausedByEmail: string | null;
    webhookDeploysPauseReason: string;
  }>;
}
interface ResumeWebhookDeploysResp {
  resumeAstroliftAppWebhookDeploys: MutationResult<{
    id: string;
    slug: string;
    webhookDeploysPaused: boolean;
    webhookDeploysPausedAt: string | null;
    webhookDeploysPausedByEmail: string | null;
    webhookDeploysPauseReason: string;
  }>;
}

/**
 * App-global webhook-deploy pause toggle (#399).
 *
 * Independent of the per-environment `deploys_paused` (#378) and
 * `ingress_paused` axes. Stops the deploy storm from CI / push /
 * scheduled triggers across every environment of this app without
 * paging through each env's controls and without taking ingress down.
 * Manual deploys from the UI / CLI continue to flow — explicit
 * on-call escape valve so a wedged CI can be stopped without locking
 * the operator out of fixing the app.
 *
 * UX shape mirrors the IngressRow pattern:
 * - Live pill (emerald play) ↔ Paused pill (amber pause).
 * - Toggle on OFF→ON opens a confirm dialog with an optional reason
 *   textarea — the reason lands on the audit log AND the row, surfaced
 *   below as "Paused by X · 5m ago — reason: <reason>".
 * - OFF→ON only — resuming is one click (the inverse always works).
 * - Hidden behind `app.deploy` for read-only viewers.
 */
function WebhookDeploysPauseSection({
  appSlug,
  paused,
  pausedAt,
  pausedByEmail,
  pauseReason,
}: {
  appSlug: string;
  paused: boolean;
  pausedAt: string | null;
  pausedByEmail: string | null;
  pauseReason: string;
}) {
  const t = useTranslations("apps.settings.webhookDeploys");
  const fmt = useFormatters();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");

  const refetch = [{ query: GET_APP, variables: { slug: appSlug } }];
  const [pause, { loading: pausing }] = useMutation<PauseWebhookDeploysResp>(
    PAUSE_APP_WEBHOOK_DEPLOYS,
    { refetchQueries: refetch, awaitRefetchQueries: true }
  );
  const [resume, { loading: resuming }] = useMutation<ResumeWebhookDeploysResp>(
    RESUME_APP_WEBHOOK_DEPLOYS,
    { refetchQueries: refetch, awaitRefetchQueries: true }
  );
  const busy = pausing || resuming;

  React.useEffect(() => {
    if (!confirmOpen) setReason("");
  }, [confirmOpen]);

  async function handlePauseConfirm() {
    try {
      const { data } = await pause({
        variables: { input: { appSlug, reason: reason.trim() || null } },
      });
      const env = data?.pauseAstroliftAppWebhookDeploys;
      if (!env) {
        toast.error(t("toastNoResponse"));
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? t("toastPauseFailed"));
        return;
      }
      toast.success(t("toastPaused"));
      setConfirmOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastPauseFailed"));
    }
  }

  async function handleResume() {
    try {
      const { data } = await resume({ variables: { input: { appSlug } } });
      const env = data?.resumeAstroliftAppWebhookDeploys;
      if (!env) {
        toast.error(t("toastNoResponse"));
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? t("toastResumeFailed"));
        return;
      }
      toast.success(t("toastResumed"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastResumeFailed"));
    }
  }

  return (
    <Section
      title={t("title")}
      description={t("description")}
      action={
        <>
          {paused ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              <PauseIcon className="size-3" />
              {t("paused")}
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-success-border bg-success/10 text-success-fg"
            >
              <PlayIcon className="size-3" />
              {t("live")}
            </Badge>
          )}
          <Can permission="app.deploy">
            {paused ? (
              <Button size="sm" variant="default" onClick={handleResume} disabled={busy}>
                {busy ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <PlayIcon className="size-3.5" />
                )}
                {t("resume")}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setConfirmOpen(true)}
                disabled={busy}
              >
                {busy ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <PauseIcon className="size-3.5" />
                )}
                {t("pause")}
              </Button>
            )}
          </Can>
        </>
      }
    >
      {/* Audit / explainer footer — the paused branch surfaces the
          actor + time + reason so the operator can spot a stale pause
          at a glance; the live branch reminds operators what the
          toggle's scope is so the next "why is CI not deploying?"
          page lands here first. */}
      {paused ? (
        <div className="space-y-1.5 text-xs">
          <p className="text-foreground">
            {t.rich("pausedBy", {
              who: () => (
                <span className="text-foreground font-medium">
                  {pausedByEmail ?? t("unknownActor")}
                </span>
              ),
              when: () => (
                <span className="text-muted-foreground">
                  {pausedAt ? fmt.formatRelativeTime(pausedAt) : t("unknownTime")}
                </span>
              ),
            })}
          </p>
          {pauseReason ? (
            <p className="text-muted-foreground">
              {t("reasonPrefix")} <span className="text-foreground">{pauseReason}</span>
            </p>
          ) : (
            <p className="text-muted-foreground italic">{t("noReason")}</p>
          )}
          <p className="text-muted-foreground text-2xs">{t("scopeNotePaused")}</p>
        </div>
      ) : (
        <p className="text-muted-foreground text-2xs">{t("scopeNoteLive")}</p>
      )}

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <PauseIcon className="size-4 text-warning-fg" />
              {t("confirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("confirmDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="grid gap-2 py-2">
            <Label htmlFor="webhook-pause-reason" className="text-xs">
              {t("reasonLabel")}
            </Label>
            <Textarea
              id="webhook-pause-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("reasonPlaceholder")}
              rows={3}
              maxLength={512}
            />
            <p className="text-muted-foreground text-2xs">{t("reasonHelp")}</p>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pausing}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handlePauseConfirm();
              }}
              disabled={pausing}
              className="bg-amber-600 text-white hover:bg-amber-700"
            >
              {pausing ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {t("confirmButton")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}

// ─── link card ────────────────────────────────────────────────────────────────

function SettingsLinkCard({
  section,
  slug,
  lastModifiedAt,
}: {
  section: LinkSection;
  slug: string;
  lastModifiedAt?: string | null;
}) {
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
            {lastModifiedAt && (
              <p className="text-muted-foreground mt-1 text-xs">
                Modified {formatRelativeAge(lastModifiedAt)}
              </p>
            )}
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

interface ForceRedeployPreviewResp {
  previewAstroliftForceRedeploy: AstroliftForceRedeployPreview | null;
}

/**
 * Destructive recovery card for wedged apps (#389 + #436 D).
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
 *
 * #436 D — in-flight deployment preview: the modal fetches
 * `previewAstroliftForceRedeploy` on open and renders the list of
 * Deployment rows the recovery path will transition to FAILED.
 * Operator sees timestamps, who triggered, and image tags — enough
 * provenance to weigh "let this finish" vs "blow it away".
 */
function ForceRedeploySection({ appSlug }: { appSlug: string }) {
  const t = useTranslations("apps.settings.forceRedeploy");
  const tPreview = useTranslations("apps.settings.forceRedeploy.preview");
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [typed, setTyped] = React.useState("");
  const [forceRedeploy, { loading }] = useMutation<ForceRedeployResp>(FORCE_REDEPLOY);

  // Lazy-load the in-flight preview when the modal opens. ``cache-and-
  // network`` keeps the list fresh between reopens — a deploy that
  // landed since the previous open shouldn't be invisible.
  const [loadPreview, previewQuery] = useLazyQuery<ForceRedeployPreviewResp>(
    PREVIEW_FORCE_REDEPLOY,
    {
      fetchPolicy: "cache-and-network",
    }
  );

  React.useEffect(() => {
    if (open) {
      void loadPreview({ variables: { appSlug } });
    } else {
      setTyped("");
    }
  }, [open, loadPreview, appSlug]);

  // Coerce out of useLazyQuery's DeepPartial<TData> envelope once the
  // top-level field is present — children below all live on the same
  // resolver, so either the whole shape lands or `data` is undefined.
  const preview =
    (previewQuery.data?.previewAstroliftForceRedeploy as
      | AstroliftForceRedeployPreview
      | undefined) ?? null;
  const previewLoading = previewQuery.loading && !preview;
  const inFlight = preview?.inFlightDeployments ?? [];

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
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="destructive" onClick={() => setOpen(true)} disabled={loading}>
                  {loading ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <FlameIcon className="size-4" />
                  )}
                  {t("button")}
                </Button>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm">{t("buttonTooltip")}</TooltipContent>
            </Tooltip>
          </Can>
        </Can>
        <p className="text-muted-foreground mt-2 text-2xs">{t("hint")}</p>
      </CardContent>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent className="max-h-[90vh] overflow-y-auto">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <FlameIcon className="text-destructive size-4" />
              {t("confirmTitle", { slug: appSlug })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("confirmDescription")}</AlertDialogDescription>
          </AlertDialogHeader>

          {/* In-flight deployment preview (#436 D) — the rows below
              are exactly what the recovery path transitions to FAILED
              before re-firing CI. ``inFlight.length === 0`` is the
              calm path: no live deploys to interrupt. */}
          <div className="bg-muted/40 my-2 space-y-2 rounded-md border p-3 text-xs">
            <p className="font-medium">
              {previewLoading
                ? tPreview("loading")
                : tPreview("header", { count: inFlight.length })}
            </p>
            {previewLoading ? (
              <Skeleton className="h-12 w-full" />
            ) : inFlight.length === 0 ? (
              <p className="text-muted-foreground">{tPreview("noInflight")}</p>
            ) : (
              <ul className="space-y-1.5">
                {inFlight.map((d) => (
                  <li
                    key={d.id}
                    className="border-border/60 flex flex-col gap-0.5 rounded-md border bg-transparent p-2 font-mono"
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline" className="text-2xs">
                        {d.status}
                      </Badge>
                      <span className="text-foreground">{d.environmentName}</span>
                      {d.workloadSlug ? (
                        <span className="text-muted-foreground">/ {d.workloadSlug}</span>
                      ) : null}
                      {d.imageTag ? (
                        <span className="text-muted-foreground">@ {d.imageTag}</span>
                      ) : null}
                    </div>
                    <p className="text-muted-foreground text-2xs">
                      {tPreview("triggeredBy", {
                        actor: d.triggeredByDisplay,
                        when: fmt.formatRelativeTime(d.startedAt ?? d.createdAt),
                      })}
                      {d.ciRunUrl ? (
                        <>
                          {" · "}
                          <a
                            href={d.ciRunUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="underline"
                          >
                            {tPreview("ciRun")}
                          </a>
                        </>
                      ) : null}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="grid gap-2 py-2">
            <Label htmlFor="force-redeploy-confirm" className="text-xs">
              {t.rich("typeToConfirm", {
                slug: () => <span className="text-foreground font-mono text-xs">{appSlug}</span>,
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
 * Hard-deregister + full teardown surface (#392 + #436 A/B/C).
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
 *
 * #436 A — blast-radius preview: the modal fetches
 * `previewAstroliftDeregister` on open and renders an expandable
 * grouped list (k8s / managed-services / identity / network / secrets)
 * with the actual object names the workflow will tear down. The
 * trigger button carries a resource-count badge so the operator sees
 * the magnitude before clicking through.
 *
 * #436 B — grace-period cancel: a successful kickoff writes a pending
 * entry to localStorage; the {@link DeregisterPendingBanner} at the
 * top of the page consumes it and renders the 5-min countdown +
 * Cancel CTA. The cancel mutation signals the workflow within the
 * window.
 *
 * #436 C — partial-failure resume: the still-live banner gets an
 * inline Retry CTA that fires the deregister mutation directly
 * (same workflow id → Temporal de-dup joins the existing run). No
 * modal reopen, no re-type.
 */

interface DeregisterResp {
  deregisterAstroliftApp: MutationResult<{
    workflowId: string;
    stillLiveResources: string[];
  }>;
}

interface PreviewResp {
  previewAstroliftDeregister: AstroliftDeregisterPreview | null;
}

interface ResourceGroupSpec {
  key: string;
  icon: typeof BoxIcon;
  count: number;
  body: React.ReactNode;
}

function ResourceGroup({ group, labelKey }: { group: ResourceGroupSpec; labelKey: string }) {
  const t = useTranslations("apps.settings.dangerZone.preview.groups");
  const [open, setOpen] = React.useState(false);
  const Icon = group.icon;
  if (group.count <= 0) return null;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="hover:bg-muted/60 flex w-full items-center gap-2 rounded-md border bg-transparent px-2 py-1.5 text-left text-xs"
        >
          <ChevronRightIcon
            className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`}
          />
          <Icon className="text-muted-foreground size-4" />
          <span className="text-foreground flex-1 font-medium">{t(labelKey)}</span>
          <Badge variant="secondary" className="text-2xs">
            {group.count}
          </Badge>
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1 pl-7">{group.body}</CollapsibleContent>
    </Collapsible>
  );
}

function DangerZoneCard({ appSlug, appName }: { appSlug: string; appName: string }) {
  const t = useTranslations("apps.settings.dangerZone");
  const tPreview = useTranslations("apps.settings.dangerZone.preview");
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [confirm, setConfirm] = React.useState("");
  const [stillLive, setStillLive] = React.useState<string[]>([]);
  const [deregister, { loading }] = useMutation<DeregisterResp>(DEREGISTER_APP);

  // Lazy-load the preview when the modal opens so closed-modal renders
  // don't fire a network call. ``cache-and-network`` keeps the count
  // badge fresh whenever the modal reopens — the resource list can
  // change between attempts (operator created/deleted services in a
  // sibling tab) and a stale badge would mislead.
  const [loadPreview, previewQuery] = useLazyQuery<PreviewResp>(PREVIEW_DEREGISTER_APP, {
    fetchPolicy: "cache-and-network",
  });

  React.useEffect(() => {
    if (open) {
      void loadPreview({ variables: { appSlug } });
    } else {
      setConfirm("");
    }
  }, [open, loadPreview, appSlug]);

  // useLazyQuery returns a DeepPartial<TData> on `data` to model the
  // "haven't fired yet" state; coerce to the full type once we've
  // checked the top-level field is present.
  const preview =
    (previewQuery.data?.previewAstroliftDeregister as AstroliftDeregisterPreview | undefined) ??
    null;
  const previewLoading = previewQuery.loading && !preview;

  const armed = confirm.trim() === appName && !loading;

  // Group the preview rows by destination so the expander tree maps
  // 1:1 with the workflow's per-step ordering (k8s → managed
  // services → identity → secrets → network).
  const k8sBody = React.useMemo(() => {
    if (!preview || preview.k8sObjects.length === 0) return null;
    // Group k8s objects by (cluster, namespace) for readability.
    const groups = new Map<string, typeof preview.k8sObjects>();
    for (const o of preview.k8sObjects) {
      const key = `${o.clusterSlug}/${o.namespace}`;
      const arr = groups.get(key) ?? [];
      arr.push(o);
      groups.set(key, arr);
    }
    return (
      <ul className="space-y-2 text-2xs">
        {Array.from(groups.entries()).map(([key, items]) => (
          <li key={key}>
            <p className="text-muted-foreground font-mono">{key}</p>
            <ul className="mt-0.5 list-disc pl-5 font-mono">
              {items.map((o) => (
                <li key={`${key}-${o.apiVersion}-${o.kind}-${o.name}`}>
                  <span className="text-muted-foreground">{o.kind}</span>{" "}
                  <span className="text-foreground">{o.name}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const managedServicesBody = React.useMemo(() => {
    if (!preview || preview.managedServices.length === 0) return null;
    return (
      <ul className="space-y-1 text-2xs">
        {preview.managedServices.map((s) => (
          <li key={s.id} className="flex items-center gap-2 font-mono">
            <span className="text-foreground">{s.name || s.kind}</span>
            <Badge variant="outline" className="text-2xs">
              {s.kind}
              {s.variant ? `/${s.variant}` : ""}
            </Badge>
            <span className="text-muted-foreground">{s.environmentName}</span>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const secretsBody = React.useMemo(() => {
    if (!preview || preview.secretRefs.length === 0) return null;
    return (
      <ul className="space-y-1 font-mono text-2xs">
        {preview.secretRefs.map((r) => (
          <li key={r.id}>
            <span className="text-foreground">{r.bundleSlug}</span>
            {r.prefix ? <span className="text-muted-foreground"> ({r.prefix})</span> : null}{" "}
            <span className="text-muted-foreground">→ {r.environmentName}</span>
            {r.clusterSlug ? (
              <span className="text-muted-foreground"> @ {r.clusterSlug}</span>
            ) : null}
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const tokensBody = React.useMemo(() => {
    if (!preview || preview.deployTokens.length === 0) return null;
    return (
      <ul className="space-y-1 font-mono text-2xs">
        {preview.deployTokens.map((tok) => (
          <li key={tok.id}>
            <span className="text-foreground">{tok.name}</span>
            <span className="text-muted-foreground"> ····{tok.last4}</span>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const identityBody = React.useMemo(() => {
    if (!preview || preview.identityRoles.length === 0) return null;
    return (
      <ul className="space-y-1 font-mono text-2xs">
        {preview.identityRoles.map((r) => (
          <li key={`${r.clusterSlug}-${r.roleArnOrPrincipal}`}>
            <span className="text-muted-foreground">{r.clusterSlug}</span>{" "}
            <span className="text-foreground">{r.roleArnOrPrincipal}</span>
            <Badge variant="outline" className="ml-1 text-2xs">
              {r.kind}
            </Badge>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const networkBody = React.useMemo(() => {
    if (!preview) return null;
    const hasWebhook = preview.sourceWebhook?.installed === true;
    const hasRegistry = !!preview.registryRepoUri;
    if (!hasWebhook && !hasRegistry) return null;
    return (
      <ul className="space-y-1 font-mono text-2xs">
        {hasWebhook && preview.sourceWebhook ? (
          <li>
            <span className="text-muted-foreground">{tPreview("sourceWebhookLabel")} →</span>{" "}
            <span className="text-foreground">{preview.sourceWebhook.repo}</span>
            <span className="text-muted-foreground"> #{preview.sourceWebhook.hookId}</span>
          </li>
        ) : null}
        {hasRegistry ? (
          <li>
            <span className="text-muted-foreground">{tPreview("registryRepoLabel")} →</span>{" "}
            <span className="text-foreground break-all">{preview.registryRepoUri}</span>
          </li>
        ) : null}
      </ul>
    );
  }, [preview, tPreview]);

  const groups: ResourceGroupSpec[] = preview
    ? [
        { key: "k8s", icon: BoxIcon, count: preview.k8sObjects.length, body: k8sBody },
        {
          key: "managedServices",
          icon: DatabaseIcon,
          count: preview.managedServices.length,
          body: managedServicesBody,
        },
        {
          key: "secrets",
          icon: LockIcon,
          count: preview.secretRefs.length,
          body: secretsBody,
        },
        {
          key: "deployTokens",
          icon: KeyIcon,
          count: preview.deployTokens.length,
          body: tokensBody,
        },
        {
          key: "identity",
          icon: ShieldIcon,
          count: preview.identityRoles.length,
          body: identityBody,
        },
        {
          key: "network",
          icon: WebhookIcon,
          count: (preview.sourceWebhook?.installed ? 1 : 0) + (preview.registryRepoUri ? 1 : 0),
          body: networkBody,
        },
      ]
    : [];

  async function fireDeregister(opts: { confirmName?: string } = {}) {
    const confirmName = opts.confirmName ?? confirm.trim();
    try {
      const { data } = await deregister({
        variables: { input: { appSlug, confirmName } },
      });
      const env = data?.deregisterAstroliftApp;
      if (!env) {
        toast.error(t("toastNoResponse"));
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? t("toastFailed"));
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error(t("toastNoPayload"));
        return;
      }
      // Partial-failure resume returns the still-live list; the
      // mutation itself accepts the resume so a retry from this
      // surface is the recovery path. Initial kickoff returns an
      // empty list — happy-path redirect.
      if (payload.stillLiveResources.length > 0) {
        setStillLive(payload.stillLiveResources);
        toast.warning(t("toastResumePending", { count: payload.stillLiveResources.length }));
        return;
      }
      // Pin the grace-period countdown banner so the operator can
      // cancel within the 5-min window from any app subpage.
      recordDeregisterPending(appSlug, payload.workflowId);
      setStillLive([]);
      toast.success(t("toastKickoff", { workflowId: payload.workflowId }));
      setOpen(false);
      router.push("/apps");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastFailed"));
    }
  }

  async function handleConfirm() {
    if (!armed) return;
    await fireDeregister();
  }

  // Partial-failure resume (#436 C): the retry CTA re-fires the
  // mutation against the same workflow id (Temporal de-dup joins the
  // existing run) without re-opening the modal. We pass the app name
  // through so the backend's confirm-name guard still passes — the
  // operator already typed it once.
  async function handleRetry() {
    await fireDeregister({ confirmName: appName });
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
              {preview && preview.totalResourceCount > 0 ? (
                <Badge variant="secondary" className="ml-1.5 text-2xs">
                  {preview.totalResourceCount}
                </Badge>
              ) : null}
            </Button>
          </Can>
          {stillLive.length > 0 ? (
            <div className="mt-3 rounded-md border border-warning-border bg-warning/5 p-3 text-xs">
              <p className="text-warning-fg">
                {t("stillLive", { count: stillLive.length })}
              </p>
              <ul className="text-foreground mt-1 list-disc pl-5 font-mono text-2xs">
                {stillLive.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              <Can permission="app.delete">
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  onClick={() => void handleRetry()}
                  disabled={loading}
                >
                  {loading ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <RefreshCwIcon className="size-4" />
                  )}
                  {t("retry")}
                </Button>
              </Can>
            </div>
          ) : (
            <p className="text-muted-foreground mt-2 text-2xs">{t("softHint")}</p>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent className="max-h-[90vh] overflow-y-auto">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangleIcon className="text-destructive size-5" />
              {t("confirmTitle", { name: appName })}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm">
                <p>{t("confirmIntro")}</p>

                {/* Blast-radius preview (#436 A) — collapsed by
                    default. Each group expands to the actual object
                    names the workflow will tear down. */}
                <div className="bg-muted/40 space-y-2 rounded-md border p-3">
                  <p className="text-xs font-medium">
                    {previewLoading
                      ? tPreview("loading")
                      : tPreview("header", {
                          count: preview?.totalResourceCount ?? 0,
                        })}
                  </p>
                  {previewLoading ? (
                    <Skeleton className="h-16 w-full" />
                  ) : preview && preview.totalResourceCount > 0 ? (
                    <div className="space-y-1.5">
                      {groups.map((g) => (
                        <ResourceGroup key={g.key} group={g} labelKey={g.key} />
                      ))}
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-xs">{tPreview("noResources")}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="deregister-confirm" className="text-xs">
                    {t("typeToConfirm")} <span className="font-mono">{appName}</span>
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

  const cronJobs = (workloads.data?.astroliftWorkloads ?? []).filter((w) => w.kind === "cronjob");
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
                      <span className="text-muted-foreground ml-2 text-2xs">{w.schedule}</span>
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
        <p className="text-muted-foreground mt-3 text-2xs">
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

// ─── retention policy ─────────────────────────────────────────────────────────

const RETENTION_SIGNALS: { signal: string; label: string }[] = [
  { signal: "logs", label: "Logs" },
  { signal: "metrics", label: "Metrics" },
  { signal: "traces", label: "Traces" },
  { signal: "audit_events", label: "Audit Events" },
];

const RETENTION_DAY_OPTIONS = [7, 14, 30, 60, 90, 180, 365] as const;
const RETENTION_DEFAULT_DAYS = 30;

interface SetRetentionResp {
  setRetentionPolicy: MutationResult<AstroliftRetentionPolicy>;
}

function RetentionPolicySection({
  appSlug,
  policies,
}: {
  appSlug: string;
  policies: AstroliftRetentionPolicy[];
}) {
  const policyMap = React.useMemo(
    () => Object.fromEntries(policies.map((p) => [p.signal, p.retentionDays])),
    [policies],
  );
  const [saving, setSaving] = React.useState<Record<string, boolean>>({});

  const [setRetention] = useMutation<SetRetentionResp>(SET_RETENTION_POLICY, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug } }],
    awaitRefetchQueries: true,
  });

  async function handleChange(signal: string, raw: string) {
    const n = parseInt(raw, 10);
    if (isNaN(n) || n < 1) return;
    setSaving((s) => ({ ...s, [signal]: true }));
    try {
      const { data } = await setRetention({
        variables: { input: { appSlug, signal, retentionDays: n } },
      });
      const env = data?.setRetentionPolicy;
      if (!env) {
        toast.error("No response from server.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Failed to save retention policy.");
        return;
      }
      toast.success(
        `${RETENTION_SIGNALS.find((s) => s.signal === signal)?.label ?? signal} retention set to ${n} days.`,
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save retention policy.");
    } finally {
      setSaving((s) => ({ ...s, [signal]: false }));
    }
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <DatabaseIcon className="text-muted-foreground size-4 shrink-0" />
          Data retention
        </span>
      }
      description={
        <>
          How long observability data is kept per signal type. Defaults to{" "}
          {RETENTION_DEFAULT_DAYS} days when no policy is set.
        </>
      }
    >
      <div className="flex flex-col gap-2">
        {RETENTION_SIGNALS.map(({ signal, label }) => {
          const current = policyMap[signal] ?? RETENTION_DEFAULT_DAYS;
          const isDefault = policyMap[signal] === undefined;
          const isSaving = !!saving[signal];
          return (
            <div
              key={signal}
              className="bg-card flex items-center justify-between gap-3 rounded-md border p-3"
            >
              <div className="flex items-center gap-3">
                <Label className="w-28 shrink-0 text-sm font-medium">{label}</Label>
                {isDefault ? (
                  <Badge variant="secondary" className="text-2xs">
                    Platform default
                  </Badge>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                {isSaving ? (
                  <Loader2Icon className="text-muted-foreground size-3.5 animate-spin" />
                ) : null}
                <Can permission="app.update">
                  <Select
                    value={String(current)}
                    onValueChange={(v) => void handleChange(signal, v)}
                    disabled={isSaving}
                  >
                    <SelectTrigger className="w-36">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {RETENTION_DAY_OPTIONS.map((d) => (
                        <SelectItem key={d} value={String(d)}>
                          {d} days
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Can>
              </div>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

// ─── environment settings ─────────────────────────────────────────────────────

interface EnvsResp2 {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface SetEnvSettingResp {
  setEnvironmentSetting: MutationResult<AstroliftEnvironmentSetting>;
}

interface ClearEnvSettingResp {
  clearEnvironmentSetting: MutationResult<AstroliftEnvironmentSetting>;
}

const SUGGESTED_OVERRIDE_KEYS: { key: string; hint: string }[] = [
  { key: "replicas", hint: "Workload replica count" },
  { key: "cpu_request", hint: "Per-pod CPU request" },
  { key: "cpu_limit", hint: "Per-pod CPU limit" },
  { key: "memory_request", hint: "Per-pod memory request" },
  { key: "memory_limit", hint: "Per-pod memory limit" },
];

function EnvironmentSettingsSection({ appSlug }: { appSlug: string }) {
  const { data } = useQuery<EnvsResp2>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  const [selectedEnvId, setSelectedEnvId] = React.useState<string>("");
  const selectedEnv = envs.find((e) => e.id === selectedEnvId) ?? envs[0] ?? null;

  const [newKey, setNewKey] = React.useState("");
  const [newValue, setNewValue] = React.useState("");
  const [adding, setAdding] = React.useState(false);

  const refetch = [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }];
  const [setSetting] = useMutation<SetEnvSettingResp>(SET_ENVIRONMENT_SETTING, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [clearSetting] = useMutation<ClearEnvSettingResp>(CLEAR_ENVIRONMENT_SETTING, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  if (envs.length === 0) return null;

  const effectiveEnv = envs.find((e) => e.id === (selectedEnvId || envs[0]?.id));
  const settings = effectiveEnv?.settings ?? [];

  async function handleAdd() {
    if (!effectiveEnv) return;
    const key = newKey.trim();
    if (!key) { toast.error("Key is required."); return; }
    setAdding(true);
    try {
      const { data: resp } = await setSetting({
        variables: { input: { environmentId: effectiveEnv.id, key, value: newValue } },
      });
      const env = resp?.setEnvironmentSetting;
      if (!env?.ok) { toast.error(env?.errors?.[0]?.message ?? "Failed to save."); return; }
      toast.success(`Set ${key}`);
      setNewKey("");
      setNewValue("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setAdding(false);
    }
  }

  async function handleClear(key: string) {
    if (!effectiveEnv) return;
    try {
      const { data: resp } = await clearSetting({
        variables: { input: { environmentId: effectiveEnv.id, key } },
      });
      const env = resp?.clearEnvironmentSetting;
      if (!env?.ok) { toast.error(env?.errors?.[0]?.message ?? "Failed to clear."); return; }
      toast.success(`Cleared ${key}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to clear.");
    }
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <KeyIcon className="text-muted-foreground size-4 shrink-0" />
          Environment overrides
        </span>
      }
      description="Per-environment key/value settings that supplement the app's base configuration."
    >
      {envs.length > 1 && (
        <div className="flex items-center gap-2">
          <Label className="text-muted-foreground text-xs">Environment</Label>
          <Select value={selectedEnvId || envs[0]?.id} onValueChange={setSelectedEnvId}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select environment" />
            </SelectTrigger>
            <SelectContent>
              {envs.map((e) => (
                <SelectItem key={e.id} value={e.id}>
                  {e.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {settings.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">
          No overrides set for{" "}
          <span className="text-foreground font-mono">{effectiveEnv?.name ?? ""}</span>. Add one
          below or pick a suggested key.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {settings.map((s) => (
            <div key={s.id} className="flex items-center gap-2 rounded px-2 py-1 hover:bg-muted/40">
              <span className="w-40 shrink-0 font-mono text-xs">{s.key}</span>
              <span className="text-muted-foreground flex-1 truncate font-mono text-xs">{s.value || "(empty)"}</span>
              <Can permission="app.update">
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-destructive hover:text-destructive h-6 px-2 text-xs"
                  onClick={() => handleClear(s.key)}
                  title="Clear override"
                >
                  <Trash2Icon className="size-3" />
                </Button>
              </Can>
            </div>
          ))}
        </div>
      )}

      <Can permission="app.update">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground mr-1 text-2xs">Suggested:</span>
            {SUGGESTED_OVERRIDE_KEYS.map((s) => {
              const alreadySet = settings.some((row) => row.key === s.key);
              return (
                <Tooltip key={s.key}>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-6 px-2 font-mono text-2xs"
                      disabled={alreadySet}
                      onClick={() => setNewKey(s.key)}
                    >
                      {s.key}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs">{alreadySet ? `${s.hint} (already set)` : s.hint}</p>
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
          <div className="flex items-center gap-2">
            <Input
              list="env-override-suggested-keys"
              placeholder="KEY"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              className="w-44 font-mono text-xs"
            />
            <datalist id="env-override-suggested-keys">
              {SUGGESTED_OVERRIDE_KEYS.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.hint}
                </option>
              ))}
            </datalist>
            <Input
              placeholder="value"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              className="flex-1 font-mono text-xs"
            />
            <Button size="sm" variant="outline" disabled={adding} onClick={handleAdd}>
              {adding ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <PlusIcon className="size-3.5" />
              )}
              Add override
            </Button>
          </div>
        </div>
      </Can>
    </Section>
  );
}

// ─── archive / restore ───────────────────────────────────────────────────────

interface ArchiveAppResp {
  archiveApp: MutationResult<{ id: string; slug: string; isArchived: boolean; archivedAt: string | null }>;
}
interface RestoreAppResp {
  restoreApp: MutationResult<{ id: string; slug: string; isArchived: boolean; archivedAt: string | null }>;
}

function ArchiveSection({
  appSlug,
  appName,
  isArchived,
  archivedAt,
}: {
  appSlug: string;
  appName: string;
  isArchived: boolean;
  archivedAt: string | null;
}) {
  const fmt = useFormatters();
  const router = useRouter();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const refetch = [{ query: GET_APP, variables: { slug: appSlug } }];
  const [archive, { loading: archiving }] = useMutation<ArchiveAppResp>(ARCHIVE_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [restore, { loading: restoring }] = useMutation<RestoreAppResp>(RESTORE_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  async function handleArchive() {
    try {
      const { data } = await archive({ variables: { input: { appSlug } } });
      const env = data?.archiveApp;
      if (!env?.ok) { toast.error(env?.errors?.[0]?.message ?? "Archive failed."); return; }
      toast.success("App archived — workloads scaled to zero.");
      setConfirmOpen(false);
      router.push("/apps");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Archive failed.");
    }
  }

  async function handleRestore() {
    try {
      const { data } = await restore({ variables: { input: { appSlug } } });
      const env = data?.restoreApp;
      if (!env?.ok) { toast.error(env?.errors?.[0]?.message ?? "Restore failed."); return; }
      toast.success("App restored — workloads returning to pre-archive replicas.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Restore failed.");
    }
  }

  return (
    <Section
      title="App archive"
      description={
        isArchived
          ? archivedAt
            ? `Archived ${fmt.formatRelativeTime(archivedAt)}. Workloads are at zero replicas; deploys are suppressed.`
            : "App is archived. Workloads are at zero replicas."
          : "Archiving scales all workloads to zero and suppresses deploys. Restore returns replicas to their pre-archive counts."
      }
      action={
        <>
          {isArchived ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              Archived
            </Badge>
          ) : null}
          <Can permission="app.update">
            {isArchived ? (
              <Button size="sm" variant="default" disabled={restoring} onClick={handleRestore}>
                {restoring ? <Loader2Icon className="size-3.5 animate-spin" /> : <PlayCircleIcon className="size-3.5" />}
                Restore
              </Button>
            ) : (
              <Button size="sm" variant="outline" disabled={archiving} onClick={() => setConfirmOpen(true)}>
                {archiving ? <Loader2Icon className="size-3.5 animate-spin" /> : <ArchiveIcon className="size-3.5" />}
                Archive
              </Button>
            )}
          </Can>
        </>
      }
    >
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Archive {appName}?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="text-muted-foreground space-y-2 text-sm">
                <p>
                  Archive is a reversible alternative to deregister. It scales every workload
                  to <span className="text-foreground font-mono">replicas=0</span>, suppresses
                  webhook + scheduled deploys, and releases the load balancer capacity.
                </p>
                <p>
                  Your manifest, secrets, deploy tokens, managed services, and domain bindings
                  are preserved. Restoring returns each workload to its pre-archive replica
                  count.
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={archiving}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={archiving}
              onClick={(e) => {
                e.preventDefault();
                void handleArchive();
              }}
            >
              {archiving ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <ArchiveIcon className="size-4" />
              )}
              Archive {appName}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}

// ─── managed services inline edit + reprovision ───────────────────────────────

interface ManagedServicesListResp {
  astroliftManagedServices: AstroliftManagedService[];
}

interface ReprovisionResp {
  reprovisionManagedService: MutationResult<AstroliftManagedService>;
}

interface UpdateManagedServiceResp {
  updateManagedService: MutationResult<AstroliftManagedService>;
}

const SERVICE_STATUS_VARIANT: Record<
  string,
  "ok" | "warn" | "error" | "pending" | "muted"
> = {
  active: "ok",
  pending: "warn",
  provisioning: "pending",
  updating: "pending",
  deprovisioning: "pending",
  failed: "error",
  deleted: "muted",
};

function ManagedServicesAdminSection({ appSlug }: { appSlug: string }) {
  const { data, loading } = useQuery<ManagedServicesListResp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug, environmentName: null },
    fetchPolicy: "cache-and-network",
  });
  const services = (data?.astroliftManagedServices ?? []).filter(
    (s) => s.status !== "deleted",
  );

  const [reprovisionTarget, setReprovisionTarget] = React.useState<AstroliftManagedService | null>(
    null,
  );
  const [editTarget, setEditTarget] = React.useState<AstroliftManagedService | null>(null);

  if (loading && services.length === 0) {
    return null;
  }
  if (services.length === 0) {
    return null;
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <PlugIcon className="text-muted-foreground size-4 shrink-0" />
          Managed service admin
        </span>
      }
      description="Edit hot-swappable fields in place; trigger a full re-provision when a config change requires tearing down and recreating the backing cloud resource."
    >
      <Card className="overflow-hidden p-0">
        <ul className="divide-y">
          {services.map((svc) => (
            <ManagedServiceAdminRow
              key={svc.id}
              svc={svc}
              appSlug={appSlug}
              onReprovision={() => setReprovisionTarget(svc)}
              onEdit={() => setEditTarget(svc)}
            />
          ))}
        </ul>
      </Card>

      <ReprovisionConfirmDialog
        appSlug={appSlug}
        target={reprovisionTarget}
        onOpenChange={(open) => {
          if (!open) setReprovisionTarget(null);
        }}
      />
      <ManagedServiceEditSheet
        appSlug={appSlug}
        target={editTarget}
        onOpenChange={(open) => {
          if (!open) setEditTarget(null);
        }}
      />
    </Section>
  );
}

function ManagedServiceAdminRow({
  svc,
  onReprovision,
  onEdit,
}: {
  svc: AstroliftManagedService;
  appSlug: string;
  onReprovision: () => void;
  onEdit: () => void;
}) {
  const dotStatus = SERVICE_STATUS_VARIANT[svc.status] ?? "muted";
  const editableCount = svc.editableFields?.length ?? 0;
  const inFlight = ["provisioning", "updating", "deprovisioning", "pending"].includes(svc.status);

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <StatusDot status={dotStatus} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-1.5">
          <span className="text-foreground font-mono text-sm">{svc.name || svc.kind}</span>
          <Badge variant="secondary" className="text-2xs">
            {svc.kind}
            {svc.variant ? `/${svc.variant}` : ""}
          </Badge>
          <Badge variant="outline" className="font-mono text-2xs">
            {svc.environmentName}
          </Badge>
          <Badge variant="outline" className="text-2xs capitalize">
            {svc.status}
          </Badge>
        </div>
        {svc.statusError ? (
          <p className="text-destructive mt-0.5 max-w-md truncate text-2xs">
            {svc.statusError}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Can permission="app.deploy">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={onEdit}
                disabled={editableCount === 0 || inFlight}
              >
                <PencilIcon className="size-3.5" />
                Edit
                {editableCount > 0 ? (
                  <Badge variant="secondary" className="ml-1 text-2xs">
                    {editableCount}
                  </Badge>
                ) : null}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p className="text-xs">
                {editableCount === 0
                  ? "No fields are editable in place for this kind. Use Re-provision to apply a config change that requires a teardown."
                  : `Edit hot-swappable fields without tearing down the backing resource.`}
              </p>
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={onReprovision}
                disabled={inFlight}
              >
                <RefreshCwIcon className="size-3.5" />
                Re-provision
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p className="text-xs">
                Tear down and recreate the cloud resource. Use when a config change is not in
                the editable-fields set.
              </p>
            </TooltipContent>
          </Tooltip>
        </Can>
      </div>
    </li>
  );
}

function ReprovisionConfirmDialog({
  appSlug,
  target,
  onOpenChange,
}: {
  appSlug: string;
  target: AstroliftManagedService | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [reprovision, { loading }] = useMutation<ReprovisionResp>(REPROVISION_MANAGED_SERVICE, {
    refetchQueries: [
      { query: LIST_MANAGED_SERVICES, variables: { appSlug, environmentName: null } },
    ],
    awaitRefetchQueries: true,
  });

  async function handleConfirm() {
    if (!target) return;
    try {
      const { data } = await reprovision({
        variables: { input: { managedServiceId: target.id } },
      });
      const env = data?.reprovisionManagedService;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Re-provision failed.");
        return;
      }
      toast.success(`Re-provisioning ${target.name || target.kind}.`);
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Re-provision failed.");
    }
  }

  return (
    <AlertDialog open={target !== null} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Re-provision {target ? `${target.kind}/${target.name}` : "managed service"}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            The driver tears down the backing cloud resource and recreates it from the current
            config. The service transitions to <span className="font-mono">pending</span>; the
            lifecycle workflow picks it up. Persistent data on this kind may or may not survive
            the teardown — check the kind&apos;s driver docs before confirming.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={loading}
            onClick={(e) => {
              e.preventDefault();
              void handleConfirm();
            }}
          >
            {loading ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-4" />
            )}
            Re-provision
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ManagedServiceEditSheet({
  appSlug,
  target,
  onOpenChange,
}: {
  appSlug: string;
  target: AstroliftManagedService | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [update, { loading }] = useMutation<UpdateManagedServiceResp>(UPDATE_MANAGED_SERVICE, {
    refetchQueries: [
      { query: LIST_MANAGED_SERVICES, variables: { appSlug, environmentName: null } },
    ],
    awaitRefetchQueries: true,
  });

  React.useEffect(() => {
    if (!target) {
      setValues({});
      return;
    }
    const editable = target.editableFields ?? [];
    const initial: Record<string, string> = {};
    for (const field of editable) {
      const v = target.config?.[field];
      initial[field] = v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);
    }
    setValues(initial);
  }, [target]);

  async function handleSave() {
    if (!target) return;
    const editable = target.editableFields ?? [];
    const changes: Record<string, unknown> = {};
    for (const field of editable) {
      const next = values[field] ?? "";
      const original = target.config?.[field];
      const originalStr =
        original == null ? "" : typeof original === "string" ? original : JSON.stringify(original);
      if (next === originalStr) continue;
      // Preserve the original scalar type when round-tripping (number/bool stay
      // typed; strings + JSON-shaped values pass through as the raw input).
      if (typeof original === "number" && next.trim() !== "" && !isNaN(Number(next))) {
        changes[field] = Number(next);
      } else if (typeof original === "boolean") {
        changes[field] = next === "true" || next === "1";
      } else {
        changes[field] = next;
      }
    }
    if (Object.keys(changes).length === 0) {
      toast.message("No changes to save.");
      return;
    }
    try {
      const { data } = await update({
        variables: { input: { id: target.id, config: changes } },
      });
      const env = data?.updateManagedService;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Update failed.");
        return;
      }
      toast.success(`Updated ${target.name || target.kind}.`);
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed.");
    }
  }

  const editable = target?.editableFields ?? [];

  return (
    <Sheet open={target !== null} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <PencilIcon className="size-4" />
            {target ? `Edit ${target.kind}/${target.name}` : "Edit managed service"}
          </SheetTitle>
          <SheetDescription>
            Only hot-swappable fields are shown. Changes apply via the driver&apos;s update
            path — no teardown. For other changes, use Re-provision.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {target && editable.length === 0 ? (
            <div className="bg-muted/40 rounded-md border p-3 text-xs">
              <p className="text-muted-foreground">
                This kind exposes no editable fields. Edit via the manifest TOML editor and
                re-provision, or change here once the driver adds in-place support.
              </p>
            </div>
          ) : null}
          {editable.map((field) => (
            <div key={field} className="grid gap-1.5">
              <Label htmlFor={`msvc-edit-${field}`} className="font-mono text-xs">
                {field}
              </Label>
              <Input
                id={`msvc-edit-${field}`}
                value={values[field] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                className="font-mono text-xs"
              />
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Can permission="app.deploy">
            <Button
              onClick={() => void handleSave()}
              disabled={loading || editable.length === 0}
            >
              {loading ? <Loader2Icon className="size-4 animate-spin" /> : null}
              Save changes
            </Button>
          </Can>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
