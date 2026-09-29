import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { AgentWorkloadPicker, SkillRefsPicker } from "@/components/workflows/pickers";
import { ORG_ID, SKILLS, WORKLOADS } from "@/components/workflows/fixtures";

const meta: Meta = { title: "Patterns/Workflows/Pickers" };
export default meta;

type Story = StoryObj;

function Workload(props: { initial?: string | null; loading?: boolean; org?: string | null }) {
  const [value, setValue] = React.useState(props.initial ?? null);
  return (
    <div className="max-w-sm">
      <AgentWorkloadPicker
        value={value}
        onChange={setValue}
        orgScoped={props.org === undefined ? ORG_ID : props.org}
        workloads={props.loading ? [] : WORKLOADS}
        loading={props.loading ?? false}
      />
    </div>
  );
}

function Skills(props: { initial?: string[]; loading?: boolean }) {
  const [value, setValue] = React.useState(props.initial ?? []);
  return (
    <div className="max-w-sm">
      <SkillRefsPicker
        value={value}
        onChange={setValue}
        orgScoped={ORG_ID}
        options={props.loading ? [] : SKILLS}
        loading={props.loading ?? false}
      />
    </div>
  );
}

export const WorkloadEmpty: Story = { render: () => <Workload /> };
export const WorkloadSelected: Story = { render: () => <Workload initial="wl-bdr" /> };
export const WorkloadLoading: Story = { render: () => <Workload loading /> };
/** The org is still resolving: disabled. */
export const WorkloadNoOrg: Story = { render: () => <Workload org={null} /> };

export const SkillsEmpty: Story = { render: () => <Skills /> };
/** An imported ref missing from the listing still renders, by slug. */
export const SkillsSelected: Story = {
  render: () => <Skills initial={["crm-lookup", "web-research", "legacy-imported-skill"]} />,
};
export const SkillsLoading: Story = { render: () => <Skills loading /> };
