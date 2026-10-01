"use client";

import { AppIdentityView } from "@/components/screens/apps/settings/AppIdentity";
import { AppSettingsScreen } from "@/components/screens/apps/settings/AppSettingsScreen";
import { AppSettingsTab } from "@/components/screens/apps/settings/AppSettingsTab";
import { ArchiveAppView } from "@/components/screens/apps/settings/ArchiveApp";
import { DangerZoneView } from "@/components/screens/apps/settings/DangerZone";
import { EnvironmentSettingsView } from "@/components/screens/apps/settings/EnvironmentSettings";
import { ForceRedeployView } from "@/components/screens/apps/settings/ForceRedeploy";
import { IngressControlsView } from "@/components/screens/apps/settings/IngressControls";
import { ManagedServicesAdminView } from "@/components/screens/apps/settings/ManagedServicesAdmin";
import { ResyncSourceView } from "@/components/screens/apps/settings/ResyncSource";
import { RetentionPolicyView } from "@/components/screens/apps/settings/RetentionPolicy";
import { RunScheduledJobView } from "@/components/screens/apps/settings/RunScheduledJob";
import { useAppIdentity } from "@/components/screens/apps/settings/use-app-identity";
import { useAppSettings } from "@/components/screens/apps/settings/use-app-settings";
import { useAppSettingsTab } from "@/components/screens/apps/settings/use-app-settings-tab";
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

import { EnvironmentsClient } from "@/app/(app)/environments/environments-client";
import { WebhooksClient } from "@/app/(app)/webhooks/webhooks-client";

import { ConfigEditorClient } from "../config/config-editor-client";
import { useAppChrome } from "@/lib/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { AssignProjectCard } from "../components/assign-project-card";
import { CiSetupSection } from "../components/ci-setup-section";
import { ControlsSection } from "../components/controls-section";
import { DeployStrategyCard } from "../components/deploy-strategy-card";
import { DeregisterPendingBanner } from "../components/deregister-pending-banner";
import { ManagedServicesSummaryCard } from "../components/managed-services-summary-card";
import { TeamsCard } from "../components/teams-card";
import { ManifestPreviewClient } from "../manifest/manifest-preview-client";

/**
 * The app's Settings tab (spec 44 §5.3): the section in `?section=`, the
 * viewer's permissions and the app from the hook, every part's own
 * container as a slot. Single-section mode mounts only the active section,
 * so only its hooks and queries run. Agents keep `SettingsClient` below
 * until their own migration.
 */
export function AppSettingsTabClient({ slug }: { slug: string }) {
  const { basePath } = useAppChrome();
  const { app: a, loading, section, access } = useAppSettingsTab(slug);
  return (
    <AppSettingsTab
      slug={slug}
      loading={loading}
      found={Boolean(a)}
      section={section}
      access={access}
      slots={
        a
          ? {
              deregisterPending: <DeregisterPendingBanner appSlug={a.slug} />,
              identity: <AppIdentityCard app={a} />,
              assignProject: (
                <AssignProjectCard
                  appSlug={a.slug}
                  currentProjectId={a.projectId ?? null}
                  currentProjectName={a.projectName}
                  currentTeamName={a.teamName}
                />
              ),
              teams: <TeamsCard appSlug={a.slug} appId={a.id} homeTeamSlug={a.teamSlug} />,
              deployStrategy: <DeployStrategyCard app={a} />,
              // CI setup is hidden when the app has no source repo (#382).
              ciSetup: a.sourceRepo ? (
                <CiSetupSection
                  appId={a.id}
                  appSlug={a.slug}
                  registryUri={a.ecrRepoUri}
                  pushCredentialRef={a.ecrPushRoleArn}
                  providerPluginSlug={a.providerPluginSlug}
                  deployBranch={a.deployBranch}
                  sourceWebhookInstalledAt={a.sourceWebhookInstalledAt ?? null}
                  ciWorkflowSyncStatus={a.ciWorkflowSyncStatus ?? null}
                  agentMode={false}
                />
              ) : undefined,
              resync: <ResyncSourceCard app={a} agentMode={false} />,
              controls: <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />,
              webhookDeploys: <WebhookDeploysPauseCard app={a} />,
              forceRedeploy: <ForceRedeployCard appSlug={a.slug} />,
              runJob: <RunScheduledJobCard appSlug={a.slug} basePath={basePath} />,
              ingress: <IngressControlsCard appSlug={a.slug} />,
              retention: <RetentionPolicyCard app={a} />,
              managedServicesSummary: <ManagedServicesSummaryCard appSlug={a.slug} />,
              managedServicesAdmin: <ManagedServicesAdminCard appSlug={a.slug} />,
              configuration: <ConfigEditorClient slug={a.slug} />,
              manifest: <ManifestPreviewClient slug={a.slug} />,
              webhooks: <WebhooksClient appSlug={a.slug} />,
              environments: <EnvironmentsClient appSlug={a.slug} />,
              environmentSettings: <EnvironmentSettingsCard appSlug={a.slug} />,
              archive: <ArchiveAppCard app={a} />,
              deregister: <DangerZoneCard appSlug={a.slug} appName={a.name} />,
            }
          : {}
      }
    />
  );
}

function AppIdentityCard({ app }: { app: AstroliftRegisteredApp }) {
  return <AppIdentityView {...useAppIdentity(app)} />;
}

/**
 * The agent settings landing (and the app one before spec 44). The screen owns the markup; each section with data
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
                  deployBranch={a.deployBranch}
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

export function ResyncSourceCard({
  app,
  agentMode,
}: {
  app: AstroliftRegisteredApp;
  agentMode: boolean;
}) {
  return (
    <ResyncSourceView
      {...useResyncSource(app.slug, app, agentMode)}
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

export function ManagedServicesAdminCard({ appSlug }: { appSlug: string }) {
  return <ManagedServicesAdminView {...useManagedServicesAdmin(appSlug)} />;
}

export function RetentionPolicyCard({ app }: { app: AstroliftRegisteredApp }) {
  return (
    <RetentionPolicyView
      {...useRetentionPolicy(app.slug, app)}
      appId={app.id}
      sourceVersion={app.version}
      policies={app.retentionPolicies ?? []}
    />
  );
}

export function EnvironmentSettingsCard({ appSlug }: { appSlug: string }) {
  return <EnvironmentSettingsView {...useEnvironmentSettings(appSlug)} />;
}

export function ArchiveAppCard({ app }: { app: AstroliftRegisteredApp }) {
  return (
    <ArchiveAppView
      {...useArchiveApp(app.slug, app)}
      appId={app.id}
      sourceVersion={app.version}
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

export function DangerZoneCard({ appSlug, appName }: { appSlug: string; appName: string }) {
  return <DangerZoneView {...useDangerZone(appSlug, appName)} />;
}
