import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { BUILDER, DEFINITION, STAGES } from "@/components/workflows/fixtures";
import { StageBuilder } from "@/components/workflows/StageBuilder";

const meta: Meta = {
  title: "Screens/Workflows/StageBuilder",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Editable: Story = { render: () => <StageBuilder {...BUILDER} /> };

export const NoStagesYet: Story = { render: () => <StageBuilder {...BUILDER} stages={[]} /> };

export const StagesLoading: Story = {
  render: () => <StageBuilder {...BUILDER} stages={[]} stagesLoading />,
};

/** A platform template: read-only with a clone banner. */
export const PlatformTemplate: Story = {
  render: () => (
    <StageBuilder
      {...BUILDER}
      definition={{ ...DEFINITION, isGlobal: true, organizationGuid: null }}
    />
  ),
};

/** Owned by a repository: read-only, edit the file and sync. */
export const RepositoryManaged: Story = {
  render: () => (
    <StageBuilder
      {...BUILDER}
      definition={{
        ...DEFINITION,
        sourceRepo: "calliopeai/company-agents",
        sourcePath: "workflows/outbound.toml",
        sourceRef: "f1f9f11a0c2e4b7d",
      }}
    />
  ),
};

export const NoManageAccess: Story = {
  render: () => <StageBuilder {...BUILDER} canManage={false} />,
};

export const DefinitionLoading: Story = {
  render: () => <StageBuilder {...BUILDER} definition={null} defLoading />,
};

export const DefinitionNotFound: Story = {
  render: () => <StageBuilder {...BUILDER} definition={null} />,
};

/** The TOML manifest view. */
export const CodeView: Story = {
  render: () => <StageBuilder {...BUILDER} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Code/ }));
    await expect(await c.findByLabelText("Workflow manifest TOML")).toBeInTheDocument();
  },
};

/** One stage open at a time: opening the second closes the first. */
export const OneStageOpen: Story = {
  render: () => <StageBuilder {...BUILDER} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    const [first, second] = c.getAllByRole("button", { name: "Expand stage" });
    await userEvent.click(first!);
    await userEvent.click(second!);
    await expect(c.getAllByRole("button", { name: "Collapse stage" })).toHaveLength(1);
  },
};

/** A supervisor handing work to its workers, a retrying stage and a nested workflow. */
export const SupervisorRetryNested: Story = {
  render: () => (
    <StageBuilder
      {...BUILDER}
      definition={{ ...DEFINITION, patternKind: "supervisor_worker" }}
      stages={[
        { ...STAGES[0], role: "route" },
        { ...STAGES[1], role: "report" },
        { ...STAGES[3], onFailure: "retry" },
        {
          ...STAGES[3],
          guid: "stage-4",
          order: 4,
          kind: "workflow",
          role: "",
          workflowRef: "bdr-outreach-follow-up",
          agentDefinitionGuid: null,
          agentDefinitionName: null,
        },
      ]}
    />
  ),
};

/** A 64-char SHA source ref, a 200-char workflow ref and an unbroken URL for a source repo. */
export const LongStrings: Story = {
  render: () => (
    <StageBuilder
      {...BUILDER}
      definition={{
        ...DEFINITION,
        name: `${"quarterly-enterprise-account-research-".repeat(3)}pipeline`,
        sourceRepo:
          "https://github.com/calliopeai/astrolift-agents-with-a-very-long-organisation-name/blob/main",
        sourcePath: "workflows/nightly/production/emr-triage-intake-and-decision-pipeline.toml",
        sourceRef: "f1f9f11a0c2e4b7d9a3e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4",
      }}
      stages={[
        ...STAGES,
        {
          ...STAGES[3],
          guid: "stage-long",
          order: 4,
          kind: "workflow",
          workflowRef:
            `arn:aws:states:us-west-2:718519534729:stateMachine:${"platform-shared-".repeat(9)}`.slice(
              0,
              200
            ),
        },
      ]}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <StageBuilder {...BUILDER} />
    </div>
  ),
};
