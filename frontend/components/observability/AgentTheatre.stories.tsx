import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  AgentTheatre,
  type AgentTheatreProps,
  type GalleryTask,
} from "@/components/observability/AgentTheatre";

const meta: Meta = { title: "Patterns/Observability/AgentTheatre" };
export default meta;

type Story = StoryObj;

// A flat emerald frame standing in for a framebuffer snapshot.
const FRAME =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#04231A"/><rect x="12" y="12" width="136" height="10" fill="#2F9E52"/><rect x="12" y="30" width="90" height="6" fill="#8FD82A" opacity=".6"/></svg>'
  );

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const task = (id: string, startedAt: string | null, snapshotUrl: string | null): GalleryTask => ({
  id,
  status: "RUNNING",
  startedAt,
  vncEnabled: true,
  vncUrl: `/agents/vnc/${id}`,
  snapshotUrl,
});

const TASKS: GalleryTask[] = [
  task("7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b", minutesAgo(3), FRAME),
  task("a1b2c3d4-0000-4000-8000-000000000001", minutesAgo(47), FRAME),
  task("c0ffee00-1111-4111-8111-111111111111", null, null),
];

const base: AgentTheatreProps = {
  hasOrg: true,
  tasks: TASKS,
  loading: false,
  error: null,
  refreshing: false,
  onRefresh: async () => {},
  onRetry: () => {},
};

export const Tiles: Story = { render: () => <AgentTheatre {...base} /> };

export const Refreshing: Story = { render: () => <AgentTheatre {...base} refreshing /> };

export const Loading: Story = {
  render: () => <AgentTheatre {...base} tasks={null} loading />,
};

export const Empty: Story = { render: () => <AgentTheatre {...base} tasks={[]} /> };

export const LoadError: Story = {
  render: () => <AgentTheatre {...base} tasks={null} error="Response not successful: 502" />,
};

export const NoOrganization: Story = {
  render: () => <AgentTheatre {...base} hasOrg={false} tasks={null} />,
};
