import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { vncProps } from "./agents-list.fixtures";
import { VncSessionSectionView } from "./VncSessionSectionView";

const meta: Meta = { title: "Screens/Agents/List/VncSessionSectionView" };
export default meta;

type Story = StoryObj;

export const Off: Story = { render: () => <VncSessionSectionView {...vncProps()} /> };

export const On: Story = { render: () => <VncSessionSectionView {...vncProps({ vncOn: true })} /> };

/** A write in flight: the switch is disabled (the loading state). */
export const Saving: Story = {
  render: () => <VncSessionSectionView {...vncProps({ vncOn: true, vncBusy: true })} />,
};
