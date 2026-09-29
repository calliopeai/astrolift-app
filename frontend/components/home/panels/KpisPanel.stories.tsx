import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { PanelGrid } from "@/components/panel/Panel";

import { KpisPanelView } from "./KpisPanel";

import {
  FAILED,
  KPIS,
  KPIS_AGENTS_ONLY,
  KPIS_EMPTY,
  KPIS_LOADING,
  PANEL,
} from "./builder-operator.fixtures";

/** The Builder KPI strip: deploys, runs, success, p95 and spend, with the period. */
const meta: Meta = { title: "Home/Panels/KpisPanel", parameters: { layout: "padded" } };
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
      <KpisPanelView panel={PANEL.kpis} {...KPIS} />
    </Grid>
  ),
};

export const Loading: Story = {
  render: () => (
    <Grid>
      <KpisPanelView panel={PANEL.kpis} {...KPIS_LOADING} />
    </Grid>
  ),
};

export const NoDeploysYet: Story = {
  render: () => (
    <Grid>
      <KpisPanelView panel={PANEL.kpis} {...KPIS_EMPTY} />
    </Grid>
  ),
};

export const OnlyAgents: Story = {
  render: () => (
    <Grid>
      <KpisPanelView panel={PANEL.kpis} {...KPIS_AGENTS_ONLY} />
    </Grid>
  ),
};

export const NoFiguresForAccess: Story = {
  render: () => (
    <Grid>
      <KpisPanelView panel={PANEL.kpis} windowDays={30} figures={[]} />
    </Grid>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Grid>
      <KpisPanelView panel={PANEL.kpis} {...KPIS_LOADING} {...FAILED} />
    </Grid>
  ),
};

export const Width768: Story = {
  render: () => (
    <Grid width={768}>
      <KpisPanelView panel={PANEL.kpis} {...KPIS} />
    </Grid>
  ),
};
