import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { AuthUsersView } from "./AuthUsers";
import { BootstrapPlanView } from "./BootstrapPlan";
import { CentralAuthView, IngressClassView } from "./CentralAuth";
import { ClusterAgentView } from "./ClusterAgent";
import { BootstrapHistoryView, ClusterSettingsScreen } from "./ClusterSettings";
import {
  AGENT,
  AUTH_USERS,
  CENTRAL_AUTH,
  CLUSTER,
  FAILED_RUN,
  HISTORY,
  INGRESS_AUTH,
  INGRESS_CLASS,
  LONG,
  PLAN,
  SETTINGS,
} from "./fixtures";
import { IngressAuthView } from "./IngressAuth";
import type { ClusterWithHeartbeat } from "./types";

const meta: Meta = {
  title: "Screens/Clusters/Settings/ClusterSettings",
  parameters: { layout: "fullscreen" },
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

/** No cluster with this slug, or no permission to see it. */
export const NotFound: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} slug="no-such-cluster" cluster={null} />,
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
      cards={{ ...cards, bootstrapPlan: <BootstrapPlanView {...PLAN} plan={null} /> }}
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

export const LongStrings: Story = {
  render: () => (
    <ClusterSettingsScreen
      {...SETTINGS}
      slug={LONG}
      cluster={{
        ...CLUSTER,
        slug: LONG,
        name: `Cluster ${LONG}`,
        endpoint: `https://${LONG}.gr7.us-west-2.eks.amazonaws.com:443/${LONG}`,
        ingressClass: `custom-${LONG}`,
      }}
      cards={cards}
      bootstrapHistory={history}
    />
  ),
};

export const HistoryOpen: Story = {
  render: () => <ClusterSettingsScreen {...SETTINGS} cards={cards} bootstrapHistory={history} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /View history/ }));
    await expect(c.getByRole("button", { name: /Hide history/ })).toBeInTheDocument();
  },
};

export const HistoryLoading: Story = {
  render: () => <BootstrapHistoryView runs={[]} loading />,
};

export const HistoryEmpty: Story = {
  render: () => <BootstrapHistoryView runs={[]} loading={false} />,
};

export const History: Story = { render: () => <BootstrapHistoryView {...HISTORY} /> };
