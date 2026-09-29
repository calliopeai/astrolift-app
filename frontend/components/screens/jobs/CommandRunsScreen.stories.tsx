import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import type { AstroliftCommandRun } from "@/graphql/lifecycle/lifecycle.types";

import {
  COMMAND_RUNS,
  type CommandRunsFixture,
  LONG_COMMAND_RUNS,
  runListProps,
} from "./jobs-tasks.fixtures";
import { COMMAND_RUNS_LIST, narrowCommandRuns } from "./jobs-list";
import { CommandRunsScreen } from "./CommandRunsScreen";

const meta: Meta = {
  title: "Screens/Jobs/CommandRunsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Commands({
  runs = COMMAND_RUNS,
  initial,
  ...patch
}: Partial<CommandRunsFixture> & { runs?: AstroliftCommandRun[]; initial?: Partial<ListState> }) {
  const list = useLocalListState(COMMAND_RUNS_LIST, initial);
  return (
    <CommandRunsScreen
      {...runListProps(narrowCommandRuns(runs, list.filters, "leo"))}
      {...patch}
      list={list}
    />
  );
}

export const Full: Story = { render: () => <Commands /> };

export const Loading: Story = { render: () => <Commands runs={[]} loading /> };

export const Empty: Story = { render: () => <Commands runs={[]} totalCount={0} /> };

export const ErrorState: Story = {
  render: () => <Commands runs={[]} error={{ message: "upstream timed out" }} />,
};

/** Mine: the commands the viewer invoked. */
export const Mine: Story = { render: () => <Commands initial={{ view: "mine" }} /> };

export const LongStrings: Story = {
  render: () => <Commands runs={[...LONG_COMMAND_RUNS, ...COMMAND_RUNS]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Commands />
    </div>
  ),
};
