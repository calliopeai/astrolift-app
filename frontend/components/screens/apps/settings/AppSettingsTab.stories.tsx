import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type * as React from "react";
import { expect, within } from "storybook/test";

import { AppChromeProvider } from "@/lib/app-chrome-context";
import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

import { EDITOR, LONG_APP, RENDER_FAILED } from "../config/app-config-manifest.fixtures";
import { PREVIEW_PROPS } from "../config/app-config-agent.fixtures";
import { ConfigEditorScreen } from "../config/ConfigEditorScreen";
import { ManifestPreviewScreen } from "../config/ManifestPreviewScreen";

import {
  ARCHIVE,
  DANGER_ZONE,
  DEREGISTER_PREVIEW,
  ENV_SETTINGS,
  FORCE_REDEPLOY,
  IDENTITY,
  IDENTITY_LONG,
  INGRESS,
  LONG,
  MANAGED_SERVICES_ADMIN,
  RESYNC,
  RETENTION,
  RUN_JOB,
  WEBHOOK_DEPLOYS,
  WEBHOOK_DEPLOYS_PAUSED,
} from "./app-settings-members.fixtures";
import { AppIdentityView } from "./AppIdentity";
import {
  type AppSettingsTabAccess,
  AppSettingsTab,
  type AppSettingsTabSlots,
} from "./AppSettingsTab";
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
 * The app's Settings tab inside the app frame. The parts owned by the
 * route's containers only (controls, assign project, teams, deploy
 * strategy, CI setup, the managed-services summary, webhooks and the
 * environments list) have stories of their own and are left out here.
 */
const meta: Meta = {
  title: "Screens/Apps/Settings/AppSettingsTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const ALL: AppSettingsTabAccess = { update: true, deploy: true, delete: true, webhooks: true };
const NONE: AppSettingsTabAccess = { update: false, deploy: false, delete: false, webhooks: false };

const SLOTS: AppSettingsTabSlots = {
  identity: <AppIdentityView {...IDENTITY} />,
  resync: <ResyncSourceView {...RESYNC} />,
  webhookDeploys: <WebhookDeploysPauseView {...WEBHOOK_DEPLOYS} />,
  forceRedeploy: <ForceRedeployView {...FORCE_REDEPLOY} />,
  runJob: <RunScheduledJobView {...RUN_JOB} />,
  ingress: <IngressControlsView {...INGRESS} />,
  retention: <RetentionPolicyView {...RETENTION} />,
  managedServicesAdmin: <ManagedServicesAdminView {...MANAGED_SERVICES_ADMIN} />,
  configuration: <ConfigEditorScreen {...EDITOR} />,
  manifest: <ManifestPreviewScreen {...PREVIEW_PROPS} />,
  environmentSettings: <EnvironmentSettingsView {...ENV_SETTINGS} />,
  archive: <ArchiveAppView {...ARCHIVE} />,
  deregister: <DangerZoneView {...DANGER_ZONE} preview={DEREGISTER_PREVIEW} />,
};

function Tab({
  initial = null,
  access = ALL,
  slots = SLOTS,
  restrictedMode = "show",
  loading = false,
  found = true,
}: {
  initial?: string | null;
  access?: AppSettingsTabAccess;
  slots?: AppSettingsTabSlots;
  restrictedMode?: "show" | "hide";
  loading?: boolean;
  found?: boolean;
}) {
  const section = useLocalSettingsSection(initial);
  return (
    <AppChromeProvider framed>
      <AppSettingsTab
        slug="checkout"
        loading={loading}
        found={found}
        section={section}
        access={access}
        slots={slots}
        restrictedMode={restrictedMode}
      />
    </AppChromeProvider>
  );
}

/** General, grouped by concern; each part saves or acts on its own. */
export const Full: Story = {
  render: () => <Tab />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Source and build")).toBeInTheDocument();
    await expect(canvas.getByText("Traffic")).toBeInTheDocument();
  },
};

export const Configuration: Story = { render: () => <Tab initial="configuration" /> };

export const Manifest: Story = { render: () => <Tab initial="manifest" /> };

export const Environments: Story = { render: () => <Tab initial="environments" /> };

/** Archive and deregister, each behind its own confirm. */
export const DangerZone: Story = {
  render: () => <Tab initial="danger-zone" />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: /Archive/ })).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: /Deregister app/ })).toBeInTheDocument();
  },
};

/** Webhook deploys paused: the reason and who paused them. */
export const Paused: Story = {
  render: () => (
    <Tab
      slots={{ ...SLOTS, webhookDeploys: <WebhookDeploysPauseView {...WEBHOOK_DEPLOYS_PAUSED} /> }}
    />
  ),
};

/** A viewer without the permissions: the same parts, disabled, naming each one. */
export const ReadOnly: Story = {
  render: () => <Tab access={NONE} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getAllByText("app.update").length).toBeGreaterThan(0);
  },
};

/** The person hides what they can't change: only what they may change is left. */
export const HiddenWhenRestricted: Story = {
  render: () => (
    <Tab
      restrictedMode="hide"
      access={{ update: false, deploy: true, delete: false, webhooks: true }}
    />
  ),
};

/** First load of the app. */
export const Loading: Story = { render: () => <Tab loading /> };

/** Nothing to show: every part hidden for this viewer. */
export const Empty: Story = { render: () => <Tab restrictedMode="hide" access={NONE} /> };

/** The app is gone, or the viewer may not read it. */
export const NotFound: Story = { render: () => <Tab found={false} /> };

/** A section's own error: the manifest failed to render. */
export const ConfigurationError: Story = {
  render: () => (
    <Tab
      initial="configuration"
      slots={{
        ...SLOTS,
        configuration: <ConfigEditorScreen {...EDITOR} rendered={RENDER_FAILED} />,
      }}
    />
  ),
};

const LONG_SLOTS: AppSettingsTabSlots = {
  ...SLOTS,
  identity: <AppIdentityView {...IDENTITY_LONG} />,
  configuration: <ConfigEditorScreen {...EDITOR} slug={LONG} app={LONG_APP} />,
  archive: <ArchiveAppView {...ARCHIVE} appName={LONG} />,
  deregister: <DangerZoneView {...DANGER_ZONE} appName={LONG} stillLive={[LONG, `${LONG}-2`]} />,
};

export const LongStrings: Story = { render: () => <Tab slots={LONG_SLOTS} /> };

export const LongStringsDangerZone: Story = {
  render: () => <Tab slots={LONG_SLOTS} initial="danger-zone" />,
};

function At768({ children }: { children: React.ReactNode }) {
  return <div style={{ width: 768 }}>{children}</div>;
}

export const Width768: Story = {
  render: () => (
    <At768>
      <Tab slots={LONG_SLOTS} />
    </At768>
  ),
};

/**
 * An agent's Settings tab: General, Environments, Domains, Managed services
 * and the one Danger zone (an agent has no Domains or Workloads tab of its
 * own, so they are sections here).
 */
const AGENT_SLOTS: AppSettingsTabSlots = {
  resync: <ResyncSourceView {...RESYNC} />,
  environments: <p>The environments list renders here.</p>,
  domains: <p>The domains list renders here.</p>,
  managedServices: <p>The managed services list renders here.</p>,
  archive: <ArchiveAppView {...ARCHIVE} />,
  deregister: <DangerZoneView {...DANGER_ZONE} preview={DEREGISTER_PREVIEW} />,
};

export const AgentSections: Story = {
  render: () => <Tab slots={AGENT_SLOTS} initial="domains" />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("The domains list renders here.")).toBeInTheDocument();
    await expect(canvas.getAllByText("Managed services").length).toBeGreaterThan(0);
    await expect(canvas.getAllByText("Danger zone")).toHaveLength(1);
  },
};
