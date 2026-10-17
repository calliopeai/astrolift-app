import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";
import { selectRows } from "@/components/list/select-rows";

import { type BrainStatus, statusEnvironment } from "./brain-status-model";
import { BEHIND, DEPLOY_FAILED, DEPLOYING, HEALTHY, NOT_DEPLOYED } from "./brain-status.fixtures";
import { BRAINS, CONNECTION, HOST_PROJECTS, LONG_BRAIN } from "./brains.fixtures";
import { BRAINS_LIST, BRAINS_SELECT, brainTeams, withTeamOptions } from "./brains-list";
import { BrainsScreen, type BrainsScreenProps } from "./BrainsScreen";
import { type BrainStatusResult, BrainStatusCell } from "./BrainStatusCell";
import type { BrainRow } from "./use-brains";

const meta: Meta = {
  title: "Screens/Brains/BrainsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const READY = (status: BrainStatus | null): BrainStatusResult => ({ state: "ready", status });

/** One row per state the status columns draw. */
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
  },
];

const STATUS: Record<string, BrainStatusResult> = {
  "leo-brain": READY(HEALTHY),
  "sales-brain": READY(NOT_DEPLOYED),
  "ops-brain": READY(DEPLOY_FAILED),
  "research-brain": READY(DEPLOYING),
  "finance-brain": READY(BEHIND),
};

function cellFor(statuses: Record<string, BrainStatusResult>): BrainsScreenProps["statusCell"] {
  function StatusCell(row: BrainRow, part: Parameters<BrainsScreenProps["statusCell"]>[1]) {
    return (
      <BrainStatusCell
        part={part}
        result={statuses[row.slug] ?? READY(null)}
        environment={statusEnvironment(row.environments)}
      />
    );
  }
  return StatusCell;
}

function Brains({
  rows: all = ROWS,
  statuses = STATUS,
  ...patch
}: Partial<Omit<BrainsScreenProps, "list" | "rows">> & {
  rows?: BrainRow[];
  statuses?: Record<string, BrainStatusResult>;
}) {
  const list = useLocalListState(withTeamOptions(BRAINS_LIST, brainTeams(all)));
  const { rows, totalCount } = selectRows(
    all,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    BRAINS_SELECT
  );
  return (
    <BrainsScreen
      list={list}
      rows={rows}
      totalCount={totalCount}
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
      statusCell={cellFor(statuses)}
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

/** The team filter offers the teams of the rows in hand. */
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

/** Status reads still in flight, and one that failed, beside answered rows. */
export const PartialStatus: Story = {
  render: () => (
    <Brains
      statuses={{
        ...STATUS,
        "leo-brain": { state: "loading" },
        "ops-brain": { state: "error", message: "source head: the host answered 502" },
      }}
    />
  ),
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
