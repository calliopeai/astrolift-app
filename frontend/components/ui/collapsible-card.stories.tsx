import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { CollapsibleCard } from "@/components/ui/collapsible-card";

const meta: Meta<typeof CollapsibleCard> = {
  title: "Atoms/CollapsibleCard",
  component: CollapsibleCard,
};
export default meta;

export const Open: StoryObj = {
  render: () => (
    <CollapsibleCard title="Advanced" defaultOpen>
      <p className="text-sm">Replicas, resource requests and autoscaling targets.</p>
    </CollapsibleCard>
  ),
};

export const Closed: StoryObj = {
  render: () => (
    <CollapsibleCard title="Advanced" defaultOpen={false}>
      <p className="text-sm">Hidden until opened.</p>
    </CollapsibleCard>
  ),
};
