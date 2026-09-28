import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { SortState } from "./types";
import { SortableColumnHeader } from "./sortable-column-header";

const meta: Meta = { title: "Patterns/DataTable/SortableColumnHeader" };
export default meta;

function Demo() {
  const [sort, setSort] = React.useState<SortState | undefined>({ key: "started", dir: "desc" });
  const toggle = (key: string) =>
    setSort((s) =>
      s?.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }
    );
  return (
    <div className="flex gap-6">
      <SortableColumnHeader sortKey="agent" sort={sort} onToggle={toggle}>
        Agent
      </SortableColumnHeader>
      <SortableColumnHeader sortKey="started" sort={sort} onToggle={toggle}>
        Started
      </SortableColumnHeader>
    </div>
  );
}
export const Default: StoryObj = { render: () => <Demo /> };
