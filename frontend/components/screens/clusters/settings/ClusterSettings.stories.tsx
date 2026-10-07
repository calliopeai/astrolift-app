import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

import { AuthUsersView } from "./AuthUsers";
import { BootstrapPlanView } from "./BootstrapPlan";
import { CentralAuthView, IngressClassView } from "./CentralAuth";
import { ClusterAgentView } from "./ClusterAgent";
import { BootstrapHistoryView, ClusterSettingsScreen } from "./ClusterSettings";
import {
  AGENT,
  ARN200,
  AUTH_USERS,
  BOOTSTRAP_RUN,
  CENTRAL_AUTH,
  CLUSTER,
  FAILED_RUN,
  HISTORY,
  INGRESS_AUTH,
  INGRESS_CLASS,
  LONG,
  NO_ACCESS,
  PLAN,
  SETTINGS,
  SHA64,
  UNBROKEN_URL,
} from "./fixtures";
import { IngressAuthView } from "./IngressAuth";
import type { ClusterWithHeartbeat } from "./types";

/** Cluster settings on the settings archetype (spec 44 §5.3). */
const meta: Meta = {
  title: "Screens/Clusters/Settings/ClusterSettings",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const cards = {
  agent: <ClusterAgentView {...AGENT} />,
  ingressClass: <IngressClassView {...INGRESS_CLASS} />,
  centralAuth: <CentralAuthView {...CENTRAL_AUTH} />,
  ingressAuth: <IngressAuthView {...INGRESS_AUTH} />,
  authUsers: <AuthUsersView {...AUTH_USERS} />,
  bootstrapPlan: <BootstrapPlanView {...PLAN} />,
};
const history = <BootstrapHistoryView {...HISTORY} />;

export const Full: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} cards={cards} bootstrapHistory={history} />,
};

export const Loading: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} cluster={null} loading />,
};

/** calliope-installer#447: deleting the cloud cluster is refused with the reason; retiring stays. */
export const ClusterLifecycleWithheld: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      cards={cards}
      bootstrapHistory={history}
      deleteWithheldReason="Cluster lifecycle is withheld from Astrolift on this install: it runs on clusters it is handed and may not create or delete one."
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/Cluster lifecycle is withheld/)).toBeVisible();
  },
};

/** No cluster with this slug, or no permission to see it. */
export const NotFound: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} slug="no-such-cluster" cluster={null} />,
};

/** A failed read is unavailable, not a verified missing cluster. */
export const ReadFailed: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <ClusterSettingsScreen
        {...SETTINGS}
        cluster={null}
        error="RAW_CLUSTER_READ_ERROR"
        onRetry={() => {}}
      />
    </NextIntlClientProvider>
  ),
};

/** A same-target failed refresh retains the last confirmed observation. */
export const CachedReadFailed: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <ClusterSettingsScreen
        {...SETTINGS}
        cards={{ agent: <ClusterAgentView {...AGENT} /> }}
        error="RAW_CLUSTER_REFRESH_ERROR"
        onRetry={() => {}}
        section={{ active: "agent", href: (id) => `?section=${id}`, select: () => {} }}
      />
    </NextIntlClientProvider>
  ),
};

export const LifecycleReadOnly: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} readOnly />,
};

export const LifecyclePending: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} refreshing />,
};

export const Retiring: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      lifecycle="decommissioning"
      cluster={{ ...CLUSTER, lifecycle: "decommissioning" }}
    />
  ),
};

export const Retired: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      lifecycle="decommissioned"
      cluster={{ ...CLUSTER, lifecycle: "decommissioned" }}
    />
  ),
};

export const UnknownLifecycle: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      lifecycle="LITERAL_FUTURE_LIFECYCLE"
      cluster={{ ...CLUSTER, lifecycle: "LITERAL_FUTURE_LIFECYCLE" }}
    />
  ),
};

export const UnknownCapabilities: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      cluster={{ ...CLUSTER, capabilities: { cert_manager: {}, metrics_server: "false" } }}
    />
  ),
};

export const LastBootstrapUnknownStatus: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      cluster={{
        ...CLUSTER,
        lastBootstrapRun: {
          ...CLUSTER.lastBootstrapRun!,
          status: "LITERAL_FUTURE_BOOTSTRAP_STATUS",
        },
      }}
    />
  ),
};

export const MalformedReleaseReport: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      cluster={{
        ...CLUSTER,
        lastBootstrapRun: {
          ...CLUSTER.lastBootstrapRun!,
          installedReleases: [
            null,
            { name: 42, version: {}, status: false },
          ] as unknown as NonNullable<typeof CLUSTER.lastBootstrapRun>["installedReleases"],
        },
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "View installed releases" })
    );
  },
};

/** Registered but never brought into management: nothing probed yet. */
export const Registered: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      lifecycle="registered"
      cluster={{
        ...CLUSTER,
        lifecycle: "registered",
        capabilities: {},
        capabilitiesProbedAt: "2026-09-27T14:06:00Z",
        lastBootstrapRun: null,
      }}
      cards={{
        ...cards,
        bootstrapPlan: <BootstrapPlanView {...PLAN} plan={{ ...PLAN.plan!, components: [] }} />,
      }}
    />
  ),
};

export const Managing: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      lifecycle="managing"
      cluster={{ ...CLUSTER, lifecycle: "managing", capabilities: {} }}
      cards={{ ...cards, bootstrapPlan: <BootstrapPlanView {...PLAN} plan={null} loading /> }}
    />
  ),
};

/** The management workflow failed, and so did the last CLI bootstrap. */
export const ManagementError: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      lifecycle="error"
      cluster={
        {
          ...CLUSTER,
          lifecycle: "error",
          lastManagementError:
            "PreflightError: the cluster's API server refused the platform role (403 Forbidden on /apis/apps/v1/namespaces/astrolift-system/deployments)",
          lastBootstrapRun: FAILED_RUN,
        } as unknown as ClusterWithHeartbeat
      }
      cards={cards}
    />
  ),
};

/** Managed, but the probe found no cert-manager or ingress controller. */
export const MissingPrereqs: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      cluster={{
        ...CLUSTER,
        capabilities: {
          cert_manager: { installed: false },
          ingress: { installed: false },
          storage_classes: [],
        },
      }}
      cards={cards}
    />
  ),
};

const longCluster = {
  ...CLUSTER,
  slug: LONG,
  name: `Cluster ${LONG}`,
  endpoint: UNBROKEN_URL,
  authMethod: `irsa-${SHA64}`,
  ingressClass: `custom-${LONG}`,
  lastBootstrapRun: {
    ...BOOTSTRAP_RUN,
    chartVersion: `astrolift-system-${SHA64}`,
    triggeredByUsername: `${LONG}@example.com`,
    installedReleases: [{ name: `release-${LONG}`, version: SHA64, status: "deployed" }],
  },
} as unknown as ClusterWithHeartbeat;

const longCards = {
  ...cards,
  ingressAuth: (
    <IngressAuthView
      {...INGRESS_AUTH}
      existing={{
        user_pool_arn: ARN200,
        user_pool_client_id: SHA64,
        user_pool_domain: UNBROKEN_URL,
      }}
    />
  ),
};

/** A 64-character SHA, a 200-character ARN and an unbroken URL: nothing widens the page. */
export const LongStrings: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      slug={LONG}
      cluster={longCluster}
      cards={longCards}
      bootstrapHistory={history}
    />
  ),
};

/** The narrowest the console goes (spec 44 §6): the section nav is a select. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="border p-4">
      <ClusterSettingsScreen
        {...SETTINGS}
        slug={LONG}
        cluster={longCluster}
        cards={longCards}
        bootstrapHistory={history}
      />
    </div>
  ),
};

/**
 * A viewer with none of the cluster permissions: the same fields, disabled,
 * each naming the permission that would allow it; no lifecycle actions; the
 * sign-in users are not listed at all.
 */
export const ReadOnly: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      access={NO_ACCESS}
      cards={cards}
      bootstrapHistory={history}
    />
  ),
};

/** May run the lifecycle, may not change the edge or decommission. */
export const PartialAccess: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      access={{ manage: true, update: false, users: true, unregister: false }}
      cards={cards}
      bootstrapHistory={history}
    />
  ),
};

/**
 * The same viewer with "Settings you can't change: Hide" chosen under
 * Settings > Appearance: the parts they lack permission for are left out, and
 * sections left empty (Ingress class, Central auth, Ingress auth, Users,
 * Danger zone) leave the nav.
 */
export const ReadOnlyHidden: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      access={NO_ACCESS}
      cards={cards}
      bootstrapHistory={history}
      restrictedMode="hide"
    />
  ),
};

/** The decommission confirm: what goes and what stays. */
export const Decommission: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} cards={cards} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Decommission" }));
  },
};

export const HistoryOpen: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} cards={cards} bootstrapHistory={history} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /View history/ }));
    await expect(c.getByRole("button", { name: /Hide history/ })).toBeInTheDocument();
  },
};

/** The last run's installed releases; opening them closes the history, so one list shows. */
export const ReleasesOpen: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} cards={cards} bootstrapHistory={history} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /View history/ }));
    await userEvent.click(c.getByRole("button", { name: /View installed releases/ }));
    await expect(c.getByRole("cell", { name: "ingress-nginx" })).toBeInTheDocument();
    await expect(c.getByRole("button", { name: /View history/ })).toBeInTheDocument();
  },
};

export const HistoryLoading: Story = {
  render: () => <BootstrapHistoryView runs={[]} loading />,
};

export const HistoryEmpty: Story = {
  render: () => <BootstrapHistoryView runs={[]} loading={false} />,
};

export const History: Story = { render: () => <BootstrapHistoryView {...HISTORY} /> };

export const HistoryReadFailed: Story = {
  render: () => (
    <BootstrapHistoryView
      {...HISTORY}
      error="LITERAL_HISTORY_READ_DIAGNOSTIC"
      onRetry={() => undefined}
    />
  ),
};

export const HistoryUnknownStatus: Story = {
  render: () => (
    <BootstrapHistoryView
      {...HISTORY}
      runs={[{ ...HISTORY.runs[0], status: "LITERAL_UNKNOWN_STATUS" }]}
    />
  ),
};

function Sectioned({ initial }: { initial: string | null }) {
  return (
    <ClusterSettingsScreen
      {...SETTINGS}
      cards={cards}
      bootstrapHistory={history}
      section={useLocalSettingsSection(initial)}
    />
  );
}

/** As the route mounts it: one section at a time, the agent first. */
export const AgentSection: Story = { render: () => <Sectioned initial={null} /> };

/** Only the users section, and so only its list's query, is mounted. */
export const UsersSection: Story = { render: () => <Sectioned initial="users" /> };
