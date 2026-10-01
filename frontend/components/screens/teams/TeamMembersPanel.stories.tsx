import { NextIntlClientProvider, useTranslations } from "next-intl";
import de from "@/messages/de.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { localizedTeamMembersList } from "./teams-list";
import { MEMBERS, MEMBERS_LONG, membersPanelProps, TEAM_LONG } from "./teams.fixtures";
import { TeamMembersPanel, type TeamMembersPanelProps } from "./TeamMembersPanel";

const meta: Meta = {
  title: "Screens/Teams/TeamMembersPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Panel({
  members = MEMBERS,
  ...overrides
}: Partial<Omit<TeamMembersPanelProps, "list">> & { members?: typeof MEMBERS }) {
  const mt = useTranslations("teams.members");
  const list = useLocalListState(localizedTeamMembersList(mt));
  return <TeamMembersPanel list={list} {...membersPanelProps(list, members, overrides)} />;
}

export const Full: Story = { render: () => <Panel /> };

/** A viewer without team.manage_members: no selection, no Add member. */
export const ReadOnly: Story = { render: () => <Panel canManageTeamMembers={false} /> };

/** Two rows checked: Assign role shows in the selection bar. */
export const Selected: Story = {
  render: () => <Panel />,
  play: async ({ canvasElement }) => {
    const boxes = within(canvasElement).getAllByRole("checkbox");
    await userEvent.click(boxes[1]!);
    await userEvent.click(boxes[2]!);
  },
};

export const Loading: Story = { render: () => <Panel members={[]} loading /> };

export const Empty: Story = { render: () => <Panel members={[]} /> };

export const LoadFailed: Story = {
  render: () => <Panel members={[]} error={{ message: "upstream timed out" }} />,
};

export const LongStrings: Story = {
  render: () => <Panel members={MEMBERS_LONG} team={TEAM_LONG} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Panel members={MEMBERS_LONG} team={TEAM_LONG} />
    </div>
  ),
};

export const GermanWidth768: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de} timeZone="Europe/Berlin">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => (
    <div style={{ width: 768 }}>
      <Panel members={MEMBERS_LONG} team={TEAM_LONG} />
    </div>
  ),
};
export const RolesReadFailed: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getAllByRole("checkbox")[1]);
    await userEvent.click(canvas.getByRole("button", { name: /Assign role to/ }));
  },
  render: () => (
    <Panel
      roles={[]}
      roleSource={{
        known: false,
        loading: false,
        error: { message: "RAW_ROLE_READ_FAILURE" },
        onRetry: () => {},
      }}
    />
  ),
};
