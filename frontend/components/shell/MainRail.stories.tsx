import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, within } from "storybook/test";

import { MainRail } from "@/components/shell/MainRail";
import { NAV, type ModuleKey, visibleNav } from "@/lib/shell/nav-model";

/** The main rail (spec 44 §4.1, §4.2), for each kind of access. */
const meta: Meta = { title: "Shell/MainRail", parameters: { layout: "fullscreen" } };
export default meta;

const allowed =
  (...keys: ModuleKey[]) =>
  (m: ModuleKey) =>
    keys.includes(m);

function Frame({
  modules,
  activeFn = "runs",
  area = "agents",
  startCollapsed = false,
}: {
  modules: ModuleKey[];
  activeFn?: string;
  area?: "home" | "agents" | "apps" | "admin";
  startCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = React.useState(startCollapsed);
  return (
    <div className="bg-background flex h-[640px]">
      <MainRail
        nav={visibleNav(NAV, allowed(...modules))}
        active={{ area, fn: activeFn }}
        collapsed={collapsed}
        onCollapsedChange={setCollapsed}
        header={<span className="font-head truncate text-sm font-semibold">Astrolift</span>}
        footer={
          <span className="text-muted-foreground truncate px-2 text-xs">leo@example.com</span>
        }
      />
      <div className="text-muted-foreground p-6 text-sm">Content</div>
    </div>
  );
}

export const Everything: StoryObj = {
  render: () => <Frame modules={["apps", "agents", "workflows", "models", "admin"]} />,
};
export const AppsOnly: StoryObj = {
  render: () => <Frame modules={["apps"]} area="apps" activeFn="deployments" />,
};
export const AgentsOnly: StoryObj = { render: () => <Frame modules={["agents"]} /> };
export const ModelsOnly: StoryObj = {
  render: () => <Frame modules={["models"]} activeFn="models" />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Models" })).toHaveAttribute("href", "/models");
    await expect(canvas.queryByRole("link", { name: "Workloads" })).not.toBeInTheDocument();
  },
};
export const Collapsed: StoryObj = {
  render: () => (
    <Frame modules={["apps", "agents", "workflows", "models", "admin"]} startCollapsed />
  ),
};

/** Operational pages must have a home instead of requiring a remembered URL. */
export const OperationalPages: StoryObj = {
  render: () => <Frame modules={["apps", "agents", "workflows", "models", "admin"]} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const name of [
      "Fleet",
      "Approvals",
      "Deployment approvals",
      "Operations",
      "Pipelines",
      "Logs",
      "Traces",
    ]) {
      await expect(canvas.getByRole("link", { name })).toBeVisible();
    }
  },
};
