import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, fn, userEvent, within } from "storybook/test";

import type { CreateBrainInput } from "@/graphql/__generated__/operations";

import { CELLS } from "../brain-cells.fixtures";
import {
  CONNECTIONS,
  CREATION_DEPLOYING,
  CREATION_FAILED,
  PROJECTS,
  wizardProps,
} from "./new-brain.fixtures";
import {
  createInput,
  draftProblems,
  EMPTY_DRAFT,
  type NewBrainStep,
  stepReady,
  withDefaults,
} from "./new-brain-model";
import { NewBrainWizard, type NewBrainWizardProps } from "./NewBrainWizard";

const meta: Meta<typeof NewBrainWizard> = {
  title: "Screens/Brains/New/NewBrainWizard",
  component: NewBrainWizard,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof NewBrainWizard>;

/** The wizard as useNewBrain drives it, with the create call recorded instead of sent. */
function Live({
  onCreate,
  ...patch
}: { onCreate: (input: CreateBrainInput) => void } & Partial<NewBrainWizardProps>) {
  const [step, setStep] = React.useState<NewBrainStep>(1);
  const [own, setOwn] = React.useState(EMPTY_DRAFT);
  const context = {
    connections: CONNECTIONS,
    projects: PROJECTS,
    matrix: CELLS,
    supportsAppSlug: true,
  };
  const draft = withDefaults(own, context);
  const problems = draftProblems(draft, context);
  const project = PROJECTS.find((p) => p.id === draft.projectId);
  return (
    <NewBrainWizard
      {...wizardProps(step, {
        draft,
        onChange: setOwn,
        problems,
        onStep: setStep,
        onBack: step > 1 ? () => setStep((step - 1) as NewBrainStep) : undefined,
        onContinue: () =>
          step < 4
            ? setStep((step + 1) as NewBrainStep)
            : onCreate(createInput(draft, { canDeploy: true })),
        continueDisabled:
          step < 4
            ? !stepReady(step, problems)
            : ![1, 2, 3].every((s) => stepReady(s as NewBrainStep, problems)),
        connectionName: CONNECTIONS.find((c) => c.id === draft.connectionId)?.name ?? "",
        teamSlug: project?.team.slug ?? "",
        projectSlug: project?.slug ?? "",
      })}
      {...patch}
    />
  );
}

const onCreate = fn((_input: CreateBrainInput) => {});

/** The whole path: a repository, a name, the default cell, then Create. */
export const CreateFilesLayer: Story = {
  render: () => <Live onCreate={onCreate} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const next = () => canvas.getByRole("button", { name: "Continue" });
    await expect(next()).toBeDisabled();
    await userEvent.type(canvas.getByLabelText(/Repository/), "acme-platform/handbook-brain");
    await userEvent.click(next());
    await userEvent.type(canvas.getByLabelText("Name"), "Handbook");
    await expect(canvas.getByLabelText(/Slug/)).toHaveValue("handbook");
    await userEvent.clear(canvas.getByLabelText(/Slug/));
    await userEvent.type(canvas.getByLabelText(/Slug/), "handbook-brain");
    await userEvent.click(next());
    await expect(canvas.getByText(/Supported by Mimwell/)).toBeInTheDocument();
    await userEvent.click(next());
    await expect(canvas.getByText("acme-platform/handbook-brain")).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Create semantic layer" }));
    await expect(onCreate).toHaveBeenCalledWith({
      projectId: PROJECTS[0]!.id,
      slug: "handbook-brain",
      sourceConnectionId: CONNECTIONS[0]!.id,
      repo: "acme-platform/handbook-brain",
      name: "Handbook",
      teamId: PROJECTS[0]!.team.id,
      visibility: "PRIVATE",
      templateRef: null,
      environmentName: "production",
      deploy: true,
      substrate: {
        authority: "FILES",
        blobs: "FS",
        vectors: "LOCAL",
        graph: "EDGES_TABLE",
        persistence: [],
        replicas: 1,
        image: null,
        port: 8080,
      },
    });
  },
};

export const Source: Story = { args: wizardProps(1) };
export const Details: Story = { args: wizardProps(2) };
export const Substrate: Story = { args: wizardProps(3) };
/** Review names the edition this install pins (`astroliftBrainEdition`). */
export const Review: Story = {
  args: wizardProps(4),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText("The edition this install pins (v2026.10.1)")
    ).toBeInTheDocument();
  },
};

/** The pinned edition has no deploy.app_slug: a slug not ending in -brain is refused before Create. */
export const SlugNeedsAppSlug: Story = {
  args: wizardProps(2, { problems: { slug: "needsAppSlug" } }),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/has no deploy.app_slug/)).toBeInTheDocument();
  },
};

/** The server refused the slug: back on Details, with its message on the field. */
export const SlugRefused: Story = {
  args: wizardProps(2, {
    serverErrors: {
      slug: "a brain's slug is its client slug plus -brain, e.g. acme-brain",
    },
  }),
};

/** A refusal about the cell lands on the substrate step. */
export const CellRefused: Story = {
  args: wizardProps(3, {
    substrateError: {
      field: "persistence",
      message: "graph=age needs persistence 'postgres' on Astrolift",
    },
  }),
};

/** A production deploy needs a fresh sign-in; cancelling the prompt leaves this. */
export const StepUpCancelled: Story = {
  args: wizardProps(4, {
    generalError: "Deploy to production needs a fresh sign-in.",
  }),
};

export const Creating: Story = { args: wizardProps(4, { busy: true }) };
export const Denied: Story = { args: wizardProps(1, { denied: true }) };

/** Accepted: the progress replaces the form. */
export const Progress: Story = {
  args: wizardProps(4, {
    progress: {
      creation: CREATION_DEPLOYING,
      loading: false,
      error: null,
      onRetry: fn(),
      deploy: true,
      failed: null,
      onStartOver: fn(),
    },
  }),
};

export const ProgressFailed: Story = {
  args: wizardProps(4, {
    progress: {
      creation: CREATION_FAILED,
      loading: false,
      error: null,
      onRetry: fn(),
      deploy: true,
      failed: "REPO_READY",
      onEdit: fn(),
      onStartOver: fn(),
    },
  }),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <NewBrainWizard {...wizardProps(3)} />
    </div>
  ),
};
