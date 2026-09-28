import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_ROLE_BINDINGS, ROLE_BINDINGS } from "./fixtures";
import { MemberRolesPanel } from "./MemberRolesPanel";

const meta: Meta = {
  title: "Screens/Administration/Access/MemberRolesPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <MemberRolesPanel bindings={ROLE_BINDINGS} loading={false} />,
};

export const Loading: Story = { render: () => <MemberRolesPanel bindings={[]} loading /> };

/** A member with no bindings; the panel has no error state of its own. */
export const Empty: Story = {
  render: () => <MemberRolesPanel bindings={[]} loading={false} />,
};

export const LongStrings: Story = {
  render: () => <MemberRolesPanel bindings={LONG_ROLE_BINDINGS} loading={false} />,
};
