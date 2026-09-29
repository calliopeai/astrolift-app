import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ChevronsUpDownIcon } from "lucide-react";
import * as React from "react";

import { PROJECTS } from "@/components/shell/fixtures";
import { ProjectsRail } from "@/components/shell/ProjectsRail";

/** The projects rail (spec 44 §4.2), on the right. */
const meta: Meta = { title: "Shell/ProjectsRail", parameters: { layout: "fullscreen" } };
export default meta;

const ORG = (
  <button
    type="button"
    className="flex w-full min-w-0 items-center gap-1 text-left text-sm font-semibold"
  >
    <span className="truncate">CONFLICT</span>
    <ChevronsUpDownIcon className="text-muted-foreground size-3.5 shrink-0" />
  </button>
);

function Frame(props: Partial<React.ComponentProps<typeof ProjectsRail>>) {
  const [collapsed, setCollapsed] = React.useState(props.collapsed ?? false);
  return (
    <div className="bg-background flex h-[560px] justify-end">
      <ProjectsRail
        org={ORG}
        projects={PROJECTS}
        activeHref="/apps/checkout"
        allProjectsHref="/projects"
        {...props}
        collapsed={collapsed}
        onCollapsedChange={setCollapsed}
      />
    </div>
  );
}

export const Default: StoryObj = { render: () => <Frame /> };
export const Loading: StoryObj = { render: () => <Frame loading projects={[]} /> };
export const NoProjects: StoryObj = { render: () => <Frame projects={[]} /> };
export const Collapsed: StoryObj = { render: () => <Frame collapsed /> };
export const LongNames: StoryObj = {
  render: () => (
    <Frame
      activeHref="/apps/x"
      projects={[
        {
          slug: "p",
          name: "a-project-with-a-very-long-name-that-runs-on",
          entities: [
            {
              kind: "app",
              key: "x",
              name: "an-app-name-that-is-longer-than-the-rail-is-wide",
              href: "/apps/x",
              status: "ok",
            },
          ],
        },
      ]}
    />
  ),
};
