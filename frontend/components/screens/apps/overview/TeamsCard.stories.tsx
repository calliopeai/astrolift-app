import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

import {
  ACCESSES,
  LONG_ACCESS,
  LONG_TEAM,
  TEAMS,
  TEAMS_CARD,
} from "./app-overview-cards-a.fixtures";
import { AddTeamSheetView, MoveTeamSheetView, TeamsCardView } from "./TeamsCard";

const meta: Meta<typeof TeamsCardView> = {
  title: "Screens/Apps/Overview/TeamsCard",
  component: TeamsCardView,
  args: TEAMS_CARD,
};
export default meta;

type Story = StoryObj<typeof TeamsCardView>;

export const Full: Story = {};

export const Loading: Story = {
  args: {
    table: fakeController<AstroliftAppTeamAccess>({ state: "loading", sort: undefined }),
    teamsLoading: true,
  },
};

export const Empty: Story = {
  args: {
    table: fakeController<AstroliftAppTeamAccess>({ state: "empty", sort: undefined }),
  },
};

/** A search that matches no grant. */
export const EmptyFiltered: Story = {
  args: {
    table: fakeController<AstroliftAppTeamAccess>({
      state: "emptyFiltered",
      search: "nope",
      isFiltered: true,
      sort: undefined,
    }),
  },
};

export const LoadFailed: Story = {
  args: {
    table: fakeController<AstroliftAppTeamAccess>({
      state: "error",
      error: new globalThis.Error("upstream timed out"),
      sort: undefined,
    }),
  },
};

/** Every team already holds a grant: Add team is disabled. */
export const AllTeamsGranted: Story = { args: { candidateTeams: [] } };

/** Viewer without app.update: static level badges, no actions. */
export const ReadOnly: Story = { globals: { permissions: "none" } };

export const LongStrings: Story = {
  args: {
    table: fakeController<AstroliftAppTeamAccess>({
      rows: [...ACCESSES, LONG_ACCESS],
      totalCount: ACCESSES.length + 1,
      sort: undefined,
    }),
    candidateTeams: [LONG_TEAM],
  },
};

/** The Add-Team sheet open. The sheet renders in a portal, so it sits beside a stub. */
export const AddTeamSheet: Story = {
  render: () => (
    <div>
      <AddTeamSheetView
        open
        onOpenChange={() => {}}
        candidateTeams={TEAMS.slice(3)}
        teamsLoading={false}
        loading={false}
        onGrant={async () => true}
      />
    </div>
  ),
};

/** The Move-home-team sheet open. */
export const MoveTeamSheet: Story = {
  render: () => (
    <div>
      <MoveTeamSheetView
        open
        onOpenChange={() => {}}
        homeTeamSlug="platform"
        teams={TEAMS}
        teamsLoading={false}
        moveLoading={false}
        onMove={async () => {}}
      />
    </div>
  ),
};
