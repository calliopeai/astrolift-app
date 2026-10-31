import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";

import {
  AppAccessOverviewScreen,
  type AppAccessOverviewScreenProps,
} from "./AppAccessOverviewScreen";
import { EDGE_ACCESS_LIST } from "./edge-access-list";
import {
  APP_ACCESS,
  EDGE_ACCESS_LONG,
  EDGE_ACCESS_ROWS,
  PERSON,
  PERSON_ROWS,
  PERSON_UNRESOLVED,
  PERSON_UNRESOLVED_ROWS,
} from "./fixtures";

/** Admin › App access: who may enter each app, and whether the edge holds it (#2534). */
const meta: Meta = {
  title: "Screens/Administration/AppAccess/Overview",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<AppAccessOverviewScreenProps, "list">> & { initial?: Partial<ListState> };

function Overview({ initial, ...props }: Props) {
  const list = useLocalListState(useLocalizedList(EDGE_ACCESS_LIST), initial);
  return <AppAccessOverviewScreen {...APP_ACCESS} list={list} {...props} />;
}

/** Every status: enforced, applying, pending a deploy, drift, unverified, open, no edge. */
export const Full: Story = {
  render: () => <Overview />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Takes effect on the next deploy")).toBeInTheDocument();
    const link = canvas.getAllByRole("link", { name: /conflict-bdr/ })[0];
    await expect(link).toHaveAttribute("href", "/apps/conflict-bdr/access?section=edge");
  },
};

export const Loading: Story = {
  render: () => <Overview rows={[]} loading totalCount={null} />,
};

export const Empty: Story = {
  render: () => <Overview rows={[]} nextCursor={null} totalCount={0} />,
};

export const EmptyFiltered: Story = {
  render: () => (
    <Overview
      rows={[]}
      nextCursor={null}
      totalCount={0}
      initial={{ filters: { cluster: "conflict-nginx", status: "drift" } }}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <Overview rows={[]} error={{ message: "upstream timed out after 30s (astroliftEdgeAccess)" }} />
  ),
};

/** Not enforced: the rules the edge does not hold yet, each with what fixes it. */
export const NotEnforced: Story = {
  render: () => {
    const rows = EDGE_ACCESS_ROWS.filter((r) =>
      ["applying", "pending_deploy", "drift", "unverified"].includes(r.status)
    );
    return (
      <Overview
        rows={rows}
        totalCount={rows.length}
        nextCursor={null}
        initial={{ view: "attention" }}
      />
    );
  },
};

/** One person: the apps they may enter and the grant that lets them in. */
export const Person: Story = {
  render: () => (
    <Overview
      rows={PERSON_ROWS}
      person={PERSON}
      totalCount={PERSON_ROWS.length}
      nextCursor={null}
      initial={{ filters: { person: PERSON.email } }}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Group: conflict-sales")).toBeInTheDocument();
    await expect(canvas.getByText(/4 other environments refuse them/)).toBeInTheDocument();
  },
};

/** Mine: the viewer's own email, the same as a person filter. */
export const Mine: Story = {
  render: () => (
    <Overview
      rows={PERSON_ROWS}
      person={{ ...PERSON, email: "leo@calliope.ai", refusedCount: 0 }}
      totalCount={PERSON_ROWS.length}
      nextCursor={null}
      initial={{ view: "mine" }}
    />
  ),
};

/** A provider that cannot be listed: group rules are unknown, and the notice says why. */
export const PersonUnresolved: Story = {
  render: () => (
    <Overview
      rows={PERSON_UNRESOLVED_ROWS}
      person={PERSON_UNRESOLVED}
      totalCount={PERSON_UNRESOLVED_ROWS.length}
      nextCursor={null}
      initial={{ filters: { person: PERSON_UNRESOLVED.email } }}
    />
  ),
};

/** A 200-char host, an unbroken group and a long email never widen the page. */
export const LongStrings: Story = {
  render: () => <Overview rows={EDGE_ACCESS_LONG} totalCount={1} nextCursor={null} />,
};

/** The narrowest the web console goes (spec 44 §6): the table scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Overview rows={[...EDGE_ACCESS_LONG, ...EDGE_ACCESS_ROWS]} />
    </div>
  ),
};
