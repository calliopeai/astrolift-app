import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";

import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import {
  APP,
  ARCHIVE,
  DANGER_ZONE,
  ENV_SETTINGS,
  FORCE_REDEPLOY,
  INGRESS,
  LONG,
  MANAGED_SERVICES_ADMIN,
  RESYNC,
  RETENTION,
  RUN_JOB,
  SETTINGS,
  WEBHOOK_DEPLOYS,
} from "./app-settings-members.fixtures";
import { AppSettingsScreen } from "./AppSettingsScreen";
import { ArchiveAppView } from "./ArchiveApp";
import { DangerZoneView } from "./DangerZone";
import { EnvironmentSettingsView } from "./EnvironmentSettings";
import { ForceRedeployView } from "./ForceRedeploy";
import { IngressControlsView } from "./IngressControls";
import { ManagedServicesAdminView } from "./ManagedServicesAdmin";
import { ResyncSourceView } from "./ResyncSource";
import { RetentionPolicyView } from "./RetentionPolicy";
import { RunScheduledJobView } from "./RunScheduledJob";
import { WebhookDeploysPauseView } from "./WebhookDeploysPause";

/**
 * The sections owned by other screens (controls, assign project, teams,
 * managed-services summary, deploy strategy, CI setup, deregister banner)
 * are slots the route fills; they are left out here and have stories of
 * their own.
 */
const meta: Meta = {
  title: "Screens/Apps/Settings/AppSettingsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const tabs = <AppTabsView slug="checkout" basePath="/apps" pathname="/apps/checkout/settings" />;

const sections = {
  resync: <ResyncSourceView {...RESYNC} />,
  webhookDeploys: <WebhookDeploysPauseView {...WEBHOOK_DEPLOYS} />,
  ingress: <IngressControlsView {...INGRESS} />,
  managedServicesAdmin: <ManagedServicesAdminView {...MANAGED_SERVICES_ADMIN} />,
  retention: <RetentionPolicyView {...RETENTION} />,
  environmentSettings: <EnvironmentSettingsView {...ENV_SETTINGS} />,
  archive: <ArchiveAppView {...ARCHIVE} />,
  forceRedeploy: <ForceRedeployView {...FORCE_REDEPLOY} />,
  runJob: <RunScheduledJobView {...RUN_JOB} />,
  dangerZone: <DangerZoneView {...DANGER_ZONE} />,
};

export const Full: Story = {
  render: () => <AppSettingsScreen {...SETTINGS} tabs={tabs} sections={sections} />,
};

/** Compact density: the link cards merge into one panel of rows. */
export const Compact: Story = {
  render: () => <AppSettingsScreen {...SETTINGS} tabs={tabs} sections={sections} compactLinks />,
};

/** An agent's settings: app-only sections and link cards are left out. */
export const Agent: Story = {
  render: () => (
    <AppSettingsScreen
      {...SETTINGS}
      basePath="/agents"
      isAgent
      sections={{
        resync: <ResyncSourceView {...RESYNC} agentMode />,
        archive: sections.archive,
        dangerZone: sections.dangerZone,
      }}
    />
  ),
};

export const Loading: Story = {
  render: () => <AppSettingsScreen {...SETTINGS} app={null} loading />,
};

/**
 * The landing has no empty state of its own; the closest is an app with no
 * repo and nothing modified yet: no CI block, no "Modified" captions.
 */
export const Empty: Story = {
  render: () => (
    <AppSettingsScreen
      {...SETTINGS}
      app={{ ...APP, sourceRepo: "", settingsLastModified: null }}
      tabs={tabs}
      sections={{ dangerZone: sections.dangerZone }}
    />
  ),
};

/** No app with this slug, or no permission to see it (the error state). */
export const NotFound: Story = {
  render: () => <AppSettingsScreen {...SETTINGS} slug="no-such-app" app={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <AppSettingsScreen
      {...SETTINGS}
      slug={LONG}
      app={{ ...APP, slug: LONG, name: LONG }}
      sections={{ dangerZone: <DangerZoneView {...DANGER_ZONE} appName={LONG} /> }}
    />
  ),
};

export const FrenchLinksWidth768: Story = {
  render: () => (
    <NextIntlClientProvider
      locale="fr"
      messages={fr}
      timeZone="UTC"
      now={new Date("2026-09-30T12:00:00Z")}
    >
      <div style={{ width: 768 }}>
        <AppSettingsScreen {...SETTINGS} compactLinks />
      </div>
    </NextIntlClientProvider>
  ),
};
