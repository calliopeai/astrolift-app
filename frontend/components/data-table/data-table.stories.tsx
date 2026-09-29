import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { BotIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

import { DataTable } from "./data-table";
import { fakeController, RUNS, type RunRow } from "./fixtures";
import type { Column } from "./types";
import type { CursorTableController } from "./use-cursor-table";
import { useRowSelection } from "./use-row-selection";
import { RunStatusBadge } from "../jobs/RunStatusBadge";

/** The one list (spec 44 §5.1), in every state. */
const meta: Meta = { title: "Patterns/DataTable", parameters: { layout: "padded" } };
export default meta;

const COLUMNS: Column<RunRow>[] = [
  { id: "id", header: "Run", cell: (r) => <span className="font-mono">{r.id}…</span> },
  { id: "agent", header: "Agent", cell: (r) => r.agent, sortKey: "agent" },
  { id: "status", header: "Status", cell: (r) => <RunStatusBadge status={r.status} /> },
  {
    id: "took",
    header: "Took",
    cell: (r) => <span className="font-mono">{r.took}</span>,
    align: "right",
  },
  { id: "trigger", header: "Trigger", cell: (r) => r.trigger },
  { id: "started", header: "Started", cell: (r) => r.started, sortKey: "started" },
];

const EMPTY = {
  icon: <BotIcon />,
  title: "No runs yet",
  description: "Runs appear here when an agent is triggered.",
  actionHref: "#",
  actionLabel: "Run an agent",
};

function Table({
  state,
  rows = RUNS,
}: {
  state: Partial<CursorTableController<RunRow>>;
  rows?: RunRow[];
}) {
  return (
    <DataTable
      label="Runs"
      controller={fakeController<RunRow>({ rows, ...state })}
      columns={COLUMNS}
      getRowId={(r) => r.id}
      rowHref={(r) => `#${r.id}`}
      empty={EMPTY}
      emptyFiltered={{ title: "No runs match", description: "Clear a filter to see more." }}
      searchPlaceholder="Search runs, ids…"
    />
  );
}

export const Ready: StoryObj = { render: () => <Table state={{ totalCount: 4 }} /> };
export const Loading: StoryObj = { render: () => <Table state={{ state: "loading" }} rows={[]} /> };
export const Empty: StoryObj = { render: () => <Table state={{ state: "empty" }} rows={[]} /> };
export const EmptyFiltered: StoryObj = {
  render: () => (
    <Table state={{ state: "emptyFiltered", isFiltered: true, search: "zzz" }} rows={[]} />
  ),
};
export const Error: StoryObj = {
  render: () => (
    <Table
      state={{ state: "error", error: new globalThis.Error("upstream timed out") }}
      rows={[]}
    />
  ),
};
export const Paged: StoryObj = {
  render: () => <Table state={{ totalCount: 140, hasNext: true, pageIndex: 1, hasPrev: true }} />,
};

function WithSelectionDemo() {
  const selection = useRowSelection();
  return (
    <DataTable
      label="Runs"
      controller={fakeController<RunRow>({ rows: RUNS })}
      columns={COLUMNS}
      getRowId={(r) => r.id}
      rowHref={(r) => `#${r.id}`}
      empty={EMPTY}
      selection={selection}
      bulkActions={(s) => (
        <Button size="sm" variant="outline">
          Retry {s.selectedCount}
        </Button>
      )}
    />
  );
}
export const WithSelection: StoryObj = { render: () => <WithSelectionDemo /> };

export const LongValues: StoryObj = {
  render: () => (
    <div className="w-[768px]">
      <Table
        state={{}}
        rows={RUNS.map((r) => ({
          ...r,
          agent: `${r.agent}-with-a-very-long-name-that-does-not-break`,
        }))}
      />
    </div>
  ),
};
