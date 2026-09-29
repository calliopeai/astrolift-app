import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import {
  APPS_PICK,
  ERRORS,
  FAILED,
  LOADING,
  LONG_ARN,
  LONG_SHA,
  READY,
  TRAFFIC,
} from "./apps-agents.fixtures";
import { TrafficErrorsPanelView } from "./TrafficErrorsPanel";

/**
 * Traffic & errors: one of the viewer's apps over 24 hours, requests and
 * errors with their figures in mono; the app picker switches which.
 */
const meta: Meta<typeof TrafficErrorsPanelView> = {
  title: "Home/Panels/TrafficErrorsPanel",
  component: TrafficErrorsPanelView,
  args: {
    panel: HOME_PANELS["traffic-errors"],
    ...READY,
    apps: APPS_PICK,
    appSlug: APPS_PICK[0]!.slug,
    onAppChange: fn(),
    traffic: TRAFFIC,
    errors: ERRORS,
    reason: "OK",
  },
  decorators: [
    (Story) => (
      <PanelGrid>
        <Story />
      </PanelGrid>
    ),
  ],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof TrafficErrorsPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Requests")).toBeInTheDocument();
    await expect(canvas.getByText("Errors")).toBeInTheDocument();
    await expect(canvas.getByRole("combobox", { name: "App" })).toBeInTheDocument();
  },
};

export const Loading: Story = { args: { ...LOADING, traffic: null, errors: null, reason: null } };

/** The cluster has no Prometheus: the panel says so instead of a flat line. */
export const NotConfigured: Story = {
  args: { traffic: null, errors: null, reason: "NOT_CONFIGURED" },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("Not configured")).toBeInTheDocument();
  },
};

export const NoApps: Story = {
  args: { apps: [], appSlug: null, traffic: null, errors: null, reason: null },
};

export const QueryError: Story = { args: { ...FAILED, traffic: null, errors: null } };

export const LongStrings: Story = {
  args: {
    apps: [{ slug: `app-${LONG_SHA}`, name: LONG_ARN }],
    appSlug: `app-${LONG_SHA}`,
  },
};

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
