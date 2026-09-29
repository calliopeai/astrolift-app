import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { TEAMS_LIST } from "./teams-list";
import { TEAM_LONG, TEAMS, teamsScreenProps } from "./teams.fixtures";
import { TeamsScreen, type TeamsScreenProps } from "./TeamsScreen";

const meta: Meta = {
  title: "Screens/Teams/TeamsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

// The create / edit sheets run their own mutations and live with the route,
// so the catalog renders the list without them.
function Screen({
  teams,
  initial,
  ...overrides
}: Partial<Omit<TeamsScreenProps, "list">> & {
  teams?: typeof TEAMS;
  initial?: Partial<ListState>;
}) {
  const list = useLocalListState(TEAMS_LIST, initial);
  return (
    <TeamsScreen
      list={list}
      {...teamsScreenProps(list, teams, overrides)}
      renderCreateDialog={() => null}
      renderEditDialog={() => null}
    />
  );
}

export const Full: Story = { render: () => <Screen /> };

export const Loading: Story = { render: () => <Screen teams={[]} loading /> };

export const Empty: Story = { render: () => <Screen teams={[]} /> };

/** Mine says why it is empty. */
export const Mine: Story = { render: () => <Screen initial={{ view: "mine" }} /> };

export const NoSearchMatch: Story = {
  render: () => <Screen teams={[]} initial={{ q: "zzz" }} />,
};

export const LoadFailed: Story = {
  render: () => <Screen teams={[]} error={{ message: "upstream timed out" }} />,
};

export const ReadOnly: Story = {
  render: () => <Screen canUpdate={false} canDelete={false} />,
};

export const LongStrings: Story = { render: () => <Screen teams={[TEAM_LONG, ...TEAMS]} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen teams={[TEAM_LONG, ...TEAMS]} />
    </div>
  ),
};
