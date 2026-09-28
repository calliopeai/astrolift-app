import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AGENT_WIZARD, LONG, LONG_AGENT_WIZARD_STEPS } from "./agents-wizard-a.fixtures";
import { AgentWizardShell } from "./AgentWizardShell";

/**
 * The register-agent-repo wizard chrome. It has no data of its own, so there
 * is no empty or error state; NextLoading is the closest "loading".
 */
const meta: Meta = {
  title: "Screens/Agents/New/AgentWizardShell",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const body = <p className="text-muted-foreground text-sm">Step body goes here.</p>;

/** Mid-wizard: step 1 done and clickable. */
export const Full: Story = {
  render: () => <AgentWizardShell {...AGENT_WIZARD}>{body}</AgentWizardShell>,
};

export const FirstStep: Story = {
  render: () => (
    <AgentWizardShell {...AGENT_WIZARD} step={1} onBack={undefined} nextDisabled>
      {body}
    </AgentWizardShell>
  ),
};

/** Last step, submit in flight. */
export const NextLoading: Story = {
  render: () => (
    <AgentWizardShell {...AGENT_WIZARD} step={4} nextLabel="Register agents" nextLoading>
      {body}
    </AgentWizardShell>
  ),
};

export const HiddenNext: Story = {
  render: () => (
    <AgentWizardShell {...AGENT_WIZARD} step={4} hideNext onCancel={undefined}>
      {body}
    </AgentWizardShell>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AgentWizardShell
      {...AGENT_WIZARD}
      steps={LONG_AGENT_WIZARD_STEPS}
      title={`Register ${LONG}`}
      description={`${LONG} ${LONG} ${LONG}`}
    >
      {body}
    </AgentWizardShell>
  ),
};
