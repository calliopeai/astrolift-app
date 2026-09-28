import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftTeam } from "@/graphql/identity/identity.types";

import { TeamsScreen } from "./TeamsScreen";
import { TEAMS_SCREEN, TEAMS_SCREEN_LONG } from "./teams-tokens.fixtures";

const meta: Meta = { title: "Screens/Teams/TeamsScreen" };
export default meta;

type Story = StoryObj;

// The create / edit sheets run their own mutations and live with the route,
// so the catalog renders the list without them.
function Screen(props: typeof TEAMS_SCREEN) {
  return <TeamsScreen {...props} renderCreateDialog={() => null} renderEditDialog={() => null} />;
}

export const Full: Story = { render: () => <Screen {...TEAMS_SCREEN} /> };

export const Loading: Story = {
  render: () => (
    <Screen {...TEAMS_SCREEN} table={fakeController<AstroliftTeam>({ state: "loading" })} />
  ),
};

export const Empty: Story = {
  render: () => (
    <Screen {...TEAMS_SCREEN} table={fakeController<AstroliftTeam>({ state: "empty" })} />
  ),
};

export const NoSearchMatch: Story = {
  render: () => (
    <Screen
      {...TEAMS_SCREEN}
      table={fakeController<AstroliftTeam>({
        state: "emptyFiltered",
        isFiltered: true,
        search: "zzz",
      })}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Screen
      {...TEAMS_SCREEN}
      table={fakeController<AstroliftTeam>({
        state: "error",
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

export const LongStrings: Story = { render: () => <Screen {...TEAMS_SCREEN_LONG} /> };
