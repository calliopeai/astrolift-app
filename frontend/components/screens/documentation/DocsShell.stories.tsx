import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_NAV_SECTIONS } from "./docs-a.fixtures";
import { DocsShell } from "./DocsShell";
import { IntroductionScreen } from "./IntroductionScreen";

const meta: Meta<typeof DocsShell> = {
  title: "Screens/Documentation/DocsShell",
  component: DocsShell,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof DocsShell>;

/** The nav shows from the lg breakpoint; widen the canvas to see it. */
export const Full: Story = {
  args: { pathname: "/documentation/introduction", children: <IntroductionScreen /> },
};

/** A nested page keeps its parent link active. */
export const NestedActive: Story = {
  args: { pathname: "/documentation/runbooks/rollback", children: <IntroductionScreen /> },
};

/** The index: no link is active. */
export const NoneActive: Story = {
  args: { pathname: "/documentation", children: <IntroductionScreen /> },
};

export const LongStrings: Story = {
  args: {
    pathname: "/documentation/long-guide",
    sections: LONG_NAV_SECTIONS,
    children: <IntroductionScreen />,
  },
};
