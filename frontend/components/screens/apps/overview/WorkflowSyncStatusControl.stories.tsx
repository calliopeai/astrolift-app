import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import { CI_SETUP, CI_SETUP_PROBLEMS } from "./app-ci-observability-section.fixtures";
import { WorkflowSyncStatusControl } from "./WorkflowSyncStatusControl";

const meta: Meta<typeof WorkflowSyncStatusControl> = {
  title: "Screens/Apps/Overview/WorkflowSyncStatusControl",
  component: WorkflowSyncStatusControl,
};
export default meta;
type Story = StoryObj<typeof meta>;
const full = { ...CI_SETUP.workflowSync, status: CI_SETUP.ciWorkflowSyncStatus! };
export const Full: Story = { args: full };
export const Loading: Story = { args: { ...full, refreshing: true } };
export const Empty: Story = {
  args: {
    ...full,
    status: { ...full.status, state: "absent", repoText: "", renderedText: "" },
  },
};
export const ErrorState: Story = {
  args: { ...full, status: { ...full.status, state: "future_state_v2" } },
};
export const LongStrings: Story = {
  args: {
    ...full,
    status: {
      ...full.status,
      state: "future_state_".repeat(30),
      repoText: "RAW_REPO_YAML\n".repeat(30),
      renderedText: "RAW_RENDERED_YAML\n".repeat(30),
    },
  },
};
export const FrenchConflict: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <WorkflowSyncStatusControl
        {...CI_SETUP_PROBLEMS.workflowSync}
        status={CI_SETUP_PROBLEMS.ciWorkflowSyncStatus!}
      />
    </NextIntlClientProvider>
  ),
};
export const JapanesePull: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <WorkflowSyncStatusControl {...full} />
    </NextIntlClientProvider>
  ),
};
export const ReadOnly: Story = {
  render: () => (
    <PermissionsProvider value={{ granted: new Set(), loading: false }}>
      <WorkflowSyncStatusControl {...full} />
    </PermissionsProvider>
  ),
};
