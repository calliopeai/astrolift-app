import type { Decorator, Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { selectWorkflows, WORKFLOWS_LIST, type WorkflowRow } from "./workflows-list";
import { LONG_ROWS, MANY_ROWS, WORKFLOW_ROWS, listProps } from "./workflows-list.fixtures";
import { WorkflowsListScreen, type WorkflowsListScreenProps } from "./WorkflowsListScreen";

// List or cards is a per-person preference in localStorage; pin it per story
// so one story's mode never leaks into the next.
const withMode: Decorator = (Story, { parameters }) => {
  try {
    localStorage.setItem(
      `astrolift.list.${WORKFLOWS_LIST.id}.mode`,
      JSON.stringify(parameters.mode ?? "list")
    );
  } catch {
    // storage unavailable: the list view
  }
  return <Story />;
};

const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowsListScreen",
  parameters: { layout: "padded" },
  decorators: [withMode],
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<WorkflowsListScreenProps, "list">> & {
  all?: WorkflowRow[];
  initial?: Partial<ListState>;
};

/** The screen over fixture rows, filtered and paged the way the hook does it. */
function Workflows({ all = WORKFLOW_ROWS, initial, ...patch }: Props) {
  const list = useLocalListState(WORKFLOWS_LIST, initial);
  const { rows, totalCount } = selectWorkflows(all, {
    q: list.state.q,
    filters: list.filters,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return <WorkflowsListScreen {...listProps({ rows, totalCount, ...patch })} list={list} />;
}

/** Configured workflows, repository and org definitions, by name. */
export const Full: Story = { render: () => <Workflows /> };

export const Loading: Story = { render: () => <Workflows all={[]} loading /> };

/** No workflows yet: New workflow and the docs link. */
export const Empty: Story = { render: () => <Workflows all={[]} /> };

export const ErrorState: Story = {
  render: () => <Workflows all={[]} error={{ message: "workflowsPage: upstream timed out" }} />,
};

/** Rows answer the previous fetch while the next loads. */
export const Refetching: Story = { render: () => <Workflows stale /> };

/** A viewer without the workflows module's create, manage or run: no New workflow, fewer actions. */
export const ReadOnly: Story = {
  render: () => (
    <Workflows canCreate={false} canManage={false} canRun={false} canViewPlatformRuns={false} />
  ),
};

/** Mine, with the note saying why it is empty. */
export const Mine: Story = { render: () => <Workflows initial={{ view: "mine" }} /> };

/** The platform templates, to create a workflow from or clone. */
export const Templates: Story = { render: () => <Workflows initial={{ view: "templates" }} /> };

export const TemplatesEmpty: Story = {
  render: () => (
    <Workflows
      all={WORKFLOW_ROWS.filter((r) => r.kind !== "template")}
      initial={{ view: "templates" }}
    />
  ),
};

/** Pattern and last-run chips. */
export const Filtered: Story = {
  render: () => <Workflows initial={{ filters: { pattern: "chained", status: "never" } }} />,
};

export const FilteredEmpty: Story = {
  render: () => <Workflows initial={{ q: "no-such-workflow" }} />,
};

/** Page 2 of 3 at 25 a page. */
export const Paged: Story = { render: () => <Workflows all={MANY_ROWS} initial={{ page: 2 }} /> };

/** The org has more configured workflows than the list reads. */
export const Truncated: Story = { render: () => <Workflows truncatedAt={200} /> };

/** Cards draw each workflow's stage shape, with the legend over the grid. */
export const Cards: Story = { parameters: { mode: "card" }, render: () => <Workflows /> };

export const CardsTemplates: Story = {
  parameters: { mode: "card" },
  render: () => <Workflows initial={{ view: "templates" }} />,
};

export const CardsLoading: Story = {
  parameters: { mode: "card" },
  render: () => <Workflows all={[]} loading />,
};

export const CardsEmpty: Story = {
  parameters: { mode: "card" },
  render: () => <Workflows all={[]} />,
};

/** A 200-char ARN project, a 64-char SHA and an unbroken URL in the description and source. */
export const LongStrings: Story = {
  render: () => <Workflows all={[...LONG_ROWS, ...WORKFLOW_ROWS]} />,
};

export const LongStringsCards: Story = {
  parameters: { mode: "card" },
  render: () => <Workflows all={[...LONG_ROWS, ...WORKFLOW_ROWS]} />,
};

/** The narrowest supported width: the table scrolls in its own frame, the page does not. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Workflows all={[...LONG_ROWS, ...MANY_ROWS]} />
    </div>
  ),
};

export const Width768Cards: Story = {
  parameters: { mode: "card" },
  render: () => (
    <div style={{ width: 768 }}>
      <Workflows all={[...LONG_ROWS, ...WORKFLOW_ROWS]} />
    </div>
  ),
};

/** Views are the header's tabs, New workflow links to the stepped page, rows open the workflow. */
export const HeaderNavigation: Story = {
  render: () => <Workflows />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const view of ["All", "Mine", "Templates"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
    await expect(canvas.getByRole("link", { name: "New workflow" })).toHaveAttribute(
      "href",
      "/workflows/new"
    );
  },
};
