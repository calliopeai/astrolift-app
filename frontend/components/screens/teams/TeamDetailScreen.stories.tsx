import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";
import { ENTITY_ACCESS_LIST } from "@/components/screens/administration/access/entity-access";
import { entityAccessProps } from "@/components/screens/administration/access/principal.fixtures";

import { TeamAccessPanel } from "./TeamAccessPanel";
import { TeamDetailScreen } from "./TeamDetailScreen";
import { TEAM_MEMBERS_LIST } from "./teams-list";
import {
  MEMBERS_LONG,
  membersPanelProps,
  PROJECTS,
  TEAM_DETAIL,
  TEAM_DETAIL_LONG,
  TEAM_LONG,
} from "./teams.fixtures";
import { TeamMembersPanel } from "./TeamMembersPanel";

const meta: Meta = {
  title: "Screens/Teams/TeamDetailScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Access() {
  const list = useLocalListState(ENTITY_ACCESS_LIST);
  return (
    <TeamAccessPanel
      slug="platform"
      access={{ list, ...entityAccessProps() }}
      reach={{ projects: PROJECTS, loading: false, error: null, onRetry: () => {} }}
    />
  );
}

function Members({ long = false }: { long?: boolean }) {
  const list = useLocalListState(TEAM_MEMBERS_LIST);
  return (
    <TeamMembersPanel
      list={list}
      {...membersPanelProps(list, long ? MEMBERS_LONG : undefined, long ? { team: TEAM_LONG } : {})}
    />
  );
}

/** Access: every grant held at the team, and what it reaches. */
export const Full: Story = {
  render: () => (
    <TeamDetailScreen {...TEAM_DETAIL} tab="access">
      <Access />
    </TeamDetailScreen>
  ),
};

export const MembersTab: Story = {
  render: () => (
    <TeamDetailScreen {...TEAM_DETAIL} tab="members">
      <Members />
    </TeamDetailScreen>
  ),
};

export const Loading: Story = {
  render: () => (
    <TeamDetailScreen {...TEAM_DETAIL} team={null} loading tab="access">
      <Access />
    </TeamDetailScreen>
  ),
};

/** The slug matched no team. */
export const NotFound: Story = {
  render: () => (
    <TeamDetailScreen {...TEAM_DETAIL} slug="no-such-team" team={null} tab="access">
      <Access />
    </TeamDetailScreen>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <TeamDetailScreen
      {...TEAM_DETAIL}
      team={null}
      error={{ message: "upstream timed out" }}
      tab="access"
    >
      <Access />
    </TeamDetailScreen>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <TeamDetailScreen {...TEAM_DETAIL_LONG} tab="members">
      <Members long />
    </TeamDetailScreen>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <TeamDetailScreen {...TEAM_DETAIL_LONG} tab="access">
        <Access />
      </TeamDetailScreen>
    </div>
  ),
};
