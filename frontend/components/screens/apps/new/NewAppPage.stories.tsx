import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, within } from "storybook/test";

import { type AppDeployStrategyFields, AppDeployStrategyStep } from "./AppDeployStrategyStep";
import { AppDetailsStepView } from "./AppDetailsStep";
import { DETAILS, DETAILS_STATE, LONG, LONG_DETAILS } from "./apps-list-wizard-shell.fixtures";
import { DEPLOY_STRATEGY_STATE, MANIFEST_STATE } from "./apps-wizard-steps-a.fixtures";
import {
  REPO_PICKER,
  REPO_PICKER_LOADING,
  REVIEW,
  REVIEW_FAILED,
  REVIEW_LONG,
  REVIEW_SUBMITTING,
} from "./apps-wizard-steps-b.fixtures";
import { ManifestPreviewStepView } from "./ManifestPreviewStep";
import { NewAppPage, type NewAppPageProps, NewAppSection } from "./NewAppPage";
import { RepoPickerStepView } from "./RepoPickerStep";
import { ReviewSubmitStepView } from "./ReviewSubmitStep";
import type { AppDetailsFields } from "./use-app-details-step";

/**
 * Apps › New app: three numbered steps over the existing step views. The page
 * has no data of its own; its loading and error states are its steps'.
 */
const meta: Meta = {
  title: "Screens/Apps/New/NewAppPage",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const noop = () => {};

const page: Omit<NewAppPageProps, "step" | "children"> = {
  onStep: noop,
  onBack: noop,
  onContinue: noop,
  onCancel: noop,
};

function Source({ loading = false }: { loading?: boolean }) {
  const [state, setState] = React.useState(MANIFEST_STATE);
  const [editing, setEditing] = React.useState(false);
  return (
    <>
      <NewAppSection title="Repository">
        <RepoPickerStepView {...(loading ? REPO_PICKER_LOADING : REPO_PICKER)} />
      </NewAppSection>
      {!loading && (
        <NewAppSection
          title="Manifest"
          description="The astrolift.toml that says what this app runs."
        >
          <ManifestPreviewStepView
            state={state}
            setState={setState}
            fetchState="found"
            fetchError=""
            editing={editing}
            setEditing={setEditing}
            refetch={async () => {}}
            maskedEnvValues={false}
          />
        </NewAppSection>
      )}
    </>
  );
}

function Run({
  details = DETAILS_STATE,
  strategy,
}: {
  details?: AppDetailsFields;
  strategy?: Partial<AppDeployStrategyFields>;
}) {
  const [a, setA] = React.useState(details);
  const [b, setB] = React.useState<AppDeployStrategyFields>({
    ...DEPLOY_STRATEGY_STATE,
    ...strategy,
  });
  return (
    <>
      <NewAppSection title="App" description="Name, slug, description, and its project.">
        <AppDetailsStepView {...DETAILS} state={a} setState={setA} />
      </NewAppSection>
      <NewAppSection title="Deploy strategy" description="How and when deploys run.">
        <AppDeployStrategyStep state={b} setState={setB} approverSlot={null} />
      </NewAppSection>
    </>
  );
}

/** Step 1, a repo picked and its manifest loaded. */
export const Full: Story = {
  render: () => (
    <NewAppPage {...page} step={1} onBack={undefined}>
      <Source />
    </NewAppPage>
  ),
};

/** Source connections still loading: Continue waits. */
export const Loading: Story = {
  render: () => (
    <NewAppPage {...page} step={1} onBack={undefined} continueDisabled>
      <Source loading />
    </NewAppPage>
  ),
};

export const RunStep: Story = {
  render: () => (
    <NewAppPage {...page} step={2}>
      <Run />
    </NewAppPage>
  ),
};

/** Fields to fix: each says what beside itself, and Continue waits. */
export const RunInvalid: Story = {
  render: () => (
    <NewAppPage {...page} step={2} continueDisabled>
      <Run
        details={{ ...DETAILS_STATE, name: "", slug: "API_Gateway!" }}
        strategy={{ triggerMode: "cron", cronExpression: "every day" }}
      />
    </NewAppPage>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Give the app a name.")).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: /Continue/ })).toBeDisabled();
  },
};

/** Step 3: what will be created and what will deploy. */
export const Review: Story = {
  render: () => (
    <NewAppPage {...page} step={3} continueLabel="Create app">
      <ReviewSubmitStepView {...REVIEW} />
    </NewAppPage>
  ),
};

/** Create running: Back and Cancel are gone, the step links are inert. */
export const Creating: Story = {
  render: () => (
    <NewAppPage
      {...page}
      step={3}
      continueLabel="Creating..."
      busy
      onBack={undefined}
      onCancel={undefined}
    >
      <ReviewSubmitStepView {...REVIEW_SUBMITTING} />
    </NewAppPage>
  ),
};

/** The create was refused: the reason shows in the step, not in a toast. */
export const CreateFailed: Story = {
  render: () => (
    <NewAppPage {...page} step={3} continueLabel="Create app">
      <ReviewSubmitStepView {...REVIEW_FAILED} />
    </NewAppPage>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <NewAppPage {...page} step={3} continueLabel="Create app">
      <ReviewSubmitStepView {...REVIEW_LONG} />
    </NewAppPage>
  ),
};

export const LongStringsRun: Story = {
  render: () => (
    <NewAppPage {...page} step={2}>
      <NewAppSection title={LONG} description={`${LONG} ${LONG}`}>
        <AppDetailsStepView
          {...LONG_DETAILS}
          state={{ ...DETAILS_STATE, name: LONG, description: `${LONG} ${LONG}` }}
          setState={noop}
        />
      </NewAppSection>
    </NewAppPage>
  ),
};

/** The narrowest supported width: the steps wrap, nothing scrolls sideways. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <NewAppPage {...page} step={3} continueLabel="Create app">
        <ReviewSubmitStepView {...REVIEW_LONG} />
      </NewAppPage>
    </div>
  ),
};

export const Width768Run: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <NewAppPage {...page} step={2}>
        <Run />
      </NewAppPage>
    </div>
  ),
};
