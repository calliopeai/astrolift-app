import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { LogLevelFilter } from "./app-log-lines";
import { LONG } from "./app-deployments-logs.fixtures";
import { LogFilters } from "./LogFilters";

const meta: Meta = { title: "Screens/Apps/Deployments/LogFilters" };
export default meta;

type Story = StoryObj;

const COUNTS = { error: 4, warn: 12, info: 310, debug: 0, other: 41 };

function Harness({
  initialQuery = "",
  children,
}: {
  initialQuery?: string;
  children?: React.ReactNode;
}) {
  const [level, setLevel] = React.useState<LogLevelFilter>("all");
  const [query, setQuery] = React.useState(initialQuery);
  return (
    <LogFilters
      level={level}
      onLevelChange={setLevel}
      query={query}
      onQueryChange={setQuery}
      counts={COUNTS}
    >
      {children}
    </LogFilters>
  );
}

export const Full: Story = { render: () => <Harness initialQuery="timeout" /> };

/** Nothing typed, no counts yet: the buffer is still loading. */
export const Loading: Story = {
  render: () => (
    <LogFilters level="all" onLevelChange={() => {}} query="" onQueryChange={() => {}} />
  ),
};

export const Empty: Story = { render: () => <Harness /> };

/** The filters have no error state of their own; a filter that matches nothing is the closest. */
export const NoMatch: Story = { render: () => <Harness initialQuery="no-such-text-anywhere" /> };

export const LongStrings: Story = {
  render: () => (
    <Harness initialQuery={LONG}>
      <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">{LONG}</span>
    </Harness>
  ),
};

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Harness initialQuery={LONG} />
    </div>
  ),
};
