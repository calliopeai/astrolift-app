import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";

import { DataTableToolbar } from "./data-table-toolbar";
import { fakeController } from "./fixtures";

const meta: Meta = { title: "Patterns/DataTable/Toolbar" };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <DataTableToolbar controller={fakeController()} searchPlaceholder="Search runs, ids…">
      <Button size="sm" variant="outline">
        + Filter
      </Button>
    </DataTableToolbar>
  ),
};

export const Searching: StoryObj = {
  render: () => (
    <DataTableToolbar controller={fakeController({ search: "7e11", isSearching: true })} />
  ),
};
