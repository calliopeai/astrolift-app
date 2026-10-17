import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  CONNECTED,
  DEGRADED_CONNECTION,
  DEVELOPER_ACCESS,
  FULL_ACCESS,
  NO_ACCESS,
  NOT_CONNECTED,
  REVOKED_CONNECTION,
  UPSTREAM_ERROR,
} from "./zentinelle-fixtures";
import { ZentinelleScreen, type ZentinelleScreenProps } from "./ZentinelleScreen";

/** Secure › Zentinelle in every state (#1888): one story per state, before any wiring. */
const meta: Meta = {
  title: "Screens/Secure/ZentinelleScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Screen(props: Partial<ZentinelleScreenProps>) {
  return <ZentinelleScreen {...CONNECTED} {...props} />;
}

const story = (props: Partial<ZentinelleScreenProps>): Story => ({
  render: () => <Screen {...props} />,
});

export const Loading: Story = story({ loading: true, connection: null });

/** An admin on an unconnected organization: the connect form. */
export const NotConnected: Story = story(NOT_CONNECTED);

/** The enrollment code exchange in flight. */
export const Connecting: Story = story({
  ...NOT_CONNECTED,
  connect: { ...CONNECTED.connect, connecting: true },
});

/** Unconnected, and the viewer may not connect: nothing to do but ask. */
export const NotConnectedViewer: Story = story({
  ...NOT_CONNECTED,
  access: { ...NO_ACCESS, status: true },
});

export const ConnectedHealthy: Story = story({});

export const GatewayDegraded: Story = story({ connection: DEGRADED_CONNECTION });

/** Zentinelle refused the install credential: disconnect, then connect again. */
export const Revoked: Story = story({ ...NOT_CONNECTED, connection: REVOKED_CONNECTION });

export const NoPermission: Story = story({ access: NO_ACCESS, connection: null });

/** The connection read failed. */
export const UpstreamError: Story = story({ error: UPSTREAM_ERROR, connection: null });

/** A developer: usage and policies, no status, no admin actions. */
export const DeveloperView: Story = story({
  access: DEVELOPER_ACCESS,
  connection: null,
  tab: "usage",
});

export const ClustersTab: Story = story({ tab: "clusters", connection: DEGRADED_CONNECTION });

export const UsageTab: Story = story({ tab: "usage" });

/** Zentinelle could not answer the usage read. */
export const UsageUpstreamError: Story = story({
  tab: "usage",
  usage: { ...CONNECTED.usage, usage: { data: [], loading: false, error: UPSTREAM_ERROR } },
});

export const PoliciesTab: Story = story({ tab: "policies" });

export const AuditTab: Story = story({ tab: "audit" });

/** Audit without export rights: the export buttons are gone. */
export const AuditNoExport: Story = story({
  tab: "audit",
  access: { ...FULL_ACCESS, auditExport: false },
});

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <ZentinelleScreen {...CONNECTED} connection={DEGRADED_CONNECTION} />
    </div>
  ),
};
