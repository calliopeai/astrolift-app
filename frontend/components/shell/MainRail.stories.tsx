import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, within } from "storybook/test";

import { MainRail } from "@/components/shell/MainRail";
import { NAV, type ModuleKey, type NavRequirement, visibleNav } from "@/lib/shell/nav-model";

/** The main rail (spec 44 §4.1, §4.2), for each kind of access. */
const meta: Meta = { title: "Shell/MainRail", parameters: { layout: "fullscreen" } };
export default meta;

const allowed =
  (...keys: ModuleKey[]) =>
  (m: ModuleKey) =>
    keys.includes(m);

function Frame({
  modules,
  live = [],
  activeFn = "runs",
  area = "agents",
  startCollapsed = false,
}: {
  modules: ModuleKey[];
  live?: NavRequirement[];
  activeFn?: string;
  area?: "home" | "agents" | "apps" | "secure" | "admin";
  startCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = React.useState(startCollapsed);
  return (
    <div className="bg-background flex h-[640px]">
      <MainRail
        nav={visibleNav(NAV, allowed(...modules), new Set(live))}
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

const ALL: ModuleKey[] = ["apps", "agents", "workflows", "models", "admin"];

/** Zentinelle connected: Secure › Zentinelle sits between Apps and Admin. */
export const ZentinelleConnected: StoryObj = {
  render: () => <Frame modules={ALL} live={["zentinelle"]} area="secure" activeFn="zentinelle" />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Zentinelle" })).toHaveAttribute(
      "href",
      "/secure/zentinelle"
    );
  },
};

/** Not connected (or revoked): no Secure area, no dead link. */
export const ZentinelleNotConnected: StoryObj = {
  render: () => <Frame modules={ALL} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("link", { name: "Zentinelle" })).not.toBeInTheDocument();
  },
};
