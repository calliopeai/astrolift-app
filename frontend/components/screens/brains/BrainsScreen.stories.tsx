import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { BRAINS, LONG_BRAIN } from "./brains.fixtures";
import { BRAINS_LIST } from "./brains-list";
import { BrainsScreen, type BrainsScreenProps } from "./BrainsScreen";
import type { BrainRow } from "./use-brains";

const meta: Meta = {
  title: "Screens/Brains/BrainsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Brains({
  rows = BRAINS,
  ...patch
}: Partial<Omit<BrainsScreenProps, "list">> & { rows?: BrainRow[] }) {
  const list = useLocalListState(BRAINS_LIST);
  const q = list.state.q.trim().toLowerCase();
  const shown = q ? rows.filter((b) => `${b.name} ${b.slug}`.toLowerCase().includes(q)) : rows;
  return (
    <BrainsScreen
      list={list}
      rows={shown}
      totalCount={shown.length}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      {...patch}
    />
  );
}

/** Where each layer is served and who it is shared with. */
export const Full: Story = {
  render: () => <Brains />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Leo's brain")).toBeInTheDocument();
    await expect(canvas.getByText("production: https://brain.acme.example")).toBeInTheDocument();
    await expect(canvas.getByText("Not served yet")).toBeInTheDocument();
    await expect(canvas.getByText("Owning team only")).toBeInTheDocument();
  },
};

export const Empty: Story = { render: () => <Brains rows={[]} /> };
export const Loading: Story = { render: () => <Brains rows={[]} loading totalCount={null} /> };
export const ReadFailed: Story = {
  render: () => <Brains rows={[]} totalCount={null} error={{ message: "Network error" }} />,
};
export const LongStrings: Story = { render: () => <Brains rows={[LONG_BRAIN, ...BRAINS]} /> };
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Brains rows={[LONG_BRAIN, ...BRAINS]} />
    </div>
  ),
};
