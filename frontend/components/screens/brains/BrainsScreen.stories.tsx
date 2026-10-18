import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { BEHIND, DEPLOY_FAILED, DEPLOYING, summaryOf } from "./brain-status.fixtures";
import { ARCHIVED_BRAIN, BRAINS, CONNECTION, HOST_PROJECTS, LONG_BRAIN } from "./brains.fixtures";
import { BRAINS_LIST, withTeamOptions } from "./brains-list";
import { BrainsScreen, type BrainsScreenProps } from "./BrainsScreen";
import type { BrainRow } from "./use-brains";

const meta: Meta = {
  title: "Screens/Brains/BrainsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** One row per state the status columns draw, each with its cached summary (#2444). */
const ROWS: BrainRow[] = [
  ...BRAINS,
  {
    ...BRAINS[0]!,
    id: "b0000000-0000-4000-8000-000000000010",
    slug: "ops-brain",
    name: "Operations runbooks",
    team: "sre",
    project: "runbooks",
    sharedWith: [{ team: "platform", access: "deployer" }],
    summary: summaryOf(DEPLOY_FAILED),
  },
  {
    ...BRAINS[0]!,
    id: "b0000000-0000-4000-8000-000000000011",
    slug: "research-brain",
    name: "Research notes",
    team: "research",
    project: "notes",
    environments: [],
    sharedWith: [],
    summary: summaryOf(DEPLOYING),
  },
  {
    ...BRAINS[0]!,
    id: "b0000000-0000-4000-8000-000000000012",
    slug: "finance-brain",
    name: "Finance definitions",
    team: "finance",
    project: "metrics",
    environments: [{ name: "staging", url: "https://finance-brain.staging.example.test" }],
    sharedWith: [],
    summary: { ...summaryOf(BEHIND)!, environmentName: "staging" },
  },
];

const TEAMS = [...new Set([...ROWS, ARCHIVED_BRAIN].map((r) => r.team))].sort();

/**
 * The screen over a stand-in for `astroliftBrainsPage`: the team filter and
 * the With archived view narrow the rows as the server would.
 */
function Brains({
  rows: all = ROWS,
  view,
  ...patch
}: Partial<Omit<BrainsScreenProps, "list" | "rows">> & { rows?: BrainRow[]; view?: string }) {
  const list = useLocalListState(withTeamOptions(BRAINS_LIST, TEAMS), view ? { view } : {});
  const rows = all.filter(
    (r) =>
      (!list.filters.team || r.team === list.filters.team) &&
      (list.filters.archived === "1" || !r.archived)
  );
  return (
    <BrainsScreen
      list={list}
      rows={rows}
      totalCount={rows.length}
      nextCursor={null}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      canCreate
      connection={null}
      connecting={false}
      onConnect={async () => {}}
      onDismissConnection={() => {}}
      hostingSlug={null}
      hostProjects={HOST_PROJECTS}
      hostSubmitting={false}
      onHost={() => {}}
      onDismissHost={() => {}}
      onSubmitHost={async () => {}}
      {...patch}
    />
  );
}

/** Each layer with its team, health, last rebuild, freshness and shares. */
export const Full: Story = {
  render: () => <Brains />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Leo's brain")).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: /Leo's brain/ })).toHaveAttribute(
      "href",
      "/brains/leo-brain"
    );
    await expect(canvas.getByText("Not deployed")).toBeInTheDocument();
    await expect(canvas.getAllByText("Owning team only")).toHaveLength(3);
    await expect(canvas.getByText("Behind head")).toBeInTheDocument();
    await expect(canvas.getByText("Ingest failed")).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "New semantic layer" })).toHaveAttribute(
      "href",
      "/brains/new"
    );
  },
};

/** The team filter offers the teams the viewer can see; the server narrows the page. */
export const FilteredByTeam: Story = {
  render: () => <Brains />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole("button", { name: /Filter/ }));
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByRole("option", { name: /Team/ }));
    await userEvent.click(await body.findByRole("option", { name: "sre" }));
    await expect(await canvas.findByText("Operations runbooks")).toBeInTheDocument();
    await expect(canvas.queryByText("Leo's brain")).not.toBeInTheDocument();
  },
};

/** Rows no status read has reached yet: health from the deployment, freshness unknown. */
export const NotCheckedYet: Story = {
  render: () => (
    <Brains
      rows={ROWS.map((r) => ({
        ...r,
        summary: r.summary && {
          ...r.summary,
          checkedAt: null,
          compiledAt: null,
          behind: null,
          lastIngest: null,
        },
      }))}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findAllByText("Unknown")).not.toHaveLength(0);
    await expect(canvas.queryByText("Behind head")).not.toBeInTheDocument();
  },
};

/** The default view leaves archived layers out. */
export const ArchivedLeftOut: Story = {
  render: () => <Brains rows={[...ROWS, ARCHIVED_BRAIN]} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Leo's brain")).toBeInTheDocument();
    await expect(canvas.queryByText("Old brain")).not.toBeInTheDocument();
  },
};

/** With archived: an archived layer is listed with a badge and opens its page for Restore. */
export const WithArchived: Story = {
  render: () => <Brains rows={[...ROWS, ARCHIVED_BRAIN]} view="archived" />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "With archived" })).toBeInTheDocument();
    const row = within(await canvas.findByRole("row", { name: /Old brain/ }));
    await expect(row.getByText("Archived")).toBeInTheDocument();
    await expect(row.getByRole("button", { name: "Connect" })).toBeDisabled();
    await expect(canvas.getByRole("link", { name: /Old brain/ })).toHaveAttribute(
      "href",
      "/brains/old-brain"
    );
  },
};

/** More than a page: the server's cursor reaches the next one. */
export const MorePages: Story = {
  render: () => <Brains nextCursor="WyJuYW1lX2FzYyIsIkxlbyJd" totalCount={60} />,
};

/** Connect asks for a token; a layer that is not served has nothing to connect to. */
const onConnect = fn(async (_slug: string) => {});
export const Connect: Story = {
  render: () => <Brains onConnect={onConnect} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const row = async (name: RegExp) => within(await canvas.findByRole("row", { name }));
    await expect(
      (await row(/Sales brain/)).getByRole("button", { name: "Connect" })
    ).toBeDisabled();
    await userEvent.click((await row(/Leo's brain/)).getByRole("button", { name: "Connect" }));
    await expect(onConnect).toHaveBeenCalledWith("leo-brain");
  },
};

/** The issued token, shown once: the MCP URL and a client config to paste. */
export const Connected: Story = {
  render: () => <Brains connection={CONNECTION} />,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("https://brain.acme.example/mcp")).toBeInTheDocument();
    await expect(body.getByText(/X-Astrolift-Brain-Token/)).toBeInTheDocument();
  },
};

/** Host an app opens the template dialog; a layer that is not served has nothing to read. */
const onHost = fn((_slug: string) => {});
export const HostAnApp: Story = {
  render: () => <Brains onHost={onHost} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const row = async (name: RegExp) => within(await canvas.findByRole("row", { name }));
    await expect(
      (await row(/Sales brain/)).getByRole("button", { name: "Host an app" })
    ).toBeDisabled();
    await userEvent.click((await row(/Leo's brain/)).getByRole("button", { name: "Host an app" }));
    await expect(onHost).toHaveBeenCalledWith("leo-brain");
  },
};

/** The template dialog for one layer. */
export const Hosting: Story = {
  render: () => <Brains hostingSlug="leo-brain" />,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Host an app on leo-brain")).toBeInTheDocument();
  },
};

export const Connecting: Story = { render: () => <Brains connecting /> };

/** Without `app.create`: no New semantic layer and no Host an app; Connect stays. */
export const ReadOnly: Story = {
  render: () => <Brains canCreate={false} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findAllByRole("button", { name: "Connect" })).not.toHaveLength(0);
    await expect(canvas.queryByRole("button", { name: "Host an app" })).not.toBeInTheDocument();
    await expect(
      canvas.queryByRole("link", { name: "New semantic layer" })
    ).not.toBeInTheDocument();
  },
};

export const Empty: Story = { render: () => <Brains rows={[]} /> };
export const EmptyReadOnly: Story = { render: () => <Brains rows={[]} canCreate={false} /> };
export const Loading: Story = { render: () => <Brains rows={[]} loading totalCount={null} /> };
export const ReadFailed: Story = {
  render: () => <Brains rows={[]} totalCount={null} error={{ message: "Network error" }} />,
};
/** The viewer holds no `app.read` anywhere: the read is refused. */
export const Denied: Story = {
  render: () => (
    <Brains rows={[]} totalCount={null} error={{ message: "permission denied: app.read" }} />
  ),
};
export const LongStrings: Story = { render: () => <Brains rows={[LONG_BRAIN, ...ROWS]} /> };
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Brains rows={[LONG_BRAIN, ...ROWS]} />
    </div>
  ),
};
