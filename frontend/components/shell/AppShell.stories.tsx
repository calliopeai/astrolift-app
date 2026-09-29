import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ChevronsUpDownIcon, MoreHorizontalIcon } from "lucide-react";
import * as React from "react";

import { PlatformIncidentBanner } from "@/components/PlatformIncidentBanner";
import { AppShell } from "@/components/shell/AppShell";
import { PROJECTS } from "@/components/shell/fixtures";
import { MainRail } from "@/components/shell/MainRail";
import { ProjectsRail } from "@/components/shell/ProjectsRail";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { NAV, visibleNav } from "@/lib/shell/nav-model";

/** Layout D, whole (spec 44 §4.2): main rail, content, projects rail. Try `[` and `]`. */
const meta: Meta = { title: "Shell/AppShell", parameters: { layout: "fullscreen" } };
export default meta;

const APP_FUNCTIONS = [
  { label: "Apps", href: "/apps", active: true },
  { label: "Deployments", href: "/deployments" },
  { label: "Scheduled jobs", href: "/jobs" },
];

function Demo({
  banner = false,
  startProjectsCollapsed = false,
}: {
  banner?: boolean;
  startProjectsCollapsed?: boolean;
}) {
  const [mainCollapsed, setMainCollapsed] = React.useState(false);
  const [projectsCollapsed, setProjectsCollapsed] = React.useState(startProjectsCollapsed);
  return (
    <AppShell
      className="h-[720px]"
      onToggleMain={() => setMainCollapsed((c) => !c)}
      onToggleProjects={() => setProjectsCollapsed((c) => !c)}
      banner={
        banner ? (
          <PlatformIncidentBanner
            statusPageUrl="https://status.example.com"
            incidents={[
              {
                id: "1",
                name: "Deploys failing in us-west-2",
                status: "investigating",
                impact: "major",
                shortlink: "#",
                resolved_at: null,
              },
            ]}
            onDismiss={() => {}}
          />
        ) : undefined
      }
      mainRail={
        <MainRail
          nav={visibleNav(NAV, () => true)}
          active={{ area: "apps", fn: "apps" }}
          collapsed={mainCollapsed}
          onCollapsedChange={setMainCollapsed}
          header={<span className="font-head truncate text-sm font-semibold">Astrolift</span>}
          footer={
            <span className="text-muted-foreground truncate px-2 text-xs">leo@example.com</span>
          }
        />
      }
      projectsRail={
        <ProjectsRail
          org={
            <span className="flex items-center gap-1 text-sm font-semibold">
              CONFLICT <ChevronsUpDownIcon className="text-muted-foreground size-3.5" />
            </span>
          }
          projects={PROJECTS}
          activeHref="/apps/checkout"
          allProjectsHref="/projects"
          collapsed={projectsCollapsed}
          onCollapsedChange={setProjectsCollapsed}
        />
      }
    >
      <ShellHeader
        crumbs={[{ label: "Apps", switcher: APP_FUNCTIONS }, { label: "checkout" }]}
        title="checkout"
        status={
          <span className="inline-flex items-center gap-1.5 text-sm">
            <StatusDot status="ok" /> running
          </span>
        }
        context="production · us-west-2"
        primaryAction={<Button size="sm">Deploy</Button>}
        menu={
          <Button size="icon" variant="ghost" aria-label="More">
            <MoreHorizontalIcon />
          </Button>
        }
        tabs={[
          "Overview",
          "Deployments",
          "Workloads",
          "Logs & metrics",
          "Domains",
          "Secrets",
          "Access",
          "Settings",
        ].map((label, i) => ({ key: label, label, href: "#", active: i === 0 }))}
      />
      <div className="text-muted-foreground mt-6 text-sm">Page content.</div>
    </AppShell>
  );
}

export const BothOpen: StoryObj = { render: () => <Demo /> };
export const ProjectsCollapsed: StoryObj = { render: () => <Demo startProjectsCollapsed /> };
export const WithIncident: StoryObj = { render: () => <Demo banner /> };
