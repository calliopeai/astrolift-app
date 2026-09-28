import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { EditProjectSheet } from "./EditProjectSheet";
import { LONG_PROJECT, editProjectProps } from "./projects-teams-dialogs.fixtures";

const meta: Meta = {
  title: "Screens/Projects/EditProjectSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Freshly opened: nothing changed, so the save stays disabled. */
export const Unchanged: Story = { render: () => <EditProjectSheet {...editProjectProps()} /> };

/** The availability check for a new slug is in flight. */
export const Checking: Story = {
  render: () => (
    <EditProjectSheet {...editProjectProps({ slug: "edge", slugStatus: "checking" })} />
  ),
};

export const Available: Story = {
  render: () => (
    <EditProjectSheet
      {...editProjectProps({ slug: "edge", slugStatus: "available", canSubmit: true })}
    />
  ),
};

export const Taken: Story = {
  render: () => (
    <EditProjectSheet {...editProjectProps({ slug: "billing", slugStatus: "taken" })} />
  ),
};

export const InvalidSlug: Story = {
  render: () => (
    <EditProjectSheet {...editProjectProps({ slug: "9-Bad_slug", slugStatus: "invalid" })} />
  ),
};

export const EmptySlug: Story = {
  render: () => <EditProjectSheet {...editProjectProps({ slug: "", slugStatus: "empty" })} />,
};

/** The update mutation is in flight. */
export const Saving: Story = {
  render: () => (
    <EditProjectSheet
      {...editProjectProps({ slug: "edge", slugStatus: "available", saving: true })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <EditProjectSheet
      {...editProjectProps({
        project: LONG_PROJECT,
        name: LONG_PROJECT.name,
        slug: "customer-facing-realtime-analytics-pipeline-eu-west",
        slugStatus: "invalid",
      })}
    />
  ),
};
