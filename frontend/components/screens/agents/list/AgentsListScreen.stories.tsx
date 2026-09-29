import type { Decorator, Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { AGENTS_LIST, type AgentRow } from "./agents-list";
import {
  AGENT_ROWS,
  LONG_AGENT_ROW,
  MANY_AGENT_ROWS,
  listProps,
  serveAgents,
} from "./agents-list.fixtures";
import { AgentsListScreen, type AgentsListScreenProps } from "./AgentsListScreen";

// List or cards is a per-person preference in localStorage; pin it per story
// so one story's mode never leaks into the next.
const withMode: Decorator = (Story, { parameters }) => {
  try {
    localStorage.setItem(
      `astrolift.list.${AGENTS_LIST.id}.mode`,
      JSON.stringify(parameters.mode ?? "list")
    );
  } catch {
    // storage unavailable: the list view
  }
  return <Story />;
};

const meta: Meta = {
  title: "Screens/Agents/List/AgentsListScreen",
  parameters: { layout: "padded" },
  decorators: [withMode],
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<AgentsListScreenProps, "list">> & {
  agents?: AgentRow[];
  initial?: Partial<ListState>;
};

/** The screen over fixture agents, filtered and paged the way the server does it. */
function Agents({ agents = AGENT_ROWS, initial, ...patch }: Props) {
  const list = useLocalListState(AGENTS_LIST, initial);
  const { rows, totalCount } = serveAgents(agents, {
    q: list.state.q,
    filters: list.filters,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return <AgentsListScreen {...listProps({ rows, totalCount, ...patch })} list={list} />;
}

/** The fleet by name: running, failing, scheduled (next firing), paused and idle agents. */
export const Full: Story = { render: () => <Agents /> };

export const Loading: Story = { render: () => <Agents agents={[]} loading /> };

/** No agents registered yet: New agent and the docs link. */
export const Empty: Story = { render: () => <Agents agents={[]} /> };

/** A viewer without the Agents module's create: no New agent, no Run now. */
export const ReadOnly: Story = { render: () => <Agents canCreate={false} canRun={false} /> };

export const ErrorState: Story = {
  render: () => <Agents agents={[]} error={{ message: "Network error: failed to fetch" }} />,
};

/** Rows answer the previous poll while the next loads. */
export const Refetching: Story = { render: () => <Agents stale /> };

/** Mine: agents the viewer registered, with the note on agents that have no owner. */
export const Mine: Story = { render: () => <Agents initial={{ view: "mine" }} /> };

export const Paused: Story = { render: () => <Agents initial={{ view: "paused" }} /> };

/** The Paused view with nothing paused. */
export const EmptyView: Story = {
  render: () => <Agents agents={AGENT_ROWS.slice(0, 1)} initial={{ view: "paused" }} />,
};

/** Project, model and cluster chips. */
export const Filtered: Story = {
  render: () => (
    <Agents
      initial={{ filters: { project: "platform", model: "managed", cluster: "eks-us-west-2" } }}
    />
  ),
};

/** A chip that matches nothing: "No agents match" and Clear. */
export const EmptyFiltered: Story = {
  render: () => <Agents initial={{ filters: { runtime: "no-such-runtime" } }} />,
};

/** Sixty agents: numbered pages, page 2 of 3. */
export const Paged: Story = {
  render: () => <Agents agents={MANY_AGENT_ROWS} initial={{ page: 2 }} />,
};

export const Cards: Story = { parameters: { mode: "card" }, render: () => <Agents /> };

export const CardsLoading: Story = {
  parameters: { mode: "card" },
  render: () => <Agents agents={[]} loading />,
};

export const CardsEmpty: Story = {
  parameters: { mode: "card" },
  render: () => <Agents agents={[]} />,
};

export const CardsError: Story = {
  parameters: { mode: "card" },
  render: () => <Agents agents={[]} error={{ message: "upstream timed out" }} />,
};

/** A 64-char slug suffix, a 200-char ARN app slug and an unbroken repo URL. */
export const LongStrings: Story = {
  render: () => <Agents agents={[LONG_AGENT_ROW, ...AGENT_ROWS]} />,
};

export const LongStringsCards: Story = {
  parameters: { mode: "card" },
  render: () => <Agents agents={[LONG_AGENT_ROW, ...AGENT_ROWS]} />,
};

/** The narrowest supported width: the table scrolls in its own frame, the page does not. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Agents agents={[LONG_AGENT_ROW, ...MANY_AGENT_ROWS]} />
    </div>
  ),
};

export const Width768Cards: Story = {
  parameters: { mode: "card" },
  render: () => (
    <div style={{ width: 768 }}>
      <Agents agents={[LONG_AGENT_ROW, ...AGENT_ROWS]} />
    </div>
  ),
};

/** Views are the header's tabs and New agent links to its page. */
export const HeaderNavigation: Story = {
  render: () => <Agents />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const view of ["All", "Mine", "Paused"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
    await expect(canvas.getByRole("link", { name: "New agent" })).toHaveAttribute(
      "href",
      "/agents/new"
    );
  },
};
