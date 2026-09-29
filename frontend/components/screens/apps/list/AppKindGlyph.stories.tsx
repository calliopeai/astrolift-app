import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { TOPOLOGY_META, type TopologyKind } from "@/lib/topology";

import { AppKindGlyph } from "./AppKindGlyph";

/**
 * One icon per app shape. It has no loading, empty or error state of its
 * own: an app with no workloads yet is the "empty" glyph.
 */
const meta: Meta = {
  title: "Screens/Apps/List/AppKindGlyph",
};
export default meta;

type Story = StoryObj;

export const Service: Story = { render: () => <AppKindGlyph topology="service" /> };

export const NoWorkloads: Story = { render: () => <AppKindGlyph topology={null} /> };

/** Every kind with its label, as the Kind filter lists them. */
export const AllKinds: Story = {
  render: () => (
    <ul className="grid gap-2 sm:grid-cols-2">
      {[...(Object.keys(TOPOLOGY_META) as TopologyKind[]), null].map((k) => (
        <li key={k ?? "none"} className="flex min-w-0 items-center gap-2 text-sm">
          <AppKindGlyph topology={k} />
          <span className="min-w-0 truncate">
            {k ? TOPOLOGY_META[k].label : "No workloads yet"}
          </span>
        </li>
      ))}
    </ul>
  ),
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="flex items-center gap-2">
      <AppKindGlyph topology="microservices" />
      <AppKindGlyph topology="service-agent" />
      <AppKindGlyph topology="scheduled" />
    </div>
  ),
};
