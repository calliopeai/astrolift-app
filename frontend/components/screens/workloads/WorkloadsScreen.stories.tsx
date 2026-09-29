import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { AREA, LONG_WORKLOADS, MANY_WORKLOADS } from "./workloads.fixtures";
import { selectWorkloads, WORKLOADS_LIST } from "./workloads-list";
import { WorkloadsScreen, type WorkloadsScreenProps } from "./WorkloadsScreen";

/** Agents › Workloads (spec 44 §4.1, §10.2). */
const meta: Meta = {
  title: "Screens/Workloads/WorkloadsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<WorkloadsScreenProps, "list">> & {
  workloads?: AstroliftWorkload[];
  initial?: Partial<ListState>;
};

/** The screen over fixture workloads, filtered and paged the way the hook does it. */
function Workloads({ workloads = AREA, initial, ...patch }: Props) {
  const list = useLocalListState(WORKLOADS_LIST, initial);
  const { rows, totalCount } = selectWorkloads(workloads, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return (
    <WorkloadsScreen
      list={list}
      rows={rows}
      totalCount={totalCount}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      {...patch}
    />
  );
}

export const Full: Story = {
  render: () => <Workloads />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("heading", { name: "Workloads" })).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: "Agents: switch" })).toBeInTheDocument();
    for (const view of ["All", "Mine", "Agents", "Workflows", "Functions"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
  },
};

export const Loading: Story = { render: () => <Workloads workloads={[]} loading /> };

export const Empty: Story = { render: () => <Workloads workloads={[]} /> };

export const Failed: Story = {
  render: () => <Workloads workloads={[]} error={{ message: "upstream timed out" }} />,
};

/** The walk is still reading pages: rows fade until it lands. */
export const Walking: Story = { render: () => <Workloads stale /> };

export const Agents: Story = { render: () => <Workloads initial={{ view: "agents" }} /> };

export const Functions: Story = { render: () => <Workloads initial={{ view: "functions" }} /> };

/** Workflows with none of that kind: the view's own empty line. */
export const NoWorkflows: Story = {
  render: () => (
    <Workloads
      workloads={AREA.filter((w) => w.kind !== "workflow")}
      initial={{ view: "workflows" }}
    />
  ),
};

export const Mine: Story = { render: () => <Workloads initial={{ view: "mine" }} /> };

export const EmptyFiltered: Story = {
  render: () => <Workloads initial={{ filters: { app: "no-such-app" } }} />,
};

export const Paged: Story = {
  render: () => <Workloads workloads={MANY_WORKLOADS} initial={{ page: 2 }} />,
};

/** A 64-char SHA slug, a 200-char ARN-shaped app slug and an unbroken URL name. */
export const LongStrings: Story = {
  render: () => <Workloads workloads={[...LONG_WORKLOADS, ...AREA]} />,
};

/** The narrowest the web console goes: no horizontal page scroll. */
export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Workloads workloads={[...LONG_WORKLOADS, ...MANY_WORKLOADS]} />
    </div>
  ),
};
