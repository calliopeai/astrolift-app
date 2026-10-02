import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import { AgentFrame } from "./AgentFrame";
import { AGENT, LONG, PAUSED_AGENT, RUNNING_AGENT } from "./agent-detail-shell.fixtures";
import { FAILING_FRAME, FRAME, LONG_FRAME } from "./agent-frame.fixtures";

/**
 * The frame around every agent page (spec 44 §4.4, §5.2). The body here is a
 * stand-in; each tab's own screen has its own stories.
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

const meta: Meta<typeof AgentFrame> = {
  title: "Screens/Agents/Detail/AgentFrame",
  component: AgentFrame,
  parameters: { layout: "padded" },
  args: { ...FRAME, children: <Body label="Overview" /> },
};
export default meta;

type Story = StoryObj<typeof AgentFrame>;

const at = (path: string, label: string): Story => ({
  args: { pathname: `/agents/${AGENT.slug}${path}`, children: <Body label={label} /> },
  play: async ({ canvasElement }) => {
    const tabs = within(within(canvasElement).getByRole("navigation", { name: "Agent sections" }));
    await expect(tabs.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  },
});

export const Overview: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const tabs = within(canvas.getByRole("navigation", { name: "Agent sections" }));
    await expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Overview",
      "Runs",
      "Configuration",
      "Skills & tools",
      "Logs & metrics",
      "Secrets",
      "Access",
      "Settings",
    ]);
    await expect(tabs.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(canvas.getByRole("button", { name: /Run now/ })).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "conflict-astrolift" })).toHaveAttribute(
      "href",
      "/clusters/conflict-astrolift"
    );
  },
};
export const Runs = at("/runs", "Runs");
export const Configuration = at("/configuration", "Configuration");
export const SkillsAndTools = at("/skills", "Skills & tools");
export const LogsAndMetrics = at("/logs", "Logs & metrics");
export const Secrets = at("/secrets", "Secrets");
export const Access = at("/access", "Access");
export const Settings = at("/settings", "Settings");

/** A former route (`/observability`) still lights the tab that absorbed it. */
export const FormerRoute = at("/observability", "Logs & metrics");

/** The last run failed: the reason is the first thing under the header, on every tab. */
export const Failing: Story = {
  args: FAILING_FRAME,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("alert")).toHaveTextContent("ImagePullBackOff");
    await expect(canvas.getByText("Last run failed", { selector: "span" })).toBeInTheDocument();
  },
};

/** A failed run that reported no reason says so rather than going blank. */
export const FailingWithoutReason: Story = {
  args: { ...FAILING_FRAME, failedRun: { id: "", reason: null } },
};

/** No `agent.dispatch`: no Run now. The `⋯` menu is still there. */
export const WithoutRunPermission: Story = {
  args: { canRun: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: /Run now/ })).toBeNull();
    await expect(canvas.getByRole("button", { name: "More actions" })).toBeInTheDocument();
  },
};

export const Running: Story = { args: { agent: RUNNING_AGENT } };

/** Paused, never run, no repository link, model and cluster not known yet. */
export const PausedNeverRun: Story = {
  args: { agent: PAUSED_AGENT, model: null, clusterSlug: null },
};

export const Dispatching: Story = { args: { dispatching: true } };

export const Loading: Story = { args: { agent: null, loading: true } };

export const NotFound: Story = { args: { agent: null, slug: "no-such-agent" } };

export const LoadError: Story = {
  args: {
    agent: null,
    error:
      "Network error: request to https://api.astrolift.example.com/graphql/with/an/unbroken/path/that/keeps/going failed",
  },
};

/** A 96-character name and slug, custom run family and mode, a long cluster, an unbroken URL. */
export const LongStrings: Story = { args: LONG_FRAME };

/** The narrowest the web console goes: no horizontal page scroll. */
export const At768: Story = {
  args: { ...LONG_FRAME, ...{ failedRun: FAILING_FRAME.failedRun }, slug: LONG },
  render: (args) => (
    <div style={{ width: 768 }}>
      <AgentFrame {...args} />
    </div>
  ),
};

export const FrenchRunRefused: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <AgentFrame {...FRAME} model={fr.agentFrame.managedModel} onRun={async () => false}>
        <div>LITERAL_CONTENT</div>
      </AgentFrame>
    </NextIntlClientProvider>
  ),
};
export const JapaneseFailure: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <AgentFrame {...FAILING_FRAME} model={ja.agentFrame.managedModel}>
        <div>LITERAL_CONTENT</div>
      </AgentFrame>
    </NextIntlClientProvider>
  ),
};
export const FrenchLoadError: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <AgentFrame {...FRAME} agent={null} error="RAW_SERVER_DIAGNOSTIC">
        <div>LITERAL_CONTENT</div>
      </AgentFrame>
    </NextIntlClientProvider>
  ),
};
