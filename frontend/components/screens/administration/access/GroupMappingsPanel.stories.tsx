import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { GROUP_MAPPINGS_LIST } from "./group-mappings";
import { GroupMappingsPanel, type GroupMappingsPanelProps } from "./GroupMappingsPanel";
import { GROUP_MAPPINGS, mappingsProps } from "./principal.fixtures";

const meta: Meta = {
  title: "Screens/Administration/Access/GroupMappingsPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Panel(overrides: Partial<Omit<GroupMappingsPanelProps, "list">>) {
  const list = useLocalListState(GROUP_MAPPINGS_LIST);
  return <GroupMappingsPanel list={list} {...mappingsProps(overrides)} />;
}

/** The group mapped to two roles, each reaching its members. */
export const Full: Story = { render: () => <Panel /> };

/** Map a role opens the form: a role, then where it applies. */
export const Adding: Story = {
  render: () => <Panel />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Map a role" }));
    await userEvent.click(c.getByRole("button", { name: "Map role" }));
    await expect(c.getByRole("alert")).toHaveTextContent("Pick a role.");
  },
};

/** The server refused the mapping: its reason sits in the form. */
export const AddRefused: Story = {
  render: () => (
    <Panel
      initialAdding
      onCreate={() => Promise.resolve("You cannot grant a role wider than your own reach.")}
    />
  ),
};

export const Empty: Story = { render: () => <Panel rows={[]} /> };

export const Loading: Story = { render: () => <Panel rows={[]} loading /> };

export const LoadFailed: Story = {
  render: () => <Panel rows={[]} error={{ message: "upstream timed out after 30s" }} />,
};

/** A viewer without org.manage_members: no add, no row menu. */
export const ReadOnly: Story = { render: () => <Panel canManage={false} /> };

export const LongStrings: Story = {
  render: () => (
    <Panel
      externalId="azure_ad:emea-regional-compliance-and-release-coordination-group-0001"
      rows={[
        {
          ...GROUP_MAPPINGS[0],
          id: "grm-long",
          role: {
            id: "r-long",
            slug: "regional-compliance-and-release-coordination-for-emea-subsidiaries",
            name: "Regional Compliance and Release Coordination for EMEA Subsidiaries (Interim)",
          },
          sourceScopeLabel:
            "project emea/emea-subsidiary-quarter-end-freeze-coordination-and-release-readiness",
        },
        ...GROUP_MAPPINGS,
      ]}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Panel initialAdding />
    </div>
  ),
};
