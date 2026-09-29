import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";
import {
  LONG_ROLE_BINDINGS,
  ROLE_BINDINGS,
} from "@/components/screens/administration/access/fixtures";
import { accessProps } from "@/components/screens/administration/access/principal.fixtures";
import { ACCESS_LIST } from "@/components/screens/administration/access/principal-access";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { TeamAccessPanel } from "./TeamAccessPanel";
import { PROJECTS } from "./teams.fixtures";

const meta: Meta = {
  title: "Screens/Teams/TeamAccessPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const AT_TEAM: AstroliftRoleBinding[] = ROLE_BINDINGS.map((b, i) => ({
  ...b,
  id: `rb-t${i}`,
  scopeKind: "TEAM" as const,
  scopeId: "14",
  sourceScopeLabel: "team platform",
  user:
    i === 2
      ? null
      : { ...b.user!, id: `u-${i}`, username: ["leo", "keith", "", "eric", "ada"][i]! },
  groupExternalId: i === 2 ? "okta:platform-oncall" : "",
}));

function Panel({
  bindings = AT_TEAM,
  projects = PROJECTS,
  reachLoading = false,
}: {
  bindings?: AstroliftRoleBinding[];
  projects?: typeof PROJECTS;
  reachLoading?: boolean;
}) {
  const list = useLocalListState(ACCESS_LIST);
  return (
    <TeamAccessPanel
      slug="platform"
      access={{ list, ...accessProps(list, bindings) }}
      reach={{ projects, loading: reachLoading, error: null, onRetry: () => {} }}
    />
  );
}

export const Full: Story = { render: () => <Panel /> };

export const Loading: Story = { render: () => <Panel bindings={[]} projects={[]} reachLoading /> };

export const Empty: Story = { render: () => <Panel bindings={[]} projects={[]} /> };

export const LongStrings: Story = {
  render: () => <Panel bindings={[...LONG_ROLE_BINDINGS, ...AT_TEAM]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Panel bindings={[...LONG_ROLE_BINDINGS, ...AT_TEAM]} />
    </div>
  ),
};
