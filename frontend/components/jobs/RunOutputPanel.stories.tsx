import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { RunOutputPanel } from "@/components/jobs/RunOutputPanel";

const meta: Meta<typeof RunOutputPanel> = {
  title: "Patterns/Jobs/RunOutputPanel",
  component: RunOutputPanel,
};
export default meta;
export const Default: StoryObj<typeof RunOutputPanel> = {
  args: {
    output:
      "12:01:04 pulling image…\n12:01:09 started\n12:01:55 timeout after 45s\n12:01:55 run failed: tool timeout",
    consoleHref: "#",
    caption: "Last 4 lines",
  },
};
