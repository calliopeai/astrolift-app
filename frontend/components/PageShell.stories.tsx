import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";

import { PageShell } from "@/components/PageShell";

/** Today's page header; the shell's three-row header (spec 44 §4.4) replaces it. */
const meta: Meta<typeof PageShell> = { title: "Patterns/PageShell", component: PageShell };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <PageShell
      title="Runs"
      description="Every agent and workflow run."
      actions={<Button size="sm">+ Run agent</Button>}
    >
      <p className="text-sm">Page content.</p>
    </PageShell>
  ),
};

export const LongTitle: StoryObj = {
  render: () => (
    <div className="w-[768px]">
      <PageShell
        title="support-bot-with-a-very-long-name-that-keeps-going-past-the-edge"
        actions={<Button size="sm">Run now</Button>}
      >
        <p className="text-sm">Page content.</p>
      </PageShell>
    </div>
  ),
};
