import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import type { Principal } from "./access-model";
import { DANA, LONG_PRINCIPAL, SAM } from "./fixtures";
import { PrincipalPicker, type PrincipalPickerProps } from "./PrincipalPicker";

/** One principal, picked: the chip with Change, or a search with quick picks. */
const meta: Meta = { title: "Access/PrincipalPicker", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

const ME: Principal = { kind: "user", id: "7", name: "Ada Park", detail: "ada@example.com" };
const POOL = [DANA, SAM, ME];

function Picker({
  initial = null,
  pool = POOL,
  ...rest
}: Partial<PrincipalPickerProps> & { initial?: Principal | null; pool?: Principal[] }) {
  const [value, setValue] = React.useState<Principal | null>(initial);
  const [query, setQuery] = React.useState("");
  const q = query.trim().toLowerCase();
  const results = q ? pool.filter((p) => p.name.toLowerCase().includes(q)) : [];
  return (
    <div className="max-w-sm">
      <PrincipalPicker
        label="Who"
        value={value}
        onChange={setValue}
        search={{ query, setQuery, results }}
        quickPicks={[{ label: "Me", principal: ME }]}
        {...rest}
      />
    </div>
  );
}

export const Picked: Story = { render: () => <Picker initial={DANA} /> };

/** Nothing picked: Me, and the search. */
export const NothingPicked: Story = { render: () => <Picker /> };

/** Typing narrows; a click picks. */
export const Searching: Story = {
  render: () => <Picker />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.type(c.getByRole("searchbox"), "sa");
    await userEvent.click(c.getByRole("button", { name: "Pick Sam Okafor" }));
    await expect(c.getByText("Sam Okafor")).toBeInTheDocument();
  },
};

export const NoMatch: Story = {
  render: () => (
    <PrincipalPicker
      label="Who"
      value={null}
      onChange={() => {}}
      search={{ query: "zzz", setQuery: () => {}, results: [] }}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <PrincipalPicker
      label="Who"
      value={null}
      onChange={() => {}}
      search={{ query: "da", setQuery: () => {}, results: [], loading: true }}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <PrincipalPicker
      label="Who"
      value={null}
      onChange={() => {}}
      search={{
        query: "da",
        setQuery: () => {},
        results: [],
        error: { message: "Network error: failed to fetch" },
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Picker initial={{ ...LONG_PRINCIPAL, kind: "user" }} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Picker initial={{ ...LONG_PRINCIPAL, kind: "user" }} />
    </div>
  ),
};
