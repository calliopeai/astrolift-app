import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { RUN_AUDIT_LIST } from "./combined-runs";
import { CombinedRunsScreen, type CombinedRunsScreenProps } from "./CombinedRunsScreen";
import { COMBINED_RUNS, COMBINED_RUNS_LONG, RUN_AUDIT } from "./fixtures";

/** Admin › Usage & governance › Runs: the combined run audit (spec 44 §4.4, decision 14). */
const meta: Meta = {
  title: "Screens/Administration/Insights/CombinedRuns",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<CombinedRunsScreenProps, "list">> & { initial?: Partial<ListState> };

function Runs({ initial, ...props }: Props) {
  const list = useLocalListState(RUN_AUDIT_LIST, initial);
  return <CombinedRunsScreen {...RUN_AUDIT} list={list} {...props} />;
}

/** All four kinds, newest first, with who or what started each. */
export const Full: Story = { render: () => <Runs /> };

export const Loading: Story = {
  render: () => <Runs rows={[]} loading totalCount={null} coverage={null} />,
};

export const Empty: Story = {
  render: () => (
    <Runs rows={[]} nextCursor={null} totalCount={0} approximateCount={false} coverage={null} />
  ),
};

/** Mine with nothing in it: only sources that record the initiator can say. */
export const EmptyMine: Story = {
  render: () => <Runs rows={[]} nextCursor={null} initial={{ view: "mine" }} coverage={null} />,
};

export const EmptyFiltered: Story = {
  render: () => (
    <Runs
      rows={[]}
      nextCursor={null}
      initial={{ filters: { kind: "job", outcome: "cancelled" } }}
      coverage={null}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <Runs
      rows={[]}
      error={{ message: "upstream timed out after 30s (astroliftDeploymentsPage)" }}
      coverage={null}
    />
  ),
};

/** One source failed; the others still show, and the page names the missing one. */
export const PartialSources: Story = {
  render: () => (
    <Runs
      rows={COMBINED_RUNS.filter((r) => r.kind !== "workflow")}
      unavailable={["workflow runs"]}
    />
  ),
};

/** Filtered by kind, from a chip or a typed `kind:deployment` token. */
export const ByKind: Story = {
  render: () => (
    <Runs
      rows={COMBINED_RUNS.filter((r) => r.kind === "deployment")}
      totalCount={5}
      approximateCount={false}
      nextCursor={null}
      coverage={null}
      initial={{ filters: { kind: "deployment", since: "7d" } }}
    />
  ),
};

export const NewRuns: Story = {
  render: () => <Runs newRows={{ count: 2, onReveal: () => {} }} />,
};

/** A 64-char SHA, a 200-char ARN and an unbroken URL never widen the page. */
export const LongStrings: Story = {
  render: () => <Runs rows={COMBINED_RUNS_LONG} initial={{ q: COMBINED_RUNS_LONG[0].scope }} />,
};

/** The narrowest the web console goes (spec 44 §6): the table scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Runs rows={[...COMBINED_RUNS_LONG, ...COMBINED_RUNS]} />
    </div>
  ),
};

/** CSV export sits in the list's overflow menu. */
export const ExportMenu: Story = {
  render: () => <Runs />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "More" }));
    await expect(
      await within(document.body).findByRole("menuitem", { name: /Export CSV/ })
    ).toBeInTheDocument();
  },
};
