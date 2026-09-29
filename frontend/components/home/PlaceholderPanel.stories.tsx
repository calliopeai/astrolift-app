import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_ARN, LONG_SHA, LONG_URL } from "@/components/panel/fixtures";
import { PanelGrid } from "@/components/panel/Panel";

import { PlaceholderPanel } from "./PlaceholderPanel";
import { HOME_PANELS } from "./registry";

/** Each Home panel's stand-in, one per kind: list, feed, KPIs, chart. */
const meta: Meta = { title: "Home/PlaceholderPanel", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

export const EveryKind: Story = {
  render: () => (
    <PanelGrid>
      <PlaceholderPanel panel={HOME_PANELS.waiting} />
      <PlaceholderPanel panel={HOME_PANELS.failing} />
      <PlaceholderPanel panel={HOME_PANELS.activity} />
      <PlaceholderPanel panel={HOME_PANELS.kpis} />
      <PlaceholderPanel panel={HOME_PANELS.deployments} />
      <PlaceholderPanel panel={HOME_PANELS["runs-spend"]} />
    </PanelGrid>
  ),
};

export const EveryPanel: Story = {
  render: () => (
    <PanelGrid>
      {Object.values(HOME_PANELS).map((p) => (
        <PlaceholderPanel key={p.key} panel={p} />
      ))}
    </PanelGrid>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PanelGrid>
      <PlaceholderPanel
        panel={{ ...HOME_PANELS["my-apps"], title: `My apps ${LONG_SHA}`, href: LONG_URL }}
      />
      <PlaceholderPanel panel={{ ...HOME_PANELS.activity, title: LONG_ARN }} />
      <PlaceholderPanel panel={{ ...HOME_PANELS["spend-quota"], title: LONG_ARN }} />
    </PanelGrid>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <PanelGrid>
        <PlaceholderPanel panel={HOME_PANELS.waiting} />
        <PlaceholderPanel panel={HOME_PANELS["platform-activity"]} />
        <PlaceholderPanel panel={HOME_PANELS["traffic-errors"]} />
      </PanelGrid>
    </div>
  ),
};
