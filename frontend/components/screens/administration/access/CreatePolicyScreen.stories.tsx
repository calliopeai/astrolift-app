import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CreatePolicyScreen } from "./CreatePolicyScreen";
import { CREATE_POLICY } from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Access/CreatePolicyScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const LONG = "a-very-long-identifier-that-keeps-going-well-past-any-sensible-column-width";
const FILLED = {
  name: "No prod deploys after hours",
  scopeLevel: "ORG" as const,
  effect: "DENY" as const,
  actionPattern: "app.deploy",
};

/** Step 1, the default DENY / ORG / "*" draft. */
export const Rule: Story = { render: () => <CreatePolicyScreen {...CREATE_POLICY} /> };

/** Step 2, the time-window template. */
export const Conditions: Story = {
  render: () => <CreatePolicyScreen {...CREATE_POLICY} initialStep={1} initialDraft={FILLED} />,
};

/** Step 3: what will be written, with the DENY warning. */
export const Review: Story = {
  render: () => <CreatePolicyScreen {...CREATE_POLICY} initialStep={2} initialDraft={FILLED} />,
};

export const Creating: Story = {
  render: () => (
    <CreatePolicyScreen {...CREATE_POLICY} creating initialStep={2} initialDraft={FILLED} />
  ),
};

/** A draft whose conditions are not a JSON array: the error sits under the field. */
export const InvalidConditions: Story = {
  render: () => (
    <CreatePolicyScreen
      {...CREATE_POLICY}
      initialStep={1}
      initialDraft={{ ...FILLED, conditionsText: '{ "kind": "time_window" ' }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <CreatePolicyScreen
      {...CREATE_POLICY}
      initialStep={2}
      initialDraft={{
        name: `Deny every production deploy outside the change window ${LONG}`,
        actionPattern: `arn:aws:iam::123456789012:role/astrolift/${LONG}/${LONG}`,
        effect: "DENY",
      }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <CreatePolicyScreen {...CREATE_POLICY} />
    </div>
  ),
};
