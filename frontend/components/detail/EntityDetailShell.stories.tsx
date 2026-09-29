import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";

import { EntityDetailShell } from "@/components/detail/EntityDetailShell";

const meta: Meta = { title: "Patterns/Detail/EntityDetailShell" };
export default meta;

const BASE = {
  breadcrumb: { label: "Jobs", href: "#" },
  heading: "nightly-backup",
  status: "succeeded",
  createdAt: "2026-09-01T00:00:00Z",
  notFoundLabel: "Job not found",
  overview: [
    { term: "Schedule", description: "0 3 * * *" },
    { term: "Last run", description: "2 hours ago" },
  ],
};

export const Ready: StoryObj = {
  render: () => (
    <EntityDetailShell
      {...BASE}
      loading={false}
      notFound={false}
      actions={<Button size="sm">Run now</Button>}
    >
      <p className="text-sm">Detail content.</p>
    </EntityDetailShell>
  ),
};
export const Loading: StoryObj = {
  render: () => <EntityDetailShell {...BASE} loading notFound={false} />,
};
export const NotFound: StoryObj = {
  render: () => <EntityDetailShell {...BASE} loading={false} notFound />,
};
