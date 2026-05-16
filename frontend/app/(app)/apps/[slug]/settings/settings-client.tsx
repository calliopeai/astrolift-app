"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  ChevronRightIcon,
  FileCodeIcon,
  GlobeIcon,
  KeyIcon,
  LineChartIcon,
  Loader2Icon,
  LockIcon,
  PauseIcon,
  PlayIcon,
  PlugIcon,
  SettingsIcon,
  Trash2Icon,
  UsersIcon,
  WebhookIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PAUSE_APP_INGRESS, RESUME_APP_INGRESS } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";
import { ControlsSection } from "../components/controls-section";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface LinkSection {
  key: string;
  title: string;
  description: string;
  href: (slug: string) => string;
  icon: typeof KeyIcon;
}

/** Ordered list of link cards that compose the settings landing. Inline
 *  controls (deploys, ingress) live above; danger zone lives below. */
const LINK_SECTIONS: LinkSection[] = [
  {
    key: "deploy-strategy",
    title: "Deploy strategy",
    description:
      "Edit the manifest TOML — image build, environments, triggers, approvals, and rollout strategy.",
    href: (s) => `/apps/${s}/config`,
    icon: FileCodeIcon,
  },
  {
    key: "deploy-tokens",
    title: "Deploy tokens",
    description:
      "Long-lived bearer tokens that let external CI hand off deployments without an interactive session.",
    href: (s) => `/apps/${s}/tokens`,
    icon: KeyIcon,
  },
  {
    key: "secrets",
    title: "Secrets + env",
    description:
      "Per-environment encrypted secrets and plaintext env vars mounted into workloads at deploy time.",
    href: (s) => `/apps/${s}/secrets`,
    icon: LockIcon,
  },
  {
    key: "managed-services",
    title: "Managed services",
    description:
      "Provisioned dependencies — Postgres, Redis, object storage — attached and routed via Astrolift.",
    href: (s) => `/apps/${s}/managed-services`,
    icon: PlugIcon,
  },
  {
    key: "domains",
    title: "Custom domains",
    description:
      "Map your own hostnames to environments. Validation, ACME-issued certs, and BYO certificate upload.",
    href: (s) => `/apps/${s}/domains`,
    icon: GlobeIcon,
  },
  {
    key: "webhooks",
    title: "Webhooks",
    description:
      "Outbound deployment, build, and lifecycle events delivered to your endpoints with HMAC signatures.",
    href: (s) => `/apps/${s}/webhooks`,
    icon: WebhookIcon,
  },
  {
    key: "members",
    title: "Team members",
    description:
      "App-scoped role bindings layered on top of org and team grants. Manage who can deploy, rotate, or destroy.",
    href: (s) => `/apps/${s}/members`,
    icon: UsersIcon,
  },
  {
    key: "observability",
    title: "Observability",
    description:
      "Logs, metrics, traces, and SLO posture for this app. Prometheus + Loki + Tempo wired by default.",
    href: (s) => `/apps/${s}/observability`,
    icon: LineChartIcon,
  },
];

export function SettingsClient({ slug }: { slug: string }) {
  const app = useQuery<AppResp>(GET_APP, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });

  if (app.loading && !app.data) {
    return (
      <PageShell title="Settings" description="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  const a = app.data?.astroliftApp;
  if (!a) {
    return (
      <PageShell title="App not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <SettingsIcon className="text-muted-foreground size-5" />
          <span>Settings</span>
        </span>
      }
      description={
        <span>
          Operational controls and configuration for{" "}
          <span className="text-foreground font-mono text-xs">{a.slug}</span>.
        </span>
      }
    >
      <AppTabs slug={a.slug} active="settings" />

      <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />

      <IngressControlsSection appSlug={a.slug} />

      <div className="flex flex-col gap-3">
        {LINK_SECTIONS.map((s) => (
          <SettingsLinkCard key={s.key} section={s} slug={a.slug} />
        ))}
      </div>

      <DangerZoneCard />
    </PageShell>
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
  const { data, loading } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-4">
        <h2 className="text-base font-semibold">Ingress</h2>
        <p className="text-muted-foreground mt-0.5 text-xs">
          Pause edge routing without stopping the workloads behind it.
        </p>
      </div>

      {loading && envs.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : envs.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">
          No environments yet — sync the manifest to populate this section.
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
            Paused
          </Badge>
        ) : (
          <Badge
            variant="outline"
            className="border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          >
            <PlayIcon className="size-3" />
            Live
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
          {paused ? "Resume" : "Pause"}
        </Button>
      </Can>
    </div>
  );
}

// ─── link card ────────────────────────────────────────────────────────────────

function SettingsLinkCard({ section, slug }: { section: LinkSection; slug: string }) {
  const Icon = section.icon;
  return (
    <Link href={section.href(slug)} className="group block">
      <Card className="hover:bg-accent/40 transition-colors">
        <CardContent className="flex items-center gap-4 p-5">
          <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{section.title}</p>
            <p className="text-muted-foreground mt-0.5 text-xs">{section.description}</p>
          </div>
          <ChevronRightIcon className="text-muted-foreground group-hover:text-foreground size-5 shrink-0 transition-colors" />
        </CardContent>
      </Card>
    </Link>
  );
}

// ─── danger zone ──────────────────────────────────────────────────────────────

/**
 * Placeholder for the hard-deregister + full teardown flow (tracked in
 * #392). Rendered as a disabled card so the surface is visible but
 * inert until the workflow ships.
 */
function DangerZoneCard() {
  return (
    <Card className="border-destructive/40">
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="bg-destructive/10 text-destructive shrink-0 rounded-md p-2.5">
          <Trash2Icon className="size-5" />
        </div>
        <div className="flex-1">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            Danger zone
            <Badge variant="outline" className="text-[10px] tracking-wide uppercase">
              Coming soon
            </Badge>
          </CardTitle>
          <CardDescription className="mt-1">
            Hard deregister this app and tear down every attached resource — workloads,
            environments, secrets, domains, and managed services. Irreversible. Tracked in #392.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <Can permission="app.delete">
          <Button variant="destructive" disabled>
            <Trash2Icon className="size-4" />
            Hard deregister + teardown
          </Button>
        </Can>
        <p className="text-muted-foreground mt-2 text-[11px]">
          For now, use <span className="font-mono">Delete</span> on the overview tab for a soft
          delete (recoverable for 30 days).
        </p>
      </CardContent>
    </Card>
  );
}
