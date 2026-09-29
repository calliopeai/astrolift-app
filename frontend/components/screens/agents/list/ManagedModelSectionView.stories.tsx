import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { managedModelProps } from "./agents-list.fixtures";
import { ManagedModelSectionView } from "./ManagedModelSectionView";

const meta: Meta = { title: "Screens/Agents/List/ManagedModelSectionView" };
export default meta;

type Story = StoryObj;

export const Off: Story = { render: () => <ManagedModelSectionView {...managedModelProps()} /> };

export const On: Story = {
  render: () => <ManagedModelSectionView {...managedModelProps({ managedOn: true })} />,
};

/** A write in flight: the switch is disabled (the loading state). */
export const Saving: Story = {
  render: () => (
    <ManagedModelSectionView {...managedModelProps({ managedOn: true, managedBusy: true })} />
  ),
};
