import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_MEMBER, LONG_ROLE_BINDINGS, MEMBER_DETAIL } from "./fixtures";
import { MemberDetail } from "./MemberDetail";

const meta: Meta = {
  title: "Screens/Administration/Access/MemberDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <MemberDetail {...MEMBER_DETAIL} /> };

export const Loading: Story = {
  render: () => <MemberDetail {...MEMBER_DETAIL} member={null} roleBindings={[]} loading />,
};

/** No such member, or one past the list query's window. */
export const NotFound: Story = {
  render: () => <MemberDetail {...MEMBER_DETAIL} member={null} roleBindings={[]} />,
};

export const RolesLoading: Story = {
  render: () => <MemberDetail {...MEMBER_DETAIL} roleBindings={[]} rolesLoading />,
};

export const NoRoles: Story = {
  render: () => <MemberDetail {...MEMBER_DETAIL} roleBindings={[]} />,
};

export const LongStrings: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} member={LONG_MEMBER} roleBindings={LONG_ROLE_BINDINGS} />
  ),
};

export const Error: Story = {
  render: () => (
    <MemberDetail
      {...MEMBER_DETAIL}
      member={null}
      roleBindings={[]}
      error={{ message: "upstream timed out after 30s (identity.members)" }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <MemberDetail {...MEMBER_DETAIL} member={LONG_MEMBER} roleBindings={LONG_ROLE_BINDINGS} />
    </div>
  ),
};
