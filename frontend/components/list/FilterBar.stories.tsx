import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { MoreHorizontalIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";

import { FilterBar, type FilterBarColumn } from "./FilterBar";
import { LONG_ARN, LONG_URL, RUNS_LIST } from "./fixtures";
import { type ListState, formatSort, useLocalListState } from "./use-list-state";

/** The one filter bar (spec 44 §5.1): search, + Filter chips, sort, columns, list|cards. */
const meta: Meta = { title: "List/FilterBar", parameters: { layout: "padded" } };
export default meta;

const COLUMNS: FilterBarColumn[] = [
  { id: "id", label: "Run", hideable: false },
  { id: "agent", label: "Agent", sortKey: "agent" },
  { id: "status", label: "Status", sortKey: "status" },
  { id: "took", label: "Took" },
  { id: "started", label: "Started", sortKey: "started" },
];

function Bar({ initial, card = false }: { initial?: Partial<ListState>; card?: boolean }) {
  const list = useLocalListState(RUNS_LIST, initial);
  React.useEffect(() => {
    if (card) list.setMode("card");
  }, [card]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <FilterBar
        list={list}
        columns={COLUMNS}
        menu={
          <Button variant="ghost" size="icon" aria-label="More">
            <MoreHorizontalIcon className="size-4" />
          </Button>
        }
      />
      <p className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
        q={list.state.q || "∅"} filters={JSON.stringify(list.filters)} sort=
        {formatSort(list.state.sort)}
      </p>
    </div>
  );
}

/** Try typing `status:failed ` or `agent:support-bot` and a space, or press `/`. */
export const Default: StoryObj = { render: () => <Bar /> };

export const WithChips: StoryObj = {
  render: () => (
    <Bar initial={{ q: "7e11", filters: { status: "failed", agent: "support-bot" } }} />
  ),
};

/** Card view has no headers, so the sort menu appears, on the same sort state. */
export const CardViewSort: StoryObj = { render: () => <Bar card /> };

export const LongStrings: StoryObj = {
  render: () => <Bar initial={{ q: LONG_URL, filters: { agent: LONG_ARN, project: LONG_URL } }} />,
};

export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="border p-4">
      <Bar initial={{ filters: { status: "failed", agent: "support-bot", trigger: "webhook" } }} />
    </div>
  ),
};
