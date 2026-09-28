import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, QUICK_LINKS } from "./app-detail-shell.fixtures";
import { QuickLinksGrid } from "./QuickLinksGrid";

const meta: Meta<typeof QuickLinksGrid> = {
  title: "Screens/Apps/Detail/QuickLinksGrid",
  component: QuickLinksGrid,
  args: QUICK_LINKS,
};
export default meta;

type Story = StoryObj<typeof QuickLinksGrid>;

export const Full: Story = {};

/** First load: the count chip is held back until the query answers. */
export const Loading: Story = {
  args: { count: 0, loading: true },
};

/** No deployments yet. The grid has no error state; a failed count reads as zero. */
export const Empty: Story = {
  args: { count: 0 },
};

export const SingleDeployment: Story = {
  args: { count: 1 },
};

/** Titles and copy are fixed; a long slug only lengthens the hrefs. */
export const LongStrings: Story = {
  args: { appHref: `/apps/${LONG}`, count: 123456 },
};

export const At768: Story = {
  args: { appHref: `/apps/${LONG}`, count: 123456 },
  render: (args) => (
    <div style={{ width: 768 }}>
      <QuickLinksGrid {...args} />
    </div>
  ),
};
