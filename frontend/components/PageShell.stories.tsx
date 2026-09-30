import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import Link from "next/link";
import { AppChromeProvider } from "@/app/(app)/apps/[slug]/components/app-chrome-context";
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

/** Project headers put real breadcrumb navigation in the description slot. */
export const BreadcrumbDescription: StoryObj = {
  render: () => (
    <PageShell
      title="Project"
      description={
        <nav aria-label="Project breadcrumb">
          <ol>
            <li>
              <Link href="/projects">Projects</Link>
            </li>
          </ol>
        </nav>
      }
    >
      <p>Project workloads.</p>
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

/** Inside the app frame: no title (the frame names the app), description and actions stay. */
export const InAppFrame: StoryObj = {
  render: () => (
    <AppChromeProvider framed>
      <PageShell
        title="Secrets"
        description="Per-environment secrets, injected at deploy time."
        actions={<Button size="sm">Add secret</Button>}
      >
        <p className="text-sm">Page content.</p>
      </PageShell>
    </AppChromeProvider>
  ),
};
