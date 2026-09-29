import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import type { AstroliftCommandRun } from "@/graphql/lifecycle/lifecycle.types";

import {
  COMMAND_RUNS,
  type CommandRunsFixture,
  LONG_COMMAND_RUNS,
  runListProps,
} from "./jobs-tasks.fixtures";
import { COMMAND_RUNS_LIST, commandRunsVariables } from "./jobs-list";
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
  // Stands in for `astroliftCommandRunsPage`, applying what the hook sends.
  const v = commandRunsVariables(list.filters, list.state);
  const served = runs.filter(
    (r) =>
      (!v.appSlug || r.registeredAppSlug === v.appSlug) && (!v.filter?.invokedBy || r.invokedByMe)
  );
  return <CommandRunsScreen {...runListProps(served)} {...patch} list={list} />;
}

export const Full: Story = { render: () => <Commands /> };

export const Loading: Story = { render: () => <Commands runs={[]} loading /> };

export const Empty: Story = { render: () => <Commands runs={[]} totalCount={0} /> };

export const ErrorState: Story = {
  render: () => <Commands runs={[]} error={{ message: "upstream timed out" }} />,
};

/** Mine: the commands the viewer invoked. */
export const Mine: Story = {
  render: () => (
    <Commands
      runs={COMMAND_RUNS.map((r) => ({ ...r, invokedByMe: r.invokedByUsername === "leo" }))}
      initial={{ view: "mine" }}
    />
  ),
};

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
