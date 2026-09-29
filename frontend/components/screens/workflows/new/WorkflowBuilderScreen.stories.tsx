import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { PatternCard, WorkflowBuilderScreen } from "./WorkflowBuilderScreen";
import { BUILDER, LONG_BUILDER_NAME, MANIFEST_PREVIEW } from "./workflows-new-builder.fixtures";
import { PATTERNS } from "./workflow-patterns";

const meta: Meta = {
  title: "Screens/Workflows/New/WorkflowBuilderScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Step 1, from a pattern: the screen loads no data, so this is also its empty state. */
export const Full: Story = { render: () => <WorkflowBuilderScreen {...BUILDER} /> };

/** Opened with `?pattern=fan_out`. */
export const PatternFromQuery: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} initialPattern="fan_out" />,
};

/** Continue with no name: the errors stand beside their fields. */
export const SourceErrors: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await expect(await canvas.findByText("Give the workflow a name.")).toBeInTheDocument();
  },
};

/** Step 1, from a manifest. */
export const FromManifest: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} initialSource="manifest" />,
};

/** The manifest did not parse: its error, with the line, on the field. */
export const ManifestInvalid: Story = {
  render: () => (
    <WorkflowBuilderScreen
      {...BUILDER}
      initialSource="manifest"
      initialErrors={{ toml: "Expected '=' after a key (line 4, col 7, at stages[0].kind)" }}
    />
  ),
};

/** Checking the manifest before the Stages step. */
export const Previewing: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} initialSource="manifest" previewing />,
};

/** Step 2 from a manifest: its stages drawn in the person's workflow view. */
export const StagesFromManifest: Story = {
  render: () => (
    <WorkflowBuilderScreen
      {...BUILDER}
      initialSource="manifest"
      initialStep={2}
      initialPreview={MANIFEST_PREVIEW}
    />
  ),
};

/** Step 2 from a pattern: the shape, stages added in the Builder. */
export const StagesFromPattern: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} initialStep={2} initialPattern="review_loop" />,
};

export const Review: Story = {
  render: () => (
    <WorkflowBuilderScreen
      {...BUILDER}
      initialSource="manifest"
      initialStep={3}
      initialPreview={MANIFEST_PREVIEW}
    />
  ),
};

/** The create mutation is in flight. */
export const Creating: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} initialStep={3} creating />,
};

/** Refused for a reason no field owns. */
export const CreateFailed: Story = {
  render: () => (
    <WorkflowBuilderScreen
      {...BUILDER}
      initialStep={3}
      initialErrors={{ form: "organization: workflow quota reached" }}
    />
  ),
};

export const NoCreateAccess: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} initialStep={3} canCreate={false} />,
};

/** Typing a name derives the slug until the slug is edited by hand. */
export const LongStrings: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByPlaceholderText("e.g. PR Review Loop"), LONG_BUILDER_NAME);
    await expect(canvas.getByPlaceholderText("e.g. pr-review-loop")).toHaveValue(
      "quarterly-enterprise-account-research-enrichment-legal-review-and-multi-region-outbound-pipeline"
    );
  },
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <WorkflowBuilderScreen
        {...BUILDER}
        initialSource="manifest"
        initialStep={2}
        initialPreview={MANIFEST_PREVIEW}
      />
    </div>
  ),
};

export const PatternCardSelected: Story = {
  render: () => (
    <div className="max-w-sm p-4">
      <PatternCard pattern={PATTERNS[2]} selected onSelect={() => {}} />
    </div>
  ),
};
