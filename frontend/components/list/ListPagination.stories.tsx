import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { ListPagination } from "./ListPagination";

/** Numbered and cursor paging (spec 44 §5.1). */
const meta: Meta = { title: "List/ListPagination", parameters: { layout: "padded" } };
export default meta;

const SIZES = [25, 50, 100];
const noop = () => {};

function NumberedDemo({ total = 140, start = 1 }: { total?: number; start?: number }) {
  const [page, setPage] = React.useState(start);
  const [size, setSize] = React.useState(25);
  return (
    <ListPagination
      mode="numbered"
      page={page}
      totalCount={total}
      onPage={setPage}
      pageSize={size}
      pageSizes={SIZES}
      onPageSize={setSize}
    />
  );
}

/** `1–25 of 140   ‹ 1 2 3 … 6 ›   25 / page` */
export const Numbered: StoryObj = { render: () => <NumberedDemo /> };
export const NumberedMiddle: StoryObj = { render: () => <NumberedDemo total={1400} start={27} /> };
export const NumberedOnePage: StoryObj = { render: () => <NumberedDemo total={7} /> };
export const NumberedEmpty: StoryObj = { render: () => <NumberedDemo total={0} /> };

/** `Showing 25 · newest first   ‹ Newer  Older ›` */
export const Cursor: StoryObj = {
  render: () => (
    <ListPagination
      mode="cursor"
      shown={25}
      order="newest first"
      hasNewer={false}
      hasOlder
      onNewer={noop}
      onOlder={noop}
      pageSize={25}
      pageSizes={SIZES}
      onPageSize={noop}
    />
  ),
};

export const CursorApproximateCount: StoryObj = {
  render: () => (
    <ListPagination
      mode="cursor"
      shown={25}
      order="newest first"
      hasNewer
      hasOlder
      onNewer={noop}
      onOlder={noop}
      totalCount={1240}
      approximate
      pageSize={25}
      pageSizes={SIZES}
      onPageSize={noop}
    />
  ),
};

export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="border p-4">
      <NumberedDemo total={123456} start={2000} />
    </div>
  ),
};
