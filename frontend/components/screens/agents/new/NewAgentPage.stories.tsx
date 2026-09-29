import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentDiscoveryStepView } from "./AgentDiscoveryStep";
import { AgentProjectStepView } from "./AgentProjectStep";
import { AgentRepoPickerStepView } from "./AgentRepoPickerStep";
import { AgentReviewSubmitStepView } from "./AgentReviewSubmitStep";
import {
  DISCOVERY_EMPTY,
  DISCOVERY_ERROR,
  DISCOVERY_FOUND,
  DISCOVERY_LONG,
  DISCOVERY_SCANNING,
  LONG,
  PROJECT,
} from "./agents-wizard-a.fixtures";
import {
  AGENT_REPO_PICKER,
  AGENT_REPO_PICKER_LONG,
  AGENT_REPO_PICKER_UNPICKED,
  AGENT_REVIEW,
  AGENT_REVIEW_FAILED,
  AGENT_REVIEW_SUBMITTING,
} from "./agents-wizard-b.fixtures";
import { NewAgentPage, NewAgentSection } from "./NewAgentPage";
import { partErrors } from "./use-new-agent";

const meta: Meta = {
  title: "Screens/Agents/New/NewAgentPage",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const noop = () => {};
const flow = { onStep: noop, onBack: noop, onContinue: noop, onCancel: noop };
const allInvalid = { repo: false, discover: false, project: false };

/** Source: a repo picked and its agents found. */
export const Full: Story = {
  render: () => (
    <NewAgentPage step={1} {...flow} onBack={undefined}>
      <NewAgentSection title="Repository">
        <AgentRepoPickerStepView {...AGENT_REPO_PICKER} />
      </NewAgentSection>
      <NewAgentSection title="Agents found">
        <AgentDiscoveryStepView {...DISCOVERY_FOUND} />
      </NewAgentSection>
    </NewAgentPage>
  ),
};

/** The scan is running. */
export const Loading: Story = {
  render: () => (
    <NewAgentPage step={1} {...flow} onBack={undefined}>
      <NewAgentSection title="Repository">
        <AgentRepoPickerStepView {...AGENT_REPO_PICKER} />
      </NewAgentSection>
      <NewAgentSection title="Agents found">
        <AgentDiscoveryStepView {...DISCOVERY_SCANNING} />
      </NewAgentSection>
    </NewAgentPage>
  ),
};

/** Continue pressed with nothing picked: the error sits under the part that needs it. */
export const MissingRepo: Story = {
  render: () => (
    <NewAgentPage step={1} {...flow} onBack={undefined}>
      <NewAgentSection title="Repository" error={partErrors(1, allInvalid, true, false).repo}>
        <AgentRepoPickerStepView {...AGENT_REPO_PICKER_UNPICKED} />
      </NewAgentSection>
    </NewAgentPage>
  ),
};

/** The repo has no agent manifests: Continue says why it cannot go on. */
export const Empty: Story = {
  render: () => (
    <NewAgentPage step={1} {...flow} onBack={undefined}>
      <NewAgentSection title="Repository">
        <AgentRepoPickerStepView {...AGENT_REPO_PICKER} />
      </NewAgentSection>
      <NewAgentSection
        title="Agents found"
        error={partErrors(1, { ...allInvalid, repo: true }, true, true).discover}
      >
        <AgentDiscoveryStepView {...DISCOVERY_EMPTY} />
      </NewAgentSection>
    </NewAgentPage>
  ),
};

export const ErrorState: Story = {
  render: () => (
    <NewAgentPage step={1} {...flow} onBack={undefined}>
      <NewAgentSection title="Repository">
        <AgentRepoPickerStepView {...AGENT_REPO_PICKER} />
      </NewAgentSection>
      <NewAgentSection title="Agents found">
        <AgentDiscoveryStepView {...DISCOVERY_ERROR} />
      </NewAgentSection>
    </NewAgentPage>
  ),
};

/** Configure, with Continue pressed before a project was picked. */
export const Configure: Story = {
  render: () => (
    <NewAgentPage step={2} {...flow}>
      <NewAgentSection title="Project" error={partErrors(2, allInvalid, true, true).project}>
        <AgentProjectStepView {...PROJECT} state={{ projectId: "" }} setState={noop} />
      </NewAgentSection>
    </NewAgentPage>
  ),
};

export const Review: Story = {
  render: () => (
    <NewAgentPage step={3} {...flow} continueLabel="Register agents">
      <AgentReviewSubmitStepView {...AGENT_REVIEW} />
    </NewAgentPage>
  ),
};

/** Registering: Back and Cancel are gone and Continue spins. */
export const Submitting: Story = {
  render: () => (
    <NewAgentPage
      step={3}
      {...flow}
      onBack={undefined}
      onCancel={undefined}
      busy
      continueLabel="Registering..."
    >
      <AgentReviewSubmitStepView {...AGENT_REVIEW_SUBMITTING} />
    </NewAgentPage>
  ),
};

export const SubmitFailed: Story = {
  render: () => (
    <NewAgentPage step={3} {...flow} continueLabel="Register agents">
      <AgentReviewSubmitStepView {...AGENT_REVIEW_FAILED} />
    </NewAgentPage>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <NewAgentPage step={1} {...flow} onBack={undefined}>
      <NewAgentSection title="Repository" error={`${LONG} ${"x".repeat(200)}`}>
        <AgentRepoPickerStepView {...AGENT_REPO_PICKER_LONG} />
      </NewAgentSection>
      <NewAgentSection title="Agents found">
        <AgentDiscoveryStepView {...DISCOVERY_LONG} />
      </NewAgentSection>
    </NewAgentPage>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <NewAgentPage step={1} {...flow} onBack={undefined}>
        <NewAgentSection title="Repository">
          <AgentRepoPickerStepView {...AGENT_REPO_PICKER_LONG} />
        </NewAgentSection>
        <NewAgentSection title="Agents found">
          <AgentDiscoveryStepView {...DISCOVERY_LONG} />
        </NewAgentSection>
      </NewAgentPage>
    </div>
  ),
};
