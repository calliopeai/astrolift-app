import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, LONG_WIZARD_STEPS, WIZARD } from "./apps-list-wizard-shell.fixtures";
import { WizardShell } from "./WizardShell";

/**
 * The register-app wizard chrome. It has no data of its own, so there is no
 * empty or error state; NextLoading is the closest "loading".
 */
const meta: Meta = {
  title: "Screens/Apps/New/WizardShell",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const body = <p className="text-muted-foreground text-sm">Step body goes here.</p>;

/** Mid-wizard: steps 1-2 done and clickable. */
export const Full: Story = {
  render: () => <WizardShell {...WIZARD}>{body}</WizardShell>,
};

export const FirstStep: Story = {
  render: () => (
    <WizardShell {...WIZARD} step={1} onBack={undefined} nextDisabled>
      {body}
    </WizardShell>
  ),
};

/** Last step, submit in flight. */
export const NextLoading: Story = {
  render: () => (
    <WizardShell {...WIZARD} step={5} nextLabel="Register app" nextLoading>
      {body}
    </WizardShell>
  ),
};

export const HiddenNext: Story = {
  render: () => (
    <WizardShell {...WIZARD} step={5} hideNext onCancel={undefined}>
      {body}
    </WizardShell>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <WizardShell
      {...WIZARD}
      steps={LONG_WIZARD_STEPS}
      title={`Register ${LONG}`}
      description={`${LONG} ${LONG} ${LONG}`}
    >
      {body}
    </WizardShell>
  ),
};
