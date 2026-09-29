import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DetailPageTabs } from "@/components/DetailPageTabs";

/** One row of tabs named by function (spec 44 §5.2). */
const meta: Meta<typeof DetailPageTabs> = {
  title: "Primitives/DetailPageTabs",
  component: DetailPageTabs,
};
export default meta;

const tabs = (active: string, labels: string[]) =>
  labels.map((label) => ({ key: label, label, href: "#", active: label === active }));

export const App: StoryObj<typeof DetailPageTabs> = {
  args: {
    ariaLabel: "App pages",
    tabs: tabs("Overview", [
      "Overview",
      "Deployments",
      "Workloads",
      "Logs & metrics",
      "Domains",
      "Secrets",
      "Access",
      "Settings",
    ]),
  },
};

/** At 768px the row scrolls inside itself; the page never widens. */
export const Narrow: StoryObj<typeof DetailPageTabs> = {
  args: App.args,
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};

export const LongLabel: StoryObj<typeof DetailPageTabs> = {
  args: {
    ariaLabel: "Tabs",
    tabs: tabs("a", ["a".repeat(90), "Deployments"]).map((t, i) => ({ ...t, active: i === 0 })),
  },
};
