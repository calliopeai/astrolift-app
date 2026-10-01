import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { PanelGrid } from "@/components/panel/Panel";

import { SpendQuotaPanelView } from "./SpendQuotaPanel";

import {
  BUDGET,
  BUDGET_NEAR,
  BUDGET_OVER,
  FAILED,
  FORECAST,
  LOADING,
  PANEL,
  READY,
} from "./builder-operator.fixtures";

/** Spend & quota on the Operator layout: spend against the org budget, with its legend. */
const meta: Meta = { title: "Home/Panels/SpendQuotaPanel", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

const Grid = ({ children, width }: { children: React.ReactNode; width?: number }) => (
  <div style={width ? { width } : undefined}>
    <PanelGrid>{children}</PanelGrid>
  </div>
);

export const Full: Story = {
  render: () => (
    <Grid>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={FORECAST}
        budget={BUDGET}
        {...READY}
      />
    </Grid>
  ),
};

export const NearBudget: Story = {
  render: () => (
    <Grid>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={FORECAST}
        budget={BUDGET_NEAR}
        {...READY}
      />
    </Grid>
  ),
};

export const OverBudget: Story = {
  render: () => (
    <Grid>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={FORECAST}
        budget={BUDGET_OVER}
        {...READY}
      />
    </Grid>
  ),
};

export const NoBudget: Story = {
  render: () => (
    <Grid>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={FORECAST}
        budget={null}
        {...READY}
      />
    </Grid>
  ),
};

export const BudgetFailed: Story = {
  render: () => (
    <Grid>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={FORECAST}
        budget={null}
        {...READY}
        budgetError={FAILED.error}
      />
    </Grid>
  ),
};

export const Loading: Story = {
  render: () => (
    <Grid>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={null}
        budget={null}
        {...LOADING}
      />
    </Grid>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Grid>
      <SpendQuotaPanelView panel={PANEL["spend-quota"]} forecast={null} budget={null} {...FAILED} />
    </Grid>
  ),
};

export const Width768: Story = {
  render: () => (
    <Grid width={768}>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={FORECAST}
        budget={BUDGET}
        {...READY}
      />
    </Grid>
  ),
};

export const GermanBudgetWidth768: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => (
    <Grid width={768}>
      <SpendQuotaPanelView
        panel={PANEL["spend-quota"]}
        forecast={FORECAST}
        budget={BUDGET_OVER}
        {...READY}
      />
    </Grid>
  ),
};
