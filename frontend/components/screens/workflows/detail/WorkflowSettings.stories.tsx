import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

import { DEFINITION, LONG_DEFINITION } from "./workflow-detail-a.fixtures";
import { LONG_WORKFLOW, WORKFLOW } from "./workflow-detail-b.fixtures";
import { WorkflowSettingsView, type WorkflowSettingsViewProps } from "./WorkflowSettings";

type Args = Omit<WorkflowSettingsViewProps, "single"> & { section?: string };

function Harness({ section, ...props }: Args) {
  const single = useLocalSettingsSection(section ?? null);
  return <WorkflowSettingsView {...props} single={single} />;
}

const CONFIGURED: Args = {
  kind: "configured",
  general: { name: WORKFLOW.name, slug: WORKFLOW.slug, description: WORKFLOW.description },
  onSave: async () => true,
  onDelete: async () => {},
};

const meta: Meta<Args> = {
  title: "Screens/Workflows/Detail/WorkflowSettings",
  component: Harness,
  parameters: { layout: "padded" },
  args: CONFIGURED,
};
export default meta;

type Story = StoryObj<Args>;

/** General, for a configured workflow: name and description save on their own. */
export const General: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByLabelText("Name")).toHaveValue("Nightly sync");
    await expect(canvas.getByRole("button", { name: "Save" })).toBeDisabled();
  },
};

export const DangerZone: Story = {
  args: { section: "danger-zone" },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("button", { name: "Delete workflow" })
    ).toBeInTheDocument();
  },
};

export const Saving: Story = { args: { saving: true } };

export const SaveError: Story = {
  args: { saveError: "name: A workflow with this name already exists in this organization." },
};

/** No `workflow.update`: the same fields, disabled, and no Danger zone. */
export const ReadOnly: Story = {
  args: { readOnly: { permission: "workflow.update" }, onSave: undefined, onDelete: undefined },
};

/** A repository definition: where it was declared, read-only, nothing to delete. */
export const RepositoryDefinition: Story = {
  args: {
    kind: "definition",
    general: { name: DEFINITION.name, slug: DEFINITION.slug, description: DEFINITION.description },
    source: { repo: DEFINITION.sourceRepo, path: DEFINITION.sourcePath, ref: DEFINITION.sourceRef },
    onSave: undefined,
    onDelete: undefined,
  },
};

/** An org-authored definition may be deleted. */
export const OrgDefinition: Story = {
  args: {
    kind: "definition",
    general: { name: DEFINITION.name, slug: DEFINITION.slug, description: "" },
    source: { repo: "", path: "", ref: "" },
    onSave: undefined,
    section: "danger-zone",
  },
};

export const LongStrings: Story = {
  args: {
    kind: "definition",
    general: {
      name: LONG_DEFINITION.name,
      slug: LONG_DEFINITION.slug,
      description: LONG_DEFINITION.description,
    },
    source: {
      repo: LONG_DEFINITION.sourceRepo,
      path: LONG_DEFINITION.sourcePath,
      ref: LONG_DEFINITION.sourceRef,
    },
    onSave: undefined,
    onDelete: undefined,
  },
};

export const At768: Story = {
  args: {
    general: {
      name: LONG_WORKFLOW.name,
      slug: LONG_WORKFLOW.slug,
      description: LONG_WORKFLOW.description,
    },
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <Harness {...args} />
    </div>
  ),
};
