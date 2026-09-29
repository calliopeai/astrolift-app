import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";

import { PersonTeamsPanel, type PersonTeamsPanelProps } from "./PersonTeamsPanel";
import { MEMBERSHIPS, teamsProps } from "./principal.fixtures";
import { PERSON_TEAMS_LIST, type TeamMembershipRow } from "./use-person-teams";

const meta: Meta = {
  title: "Screens/Administration/Access/PersonTeamsPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Panel({
  rows,
  ...overrides
}: Partial<Omit<PersonTeamsPanelProps, "list" | "rows">> & { rows?: TeamMembershipRow[] }) {
  const list = useLocalListState(PERSON_TEAMS_LIST);
  return <PersonTeamsPanel list={list} {...teamsProps(list, rows, overrides)} />;
}

export const Full: Story = { render: () => <Panel /> };

export const Loading: Story = { render: () => <Panel rows={[]} loading /> };

export const Empty: Story = { render: () => <Panel rows={[]} /> };

export const LoadFailed: Story = {
  render: () => <Panel rows={[]} error={{ message: "upstream timed out after 30s" }} />,
};

const LONG: TeamMembershipRow[] = [
  {
    ...MEMBERSHIPS[0]!,
    slug: "emea-regional-compliance-and-release-coordination-team-with-a-long-slug",
  },
  ...MEMBERSHIPS.slice(1),
];

export const LongStrings: Story = { render: () => <Panel rows={LONG} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Panel rows={LONG} />
    </div>
  ),
};
