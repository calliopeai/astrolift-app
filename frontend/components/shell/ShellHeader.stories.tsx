import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { MoreHorizontalIcon } from "lucide-react";

import { Identifier } from "@/components/Identifier";
import { RunStatusBadge } from "@/components/jobs/RunStatusBadge";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";

/** Every page's header (spec 44 §4.4): breadcrumb, title row, one row of tabs. */
const meta: Meta<typeof ShellHeader> = { title: "Shell/ShellHeader", component: ShellHeader };
export default meta;

const AGENT_FUNCTIONS = [
  { label: "Agents", href: "/agents" },
  { label: "Workflows", href: "/workflows" },
  { label: "Runs", href: "/tasks", active: true },
  { label: "Functions", href: "/functions" },
  { label: "Skills", href: "/agents/skills" },
];

const tab = (label: string, active = false) => ({ key: label, label, href: "#", active });
const MENU = (
  <Button size="icon" variant="ghost" aria-label="More">
    <MoreHorizontalIcon />
  </Button>
);

/** A list page: the tabs are views. */
export const ListPage: StoryObj = {
  render: () => (
    <ShellHeader
      crumbs={[{ label: "Agents", switcher: AGENT_FUNCTIONS }, { label: "Runs" }]}
      title="Runs"
      primaryAction={<Button size="sm">+ Run agent</Button>}
      tabs={["All", "Mine", "Running", "Failed", "Waiting", "Scheduled"].map((v, i) =>
        tab(v, i === 0)
      )}
      tabsAriaLabel="Views"
    />
  ),
};

/** A detail page: the tabs are the entity's own. */
export const DetailPage: StoryObj = {
  render: () => (
    <ShellHeader
      crumbs={[
        {
          label: "Agents",
          switcher: AGENT_FUNCTIONS.map((f) => ({ ...f, active: f.label === "Agents" })),
        },
        { label: "support-bot" },
      ]}
      title="support-bot"
      status={
        <span className="inline-flex items-center gap-1.5 text-sm">
          <StatusDot status="ok" /> ready
        </span>
      }
      context="claude-sonnet · 3 skills"
      primaryAction={<Button size="sm">Run now</Button>}
      menu={MENU}
      tabs={[
        "Overview",
        "Runs",
        "Configuration",
        "Skills & tools",
        "Logs & metrics",
        "Secrets",
        "Access",
        "Settings",
      ].map((t, i) => tab(t, i === 0))}
      tabsAriaLabel="support-bot"
    />
  ),
};

/** Entered from the projects rail: the first crumb is the project, switching projects. */
export const FromProjects: StoryObj = {
  render: () => (
    <ShellHeader
      crumbs={[
        {
          label: "storefront",
          switcher: [
            { label: "storefront", href: "/projects/storefront", active: true },
            { label: "data-platform", href: "/projects/data-platform" },
          ],
        },
        { label: "checkout" },
      ]}
      title="checkout"
      context="production · us-west-2"
      primaryAction={<Button size="sm">Deploy</Button>}
      menu={MENU}
      tabs={[
        "Overview",
        "Deployments",
        "Workloads",
        "Logs & metrics",
        "Domains",
        "Secrets",
        "Access",
        "Settings",
      ].map((t, i) => tab(t, i === 0))}
    />
  ),
};

/** A run: four crumbs, no tabs, the reason in the title row. */
export const RunPage: StoryObj = {
  render: () => (
    <ShellHeader
      crumbs={[
        { label: "Agents", switcher: AGENT_FUNCTIONS },
        { label: "support-bot", href: "#" },
        { label: "Runs", href: "#" },
        { label: "7e11…" },
      ]}
      title={
        <span className="inline-flex items-center gap-2">
          run <Identifier value="7e11b0c4-5d6e-4f70-8a9b-0c1d2e3f4a5b" kind="id" />
        </span>
      }
      status={<RunStatusBadge status="failed" />}
      context="1:10 · webhook · tool timeout after 45s"
      primaryAction={<Button size="sm">Retry</Button>}
      menu={MENU}
    />
  ),
};

/** A long title at 768px: it truncates, the actions stay. */
export const Narrow: StoryObj = {
  render: () => (
    <div className="w-[768px]">
      <ShellHeader
        crumbs={[
          { label: "Apps", switcher: [{ label: "Apps", href: "/apps", active: true }] },
          { label: "x" },
        ]}
        title="an-app-with-a-name-long-enough-to-run-past-the-edge-of-the-content-column"
        context="production · us-west-2"
        primaryAction={<Button size="sm">Deploy</Button>}
        menu={MENU}
      />
    </div>
  ),
};
