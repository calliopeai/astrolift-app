import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { DataTablePagination } from "./data-table-pagination";
import { fakeController } from "./fixtures";

const meta: Meta = { title: "Patterns/DataTable/Pagination" };
export default meta;

export const FirstPage: StoryObj = {
  render: () => (
    <DataTablePagination controller={fakeController({ totalCount: 140, hasNext: true })} />
  ),
};
export const MiddlePage: StoryObj = {
  render: () => (
    <DataTablePagination
      controller={fakeController({ totalCount: 140, hasNext: true, hasPrev: true, pageIndex: 2 })}
    />
  ),
};
export const UnknownTotal: StoryObj = {
  render: () => (
    <DataTablePagination controller={fakeController({ hasNext: true, hasPrev: true })} />
  ),
};
