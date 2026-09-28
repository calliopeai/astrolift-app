import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_POLICIES, policiesProps } from "./fixtures";
import { PoliciesScreen } from "./PoliciesScreen";

const meta: Meta = {
  title: "Screens/Administration/Access/PoliciesScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <PoliciesScreen {...policiesProps()} /> };

export const Loading: Story = {
  render: () => <PoliciesScreen {...policiesProps({ state: "loading", rows: [] })} />,
};

export const Empty: Story = {
  render: () => <PoliciesScreen {...policiesProps({ state: "empty", rows: [] })} />,
};

export const EmptyFiltered: Story = {
  render: () => (
    <PoliciesScreen
      {...policiesProps({ state: "emptyFiltered", rows: [], isFiltered: true, search: "zzz" })}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <PoliciesScreen
      {...policiesProps({
        state: "error",
        rows: [],
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

export const Deleting: Story = {
  render: () => <PoliciesScreen {...policiesProps({}, { deleting: true })} />,
};

export const LongStrings: Story = {
  render: () => <PoliciesScreen {...policiesProps({ rows: LONG_POLICIES, totalCount: 1 })} />,
};
