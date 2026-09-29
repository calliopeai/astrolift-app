import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  ACCESSES,
  LONG_ACCESS,
  LONG_TEAM,
  TEAMS,
  TEAMS_CARD,
} from "./app-overview-cards-a.fixtures";
import { AddTeamSheetView, MoveTeamSheetView, TeamsCardView } from "./TeamsCard";
import { APP_TEAM_ACCESS_LIST } from "./use-teams-card";

/** The card with in-memory list state, as the hook would pass it. */
function TeamsCard({ initial, ...props }: typeof TEAMS_CARD & { initial?: Partial<ListState> }) {
  const list = useLocalListState(APP_TEAM_ACCESS_LIST, initial);
  return <TeamsCardView {...props} list={list} />;
}

const meta: Meta<typeof TeamsCard> = {
  title: "Screens/Apps/Overview/TeamsCard",
  component: TeamsCard,
  args: TEAMS_CARD,
};
export default meta;

type Story = StoryObj<typeof TeamsCard>;

export const Full: Story = {};

export const Loading: Story = {
  args: {
    rows: [],
    loading: true,
    totalCount: null,
    teamsLoading: true,
  },
};

export const Empty: Story = {
  args: {
    rows: [],
    totalCount: 0,
  },
};

/** A search that matches no grant. */
export const EmptyFiltered: Story = {
  args: {
    rows: [],
    totalCount: 0,
    initial: { q: "nope" },
  },
};

export const LoadFailed: Story = {
  args: {
    rows: [],
    error: { message: "upstream timed out" },
  },
};

/** More grants than one page: Older is live on the cursor. */
export const MorePages: Story = { args: { nextCursor: "cursor-2", totalCount: 60 } };

/** Every team already holds a grant: Add team is disabled. */
export const AllTeamsGranted: Story = { args: { candidateTeams: [] } };

/** Viewer without app.update: static level badges, no actions. */
export const ReadOnly: Story = { globals: { permissions: "none" } };

export const LongStrings: Story = {
  args: {
    rows: [...ACCESSES, LONG_ACCESS],
    totalCount: ACCESSES.length + 1,
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

export const At768: Story = {
  args: LongStrings.args,
  render: (args) => (
    <div style={{ width: 768 }}>
      <TeamsCard {...args} />
    </div>
  ),
};
