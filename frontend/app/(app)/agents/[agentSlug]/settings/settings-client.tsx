"use client";

import { AssignProjectCard } from "@/app/(app)/apps/[slug]/components/assign-project-card";
import { CiSetupSection } from "@/app/(app)/apps/[slug]/components/ci-setup-section";
import { DeregisterPendingBanner } from "@/app/(app)/apps/[slug]/components/deregister-pending-banner";
import { TeamsCard } from "@/app/(app)/apps/[slug]/components/teams-card";
import { AppDomainsClient } from "@/app/(app)/apps/[slug]/domains/domains-client";
import { ManagedServicesClient } from "@/app/(app)/apps/[slug]/managed-services/managed-services-client";
import {
  ArchiveAppCard,
  DangerZoneCard,
  ResyncSourceCard,
} from "@/app/(app)/apps/[slug]/settings/settings-client";
import { EnvironmentsClient } from "@/app/(app)/environments/environments-client";
import { AppSettingsTab } from "@/components/screens/apps/settings/AppSettingsTab";
import { useAppSettingsTab } from "@/components/screens/apps/settings/use-app-settings-tab";

/**
 * The agent's Settings tab (spec 44 §5.2, §5.3) on the settings archetype,
 * single-section mode: General (project, teams, source), Environments,
 * Domains, Managed services and one Danger zone, by `?section=`. An agent IS
 * a RegisteredApp, so each part is the app's own container, and only the
 * active section mounts, so only its queries run. The app-only parts
 * (deploy strategy, ingress, retention, scheduled jobs) stay out, as they
 * did on the agent's settings page.
 */
export function AgentSettingsTabClient({ slug }: { slug: string }) {
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
              assignProject: (
                <AssignProjectCard
                  appSlug={a.slug}
                  currentProjectId={a.projectId ?? null}
                  currentProjectName={a.projectName}
                  currentTeamName={a.teamName}
                />
              ),
              teams: <TeamsCard appSlug={a.slug} appId={a.id} homeTeamSlug={a.teamSlug} />,
              ciSetup: a.sourceRepo ? (
                <CiSetupSection
                  appId={a.id}
                  appSlug={a.slug}
                  registryUri={a.ecrRepoUri}
                  pushCredentialRef={a.ecrPushRoleArn}
                  providerPluginSlug={a.providerPluginSlug}
                  sourceWebhookInstalledAt={a.sourceWebhookInstalledAt ?? null}
                  ciWorkflowSyncStatus={a.ciWorkflowSyncStatus ?? null}
                  agentMode
                />
              ) : undefined,
              resync: <ResyncSourceCard app={a} agentMode />,
              environments: <EnvironmentsClient appSlug={a.slug} />,
              domains: <AppDomainsClient slug={a.slug} />,
              managedServices: <ManagedServicesClient slug={a.slug} />,
              archive: <ArchiveAppCard app={a} />,
              deregister: <DangerZoneCard appSlug={a.slug} appName={a.name} />,
            }
          : {}
      }
    />
  );
}
