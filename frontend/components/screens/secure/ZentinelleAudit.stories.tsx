import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ZentinelleAudit } from "./ZentinelleAudit";
import { CONNECTED, UPSTREAM_ERROR } from "./zentinelle-fixtures";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/Audit",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const props = { ...CONNECTED.audit, canExport: true };

export const Full: Story = { render: () => <ZentinelleAudit {...props} /> };

export const Exporting: Story = { render: () => <ZentinelleAudit {...props} exporting /> };

export const NoExport: Story = { render: () => <ZentinelleAudit {...props} canExport={false} /> };

export const Loading: Story = {
  render: () => <ZentinelleAudit {...props} feed={{ ...props.feed, items: [], loading: true }} />,
};

export const Empty: Story = {
  render: () => <ZentinelleAudit {...props} feed={{ ...props.feed, items: [], hasMore: false }} />,
};

export const UpstreamError: Story = {
  render: () => (
    <ZentinelleAudit {...props} feed={{ ...props.feed, items: [], error: UPSTREAM_ERROR }} />
  ),
};
