import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { ZentinelleScopeFilter } from "./ZentinelleScopeFilter";
import { CONNECTED } from "./zentinelle-fixtures";
import type { ZentinelleScope, ZentinelleWindow } from "./zentinelle-model";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/ScopeFilter",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Live({ initial, teams }: { initial: ZentinelleScope; teams?: boolean }) {
  const [scope, setScope] = React.useState(initial);
  const [range, setRange] = React.useState<ZentinelleWindow>("24h");
  return (
    <ZentinelleScopeFilter
      scope={scope}
      onScopeChange={setScope}
      options={CONNECTED.usage.filter.options}
      teams={teams}
      range={range}
      onRangeChange={setRange}
    />
  );
}

export const All: Story = { render: () => <Live initial={{ kind: "all" }} /> };

export const OneAgent: Story = {
  render: () => <Live initial={{ kind: "agent", slug: "triage-bot" }} />,
};

export const Project: Story = {
  render: () => <Live initial={{ kind: "project", id: "0199a1b2-0000-7000-8000-0000000000p1" }} />,
};

export const NoTeamsNoOptions: Story = {
  render: () => (
    <ZentinelleScopeFilter
      scope={{ kind: "all" }}
      onScopeChange={() => {}}
      options={{ projects: [], teams: [] }}
      teams={false}
    />
  ),
};
