import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import { DetailTabSections } from "./DetailTabSections";

const body = (
  <EmptyState
    icon={<LayersIcon className="size-5" />}
    title="Section body"
    description="The section's own screen renders here."
  />
);

const SECTIONS = [
  { id: "general", label: "General", href: "?" },
  { id: "environments", label: "Environments", href: "?section=environments" },
  { id: "domains", label: "Domains", href: "?section=domains" },
  { id: "danger-zone", label: "Danger zone", href: "?section=danger-zone" },
];

/**
 * The sections inside a consolidated detail tab (spec 44 §5.2): a list on
 * the left, above the body below `md`. It has no data, so no loading, empty
 * or error state of its own; the section's body carries those.
 */
const meta: Meta<typeof DetailTabSections> = {
  title: "Detail/DetailTabSections",
  component: DetailTabSections,
  parameters: { layout: "padded" },
  args: { ariaLabel: "Sections", sections: SECTIONS, active: "general", children: body },
};
export default meta;

type Story = StoryObj<typeof DetailTabSections>;

export const Default: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "General" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(canvas.getByRole("link", { name: "Domains" })).toHaveAttribute(
      "href",
      "?section=domains"
    );
  },
};

export const DangerZoneActive: Story = { args: { active: "danger-zone" } };

/** A 200-character label truncates in its row instead of widening the nav. */
export const LongLabels: Story = {
  args: {
    sections: [
      ...SECTIONS,
      {
        id: "long",
        label: `arn:aws:iam::123456789012:role/${"a".repeat(170)}`,
        href: "?section=long",
      },
    ],
  },
};

/** The narrowest the web console goes: the list wraps above the body. */
export const At768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <DetailTabSections {...args} />
    </div>
  ),
};
