"use client";

import { AppSettingsScreen } from "@/components/screens/apps/settings/AppSettingsScreen";
import { ArchiveAppView } from "@/components/screens/apps/settings/ArchiveApp";
import { DangerZoneView } from "@/components/screens/apps/settings/DangerZone";
import { EnvironmentSettingsView } from "@/components/screens/apps/settings/EnvironmentSettings";
import { ForceRedeployView } from "@/components/screens/apps/settings/ForceRedeploy";
import { IngressControlsView } from "@/components/screens/apps/settings/IngressControls";
import { ManagedServicesAdminView } from "@/components/screens/apps/settings/ManagedServicesAdmin";
import { ResyncSourceView } from "@/components/screens/apps/settings/ResyncSource";
import { RetentionPolicyView } from "@/components/screens/apps/settings/RetentionPolicy";
import { RunScheduledJobView } from "@/components/screens/apps/settings/RunScheduledJob";
import { useAppSettings } from "@/components/screens/apps/settings/use-app-settings";
import { useArchiveApp } from "@/components/screens/apps/settings/use-archive-app";
import { useDangerZone } from "@/components/screens/apps/settings/use-danger-zone";
import { useEnvironmentSettings } from "@/components/screens/apps/settings/use-environment-settings";
import { useForceRedeploy } from "@/components/screens/apps/settings/use-force-redeploy";
import { useIngressControls } from "@/components/screens/apps/settings/use-ingress-controls";
import { useManagedServicesAdmin } from "@/components/screens/apps/settings/use-managed-services-admin";
import { useResyncSource } from "@/components/screens/apps/settings/use-resync-source";
import { useRetentionPolicy } from "@/components/screens/apps/settings/use-retention-policy";
import { useRunScheduledJob } from "@/components/screens/apps/settings/use-run-scheduled-job";
import { useWebhookDeploysPause } from "@/components/screens/apps/settings/use-webhook-deploys-pause";
import { WebhookDeploysPauseView } from "@/components/screens/apps/settings/WebhookDeploysPause";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { useAppearance } from "@/providers/AppearanceProvider";

import { useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { AssignProjectCard } from "../components/assign-project-card";
import { CiSetupSection } from "../components/ci-setup-section";
import { ControlsSection } from "../components/controls-section";
import { DeployStrategyCard } from "../components/deploy-strategy-card";
import { DeregisterPendingBanner } from "../components/deregister-pending-banner";
import { ManagedServicesSummaryCard } from "../components/managed-services-summary-card";
import { TeamsCard } from "../components/teams-card";

/**
 * App settings landing. The screen owns the markup; each section with data
 * of its own gets a container here so its hook runs only when the section
 * is rendered (agents skip the app-only sections and their queries).
 */
export function SettingsClient({
  slug,
  resourceKind = "app",
}: {
  slug: string;
  resourceKind?: "app" | "agent";
}) {
  const isAgent = resourceKind === "agent";
  const { appearance } = useAppearance();
  const { basePath } = useAppChrome();
  const settings = useAppSettings(slug);
  const a = settings.app;

  return (
    <AppSettingsScreen
      {...settings}
      slug={slug}
      isAgent={isAgent}
      compactLinks={appearance.density === "compact"}
      basePath={basePath}
      tabs={a ? <AppTabs slug={a.slug} active="settings" /> : undefined}
      sections={
        a
          ? {
              deregisterPending: <DeregisterPendingBanner appSlug={a.slug} />,
              controls: isAgent ? undefined : (
                <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />
              ),
              resync: <ResyncSourceCard app={a} agentMode={isAgent} />,
              webhookDeploys: isAgent ? undefined : <WebhookDeploysPauseCard app={a} />,
              ingress: isAgent ? undefined : <IngressControlsCard appSlug={a.slug} />,
              assignProject: (
                <AssignProjectCard
                  appSlug={a.slug}
                  currentProjectId={a.projectId ?? null}
                  currentProjectName={a.projectName}
                  currentTeamName={a.teamName}
                />
              ),
              teams: <TeamsCard appSlug={a.slug} appId={a.id} homeTeamSlug={a.teamSlug} />,
              managedServicesSummary: isAgent ? undefined : (
                <ManagedServicesSummaryCard appSlug={a.slug} />
              ),
              managedServicesAdmin: isAgent ? undefined : (
                <ManagedServicesAdminCard appSlug={a.slug} />
              ),
              retention: isAgent ? undefined : <RetentionPolicyCard app={a} />,
              environmentSettings: isAgent ? undefined : (
                <EnvironmentSettingsCard appSlug={a.slug} />
              ),
              archive: <ArchiveAppCard app={a} />,
              deployStrategy: isAgent ? undefined : <DeployStrategyCard app={a} />,
              // CI setup is hidden when the app has no source repo (#382);
              // the screen places it under the manifest link.
              ciSetup: a.sourceRepo ? (
                <CiSetupSection
                  appId={a.id}
                  appSlug={a.slug}
                  registryUri={a.ecrRepoUri}
                  pushCredentialRef={a.ecrPushRoleArn}
                  providerPluginSlug={a.providerPluginSlug}
                  sourceWebhookInstalledAt={a.sourceWebhookInstalledAt ?? null}
                  ciWorkflowSyncStatus={a.ciWorkflowSyncStatus ?? null}
                  agentMode={isAgent}
                />
              ) : undefined,
              forceRedeploy: isAgent ? undefined : <ForceRedeployCard appSlug={a.slug} />,
              runJob: isAgent ? undefined : (
                <RunScheduledJobCard appSlug={a.slug} basePath={basePath} />
              ),
              dangerZone: <DangerZoneCard appSlug={a.slug} appName={a.name} />,
            }
          : undefined
      }
    />
  );
}

function ResyncSourceCard({ app, agentMode }: { app: AstroliftRegisteredApp; agentMode: boolean }) {
  return (
    <ResyncSourceView
      {...useResyncSource(app.slug)}
      lastResyncAt={app.lastResyncAt ?? null}
      agentMode={agentMode}
    />
  );
}

function WebhookDeploysPauseCard({ app }: { app: AstroliftRegisteredApp }) {
  return (
    <WebhookDeploysPauseView
      {...useWebhookDeploysPause(app.slug)}
      paused={app.webhookDeploysPaused}
      pausedAt={app.webhookDeploysPausedAt ?? null}
      pausedByEmail={app.webhookDeploysPausedByEmail ?? null}
      pauseReason={app.webhookDeploysPauseReason ?? ""}
    />
  );
}

function IngressControlsCard({ appSlug }: { appSlug: string }) {
  return <IngressControlsView {...useIngressControls(appSlug)} />;
}

function ManagedServicesAdminCard({ appSlug }: { appSlug: string }) {
  return <ManagedServicesAdminView {...useManagedServicesAdmin(appSlug)} />;
}

function RetentionPolicyCard({ app }: { app: AstroliftRegisteredApp }) {
  return (
    <RetentionPolicyView {...useRetentionPolicy(app.slug)} policies={app.retentionPolicies ?? []} />
  );
}

function EnvironmentSettingsCard({ appSlug }: { appSlug: string }) {
  return <EnvironmentSettingsView {...useEnvironmentSettings(appSlug)} />;
}

function ArchiveAppCard({ app }: { app: AstroliftRegisteredApp }) {
  return (
    <ArchiveAppView
      {...useArchiveApp(app.slug)}
      appName={app.name}
      isArchived={app.isArchived}
      archivedAt={app.archivedAt ?? null}
    />
  );
}

function ForceRedeployCard({ appSlug }: { appSlug: string }) {
  return <ForceRedeployView {...useForceRedeploy(appSlug)} />;
}

function RunScheduledJobCard({ appSlug, basePath }: { appSlug: string; basePath: string }) {
  return <RunScheduledJobView {...useRunScheduledJob(appSlug)} basePath={basePath} />;
}

function DangerZoneCard({ appSlug, appName }: { appSlug: string; appName: string }) {
  return <DangerZoneView {...useDangerZone(appSlug, appName)} />;
}
