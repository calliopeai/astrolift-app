import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { BotIcon, DownloadIcon, MoreHorizontalIcon, UsersIcon } from "lucide-react";
import * as React from "react";
import { expect, within } from "storybook/test";

import type { Column } from "@/components/data-table";
import { Identifier } from "@/components/Identifier";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { exportCsv } from "./exportCsv";
import {
  LONG_RUNS,
  MEMBERS,
  MEMBERS_LIST,
  MEMBERS_TOTAL,
  type MemberRow,
  NEW_RUNS,
  RUNS,
  RUNS_LIST,
  type RunRow,
  type RunStatus,
} from "./fixtures";
import { ListPage, type ListPageProps } from "./ListPage";
import { useHeldRows } from "./use-held-rows";
import { type ListState, useLocalListState } from "./use-list-state";

/** The list archetype (spec 44 §5.1), in every state. */
const meta: Meta = { title: "List/ListPage", parameters: { layout: "fullscreen" } };
export default meta;

const AGENT_FUNCTIONS = [
  { label: "Agents", href: "#agents" },
  { label: "Workflows", href: "#workflows" },
  { label: "Runs", href: "#runs", active: true },
  { label: "Functions", href: "#functions" },
];

const DOT: Record<RunStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  succeeded: "ok",
  failed: "error",
  waiting: "warn",
  scheduled: "muted",
};

const RUN_COLUMNS: Column<RunRow>[] = [
  {
    id: "id",
    header: "Run",
    width: "w-32",
    cell: (r) => <Identifier value={r.id} kind="sha" copyable={false} />,
  },
  {
    id: "agent",
    header: "Agent",
    sortKey: "agent",
    cellClassName: "max-w-64",
    cell: (r) => (
      <span className="block min-w-0 truncate font-mono" title={r.agent}>
        {r.agent}
      </span>
    ),
  },
  {
    id: "status",
    header: "Status",
    sortKey: "status",
    cell: (r) => (
      <span className="inline-flex items-center gap-1.5 font-mono text-xs">
        <StatusDot status={DOT[r.status]} />
        {r.status}
      </span>
    ),
  },
  {
    id: "took",
    header: "Took",
    align: "right",
    cell: (r) => <span className="font-mono tabular-nums">{r.took}</span>,
  },
  { id: "trigger", header: "Trigger", cell: (r) => r.trigger },
  {
    id: "project",
    header: "Project",
    cellClassName: "max-w-48",
    cell: (r) => (
      <span className="block min-w-0 truncate font-mono" title={r.project}>
        {r.project}
      </span>
    ),
  },
  {
    id: "started",
    header: "Started",
    sortKey: "started",
    cell: (r) => <span className="font-mono">{r.started}</span>,
  },
];

function RunCard(r: RunRow) {
  return (
    <div className="bg-card flex min-w-0 flex-col gap-2 rounded-md border p-4">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <Identifier value={r.id} kind="sha" copyable={false} />
        <span className="inline-flex shrink-0 items-center gap-1.5 font-mono text-xs">
          <StatusDot status={DOT[r.status]} />
          {r.status}
        </span>
      </div>
      <p className="min-w-0 truncate font-mono text-sm" title={r.agent}>
        {r.agent}
      </p>
      <p className="text-muted-foreground font-mono text-xs">
        {r.trigger} · {r.started} · {r.took}
      </p>
    </div>
  );
}

const RUNS_EMPTY = {
  icon: <BotIcon />,
  title: "No runs yet",
  description: "Runs appear here when an agent or workflow is triggered.",
  actionHref: "#agents",
  actionLabel: "Run an agent",
  learnMoreHref: "#docs-runs",
};

/** A routed list's props: the stories below own the page header. */
type Routed<TRow> = Partial<Extract<ListPageProps<TRow>, { header: unknown }>>;

type RunsProps = Routed<RunRow> & { initial?: Partial<ListState>; searchable?: boolean };

function Runs({ initial, rows = RUNS, searchable, ...props }: RunsProps) {
  const list = useLocalListState({ ...RUNS_LIST, searchable }, initial);
  return (
    <ListPage<RunRow>
      header={{
        crumbs: [{ label: "Agents", switcher: AGENT_FUNCTIONS }, { label: "Runs" }],
        title: "Runs",
        primaryAction: <Button size="sm">Run agent</Button>,
      }}
      list={list}
      label="Runs"
      columns={RUN_COLUMNS}
      rows={rows}
      getRowId={(r) => r.id}
      rowHref={(r) => `#run-${r.id}`}
      renderCard={RunCard}
      empty={RUNS_EMPTY}
      nextCursor="cursor-2"
      totalCount={1240}
      approximateCount
      bulkActions={() => (
        <>
          <Button size="sm" variant="outline">
            Retry
          </Button>
          <Button size="sm" variant="outline">
            Cancel
          </Button>
        </>
      )}
      rowActions={() => (
        <>
          <DropdownMenuItem>Retry</DropdownMenuItem>
          <DropdownMenuItem>Cancel</DropdownMenuItem>
        </>
      )}
      {...props}
    />
  );
}

/** Agents › Runs: cursor paged, bulk Retry and Cancel, six views. */
export const RunsFull: StoryObj = { render: () => <Runs /> };
export const BoundedSnapshotWithoutSearch: StoryObj = {
  render: () => (
    <Runs searchable={false} nextCursor={null} totalCount={RUNS.length} approximateCount={false} />
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryByRole("searchbox")).not.toBeInTheDocument();
    await expect(
      within(canvasElement).getByRole("link", { name: RUNS[0].id.split("-")[0] })
    ).toHaveAttribute("href", `#run-${RUNS[0].id}`);
  },
};

/** Filters from `+ Filter` or typed tokens, as chips; the Failed view. */
export const RunsFiltered: StoryObj = {
  render: () => (
    <Runs
      initial={{
        view: "failed",
        q: "7e11",
        filters: { agent: "support-bot", trigger: "webhook" },
        sort: [
          { key: "status", dir: "asc" },
          { key: "started", dir: "desc" },
        ],
        after: "cursor-1",
      }}
    />
  ),
};

export const RunsCards: StoryObj = {
  render: () => {
    function Cards() {
      const list = useLocalListState(RUNS_LIST);
      React.useEffect(() => list.setMode("card"), []); // eslint-disable-line react-hooks/exhaustive-deps
      return (
        <ListPage<RunRow>
          header={{ crumbs: [{ label: "Runs" }], title: "Runs" }}
          list={list}
          label="Runs"
          columns={RUN_COLUMNS}
          rows={RUNS.slice(0, 9)}
          getRowId={(r) => r.id}
          rowHref={(r) => `#run-${r.id}`}
          renderCard={RunCard}
          empty={RUNS_EMPTY}
          nextCursor="cursor-2"
        />
      );
    }
    return <Cards />;
  },
};

/** Live: new runs wait behind the pill; the rows being read hold still. */
export const RunsNewRows: StoryObj = {
  render: () => {
    function Live() {
      const [rows, setRows] = React.useState(RUNS);
      React.useEffect(() => {
        const t = setTimeout(() => setRows([...NEW_RUNS, ...RUNS]), 50);
        return () => clearTimeout(t);
      }, []);
      const held = useHeldRows(rows, (r) => r.id);
      return <Runs rows={held.rows} newRows={{ count: held.newCount, onReveal: held.reveal }} />;
    }
    return <Live />;
  },
};

export const Loading: StoryObj = { render: () => <Runs rows={[]} loading /> };
export const Empty: StoryObj = { render: () => <Runs rows={[]} /> };
export const EmptyView: StoryObj = {
  render: () => <Runs rows={[]} initial={{ view: "waiting" }} />,
};
export const EmptyFiltered: StoryObj = {
  render: () => <Runs rows={[]} initial={{ filters: { agent: "no-such-agent" } }} />,
};
export const Error: StoryObj = {
  render: () => (
    <Runs
      rows={[]}
      error={{ message: "upstream timed out after 30s (runs.list, request 01J9Z3K4X7)" }}
      onRetry={() => {}}
    />
  ),
};

/** A 64-char SHA, a 200-char ARN and an unbroken URL never widen the page. */
export const LongStrings: StoryObj = {
  render: () => (
    <Runs
      rows={LONG_RUNS}
      header={{
        crumbs: [{ label: "Agents", switcher: AGENT_FUNCTIONS }, { label: "Runs" }],
        title: LONG_RUNS[0].agent,
        primaryAction: <Button size="sm">Run agent</Button>,
      }}
      initial={{ q: LONG_RUNS[0].project, filters: { project: LONG_RUNS[0].project } }}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6): tables scroll in their frame. */
export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <div className="p-6">
        <Runs rows={LONG_RUNS} />
      </div>
    </div>
  ),
};

// ---------------------------------------------------------------------------
// Embedded: a list on a detail page's tab (an agent's Runs tab)
// ---------------------------------------------------------------------------

const AGENT_TABS = [
  "Overview",
  "Runs",
  "Configuration",
  "Skills & tools",
  "Logs & metrics",
  "Secrets",
  "Access",
  "Settings",
].map((label) => ({
  key: label,
  label,
  href: `#${label.toLowerCase()}`,
  active: label === "Runs",
}));

type EmbeddedProps = Partial<Extract<ListPageProps<RunRow>, { embedded: true }>> & {
  initial?: Partial<ListState>;
  title?: string;
};

/** The entity owns the header and the one row of tabs; the list draws none. */
function EmbeddedRuns({ initial, title = "support-bot", rows = RUNS, ...props }: EmbeddedProps) {
  const list = useLocalListState(RUNS_LIST, initial);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ShellHeader
        crumbs={[{ label: "Agents", switcher: AGENT_FUNCTIONS }, { label: title }]}
        title={title}
        status={<StatusDot status="ok" />}
        context="claude-sonnet"
        primaryAction={<Button size="sm">Run now</Button>}
        tabs={AGENT_TABS}
        tabsAriaLabel="Agent"
      />
      <ListPage<RunRow>
        embedded
        list={list}
        label="Runs"
        columns={RUN_COLUMNS}
        rows={rows}
        getRowId={(r) => r.id}
        rowHref={(r) => `#run-${r.id}`}
        renderCard={RunCard}
        empty={RUNS_EMPTY}
        nextCursor="cursor-2"
        {...props}
      />
    </div>
  );
}

/** Views as a compact picker at the start of the filter bar, still `?view=`. */
export const Embedded: StoryObj = {
  render: () => (
    <div className="p-6">
      <EmbeddedRuns />
    </div>
  ),
};
export const EmbeddedFilteredView: StoryObj = {
  render: () => (
    <div className="p-6">
      <EmbeddedRuns initial={{ view: "failed", filters: { trigger: "webhook" } }} />
    </div>
  ),
};
export const EmbeddedLoading: StoryObj = {
  render: () => (
    <div className="p-6">
      <EmbeddedRuns rows={[]} loading />
    </div>
  ),
};
export const EmbeddedEmpty: StoryObj = {
  render: () => (
    <div className="p-6">
      <EmbeddedRuns rows={[]} />
    </div>
  ),
};
export const EmbeddedError: StoryObj = {
  render: () => (
    <div className="p-6">
      <EmbeddedRuns
        rows={[]}
        error={{ message: "runs.list: upstream timed out after 30s" }}
        onRetry={() => {}}
      />
    </div>
  ),
};
export const EmbeddedLongStrings: StoryObj = {
  render: () => (
    <div className="p-6">
      <EmbeddedRuns
        rows={LONG_RUNS}
        title={LONG_RUNS[0].agent}
        initial={{ q: LONG_RUNS[0].project }}
      />
    </div>
  ),
};
export const EmbeddedWidth768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <div className="p-6">
        <EmbeddedRuns rows={LONG_RUNS} initial={{ view: "scheduled" }} />
      </div>
    </div>
  ),
};

// ---------------------------------------------------------------------------
// Members: numbered paging, CSV export in the overflow menu
// ---------------------------------------------------------------------------

const MEMBER_COLUMNS: Column<MemberRow>[] = [
  { id: "name", header: "Name", sortKey: "name", cell: (m) => m.name },
  {
    id: "email",
    header: "Email",
    sortKey: "email",
    cell: (m) => <span className="font-mono">{m.email}</span>,
  },
  {
    id: "role",
    header: "Role",
    sortKey: "role",
    cell: (m) => <span className="font-mono">{m.role}</span>,
  },
  { id: "team", header: "Team", cell: (m) => m.team },
  {
    id: "lastSeen",
    header: "Last seen",
    sortKey: "lastSeen",
    cell: (m) => <span className="font-mono">{m.lastSeen}</span>,
  },
];

function Members(props: Routed<MemberRow>) {
  const list = useLocalListState(MEMBERS_LIST, { page: 2 });
  return (
    <ListPage<MemberRow>
      header={{
        crumbs: [{ label: "Admin" }, { label: "Members" }],
        title: "Members",
        primaryAction: <Button size="sm">Invite</Button>,
      }}
      list={list}
      label="Members"
      columns={MEMBER_COLUMNS}
      rows={MEMBERS}
      getRowId={(m) => m.id}
      rowHref={(m) => `#member-${m.id}`}
      empty={{
        icon: <UsersIcon />,
        title: "No members yet",
        actionHref: "#invite",
        actionLabel: "Invite a member",
      }}
      totalCount={MEMBERS_TOTAL}
      menu={
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="More">
              <MoreHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() =>
                exportCsv("members", MEMBERS, [
                  { header: "Name", value: (m) => m.name },
                  { header: "Email", value: (m) => m.email },
                  { header: "Role", value: (m) => m.role },
                  { header: "Team", value: (m) => m.team },
                ])
              }
            >
              <DownloadIcon className="size-4" />
              Export CSV
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      }
      {...props}
    />
  );
}

/** Admin › Members: numbered pages, CSV export from `⋯`. */
export const MembersNumbered: StoryObj = { render: () => <Members /> };
export const MembersLoading: StoryObj = { render: () => <Members rows={[]} loading /> };
