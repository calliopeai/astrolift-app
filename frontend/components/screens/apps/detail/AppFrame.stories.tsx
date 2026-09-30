import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import spanish from "@/messages/es.json";
import { expect, within } from "storybook/test";

import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import { AppFrame } from "./AppFrame";
import { LONG } from "./app-detail-shell.fixtures";
import { FAILING_APP, FRAME, LONG_FRAME_APP } from "./app-frame.fixtures";

/**
 * The frame around every app page (spec 44 §5.2). The body here is a stand-in;
 * each tab's own screen has its own stories.
 */
function Body({ label }: { label: string }) {
  return (
    <EmptyState
      icon={<LayersIcon className="size-5" />}
      title={label}
      description="The tab's own screen renders here."
    />
  );
}

const meta: Meta<typeof AppFrame> = {
  title: "Screens/Apps/Detail/AppFrame",
  component: AppFrame,
  parameters: { layout: "padded" },
  args: { ...FRAME, children: <Body label="Overview" /> },
};
export default meta;

type Story = StoryObj<typeof AppFrame>;

const at = (path: string, label: string): Story => ({
  args: { pathname: `/apps/checkout${path}`, children: <Body label={label} /> },
});

export const Overview: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const tabs = within(canvas.getByRole("navigation", { name: "App sections" }));
    await expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Overview",
      "Deployments",
      "Workloads",
      "Logs & metrics",
      "Domains",
      "Secrets",
      "Access",
      "Settings",
    ]);
    await expect(tabs.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(canvas.getByRole("button", { name: /Deploy/ })).toBeInTheDocument();
  },
};
export const Deployments = at("/deployments", "Deployments");
export const Workloads = at("/workloads", "Workloads");

/** A workload's own page stays under the Workloads tab, with its own crumb. */
export const WorkloadDetail: Story = {
  args: { pathname: "/apps/checkout/workloads/web", children: <Body label="Workload web" /> },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Workloads" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(canvas.getByText("web", { selector: "[aria-current='page']" })).toBeVisible();
  },
};
export const LogsAndMetrics = at("/logs", "Logs & metrics");
export const Domains = at("/domains", "Domains");
export const Secrets = at("/secrets", "Secrets");
export const Access = at("/access", "Access");
export const Settings = at("/settings", "Settings");

/** A failing app: the reason is the first thing under the header. */
export const Failing: Story = {
  args: { app: FAILING_APP },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("alert")).toHaveTextContent("Image pull denied");
  },
};

/** No `app.deploy`: no Deploy button. The `⋯` menu is still there. */
export const WithoutDeployPermission: Story = {
  args: { canDeploy: false, canUpdate: false, canDelete: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: /Deploy/ })).toBeNull();
    await expect(canvas.getByRole("button", { name: "More actions" })).toBeInTheDocument();
  },
};

/** No deploy history: Deploy goes to the environments to pick a tag. */
export const NoDeployHistory: Story = {
  args: { latestDeploy: null },
};

export const Archived: Story = {
  args: { app: { ...FRAME.app!, archived: true } },
};

export const Provisioning: Story = {
  args: { app: { ...FRAME.app!, status: "provisioning", live: false } },
};

export const Loading: Story = {
  args: { app: null, loading: true },
};

export const NotFound: Story = {
  args: { app: null, slug: "no-such-app" },
};

export const LoadError: Story = {
  args: {
    app: null,
    error:
      "Network error: request to https://api.astrolift.example.com/graphql/with/an/unbroken/path/that/keeps/going failed",
  },
};

/** A 96-character name, a long environment and cluster, and an unbroken URL. */
export const LongStrings: Story = {
  args: {
    slug: LONG,
    pathname: `/apps/${LONG}/workloads/${"w".repeat(64)}`,
    app: LONG_FRAME_APP,
    latestDeploy: { imageTag: "a".repeat(64), environmentName: "production" },
  },
};

/** The narrowest the web console goes: no horizontal page scroll. */
export const At768: Story = {
  args: { app: LONG_FRAME_APP, slug: LONG, pathname: `/apps/${LONG}/logs` },
  render: (args) => (
    <div style={{ width: 768 }}>
      <AppFrame {...args} />
    </div>
  ),
};

export const SpanishWidth768: Story = {
  render: (args) => (
    <NextIntlClientProvider locale="es" messages={spanish}>
      <div style={{ width: 768 }}>
        <AppFrame {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
