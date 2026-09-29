import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { EVERY_KIND, INVALID_POLICY, LONG_POLICY } from "@/components/access/fixtures";

import { editPolicyProps, LONG_POLICIES, NEW_POLICY } from "./fixtures";
import { PolicyEditorScreen } from "./PolicyEditorScreen";

const meta: Meta = {
  title: "Screens/Administration/Access/PolicyEditorScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const FILLED = { name: "No prod deploys after hours" };

/** New, step 1: name, slug, scope, description. */
export const NewDetails: Story = { render: () => <PolicyEditorScreen {...NEW_POLICY} /> };

/** New, step 2: the sentence builder. */
export const NewRule: Story = {
  render: () => <PolicyEditorScreen {...NEW_POLICY} initialStep={1} initialDraft={FILLED} />,
};

/** Every condition kind the pickers cover, and a custom one kept verbatim. */
export const EveryConditionKind: Story = {
  render: () => (
    <PolicyEditorScreen
      {...NEW_POLICY}
      initialStep={1}
      initialDraft={{ ...FILLED, shape: EVERY_KIND }}
    />
  ),
};

/** A condition with an error: Continue stays on the rule and says why. */
export const InvalidCondition: Story = {
  render: () => (
    <PolicyEditorScreen
      {...NEW_POLICY}
      initialStep={1}
      initialDraft={{ ...FILLED, shape: INVALID_POLICY }}
    />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Continue" }));
    await expect(c.getByRole("alert")).toHaveTextContent(/Fix/);
  },
};

/** New, step 3: the sentence, the not-enforced note, and the simulation it cannot run yet. */
export const NewReview: Story = {
  render: () => <PolicyEditorScreen {...NEW_POLICY} initialStep={2} initialDraft={FILLED} />,
};

/** A blank name: the error sits under the field. */
export const MissingName: Story = {
  render: () => <PolicyEditorScreen {...NEW_POLICY} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Continue" }));
    await expect(c.getByText("Name the policy.")).toBeInTheDocument();
  },
};

export const Saving: Story = {
  render: () => <PolicyEditorScreen {...NEW_POLICY} saving initialStep={2} initialDraft={FILLED} />,
};

/** An existing policy, opened on its rule. */
export const Edit: Story = {
  render: () => <PolicyEditorScreen {...editPolicyProps()} initialStep={1} />,
};

/** Editing changed the rule: the review shows what it was. */
export const EditReviewChanged: Story = {
  render: () => (
    <PolicyEditorScreen
      {...editPolicyProps()}
      initialStep={2}
      initialDraft={{ shape: EVERY_KIND }}
    />
  ),
};

/** A save refused because someone saved first: the reason sits on the review. */
export const EditConflict: Story = {
  render: () => (
    <PolicyEditorScreen
      {...editPolicyProps({
        onSave: async () => "Version mismatch: this policy is at version 3, you edited version 1.",
      })}
      initialStep={2}
    />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Save policy" }));
    await expect(await c.findByRole("alert")).toHaveTextContent("Version mismatch");
  },
};

/** A viewer without org.update reads the policy as its sentence. */
export const ReadOnly: Story = {
  render: () => <PolicyEditorScreen {...editPolicyProps({ canManage: false })} />,
};

export const Loading: Story = {
  render: () => <PolicyEditorScreen {...editPolicyProps({ policy: null, loading: true })} />,
};

export const Error: Story = {
  render: () => (
    <PolicyEditorScreen
      {...editPolicyProps({ policy: null, error: { message: "upstream timed out after 30s" } })}
    />
  ),
};

export const NotFound: Story = {
  render: () => <PolicyEditorScreen {...editPolicyProps({ policy: null, id: "pol-gone" })} />,
};

export const LongStrings: Story = {
  render: () => (
    <PolicyEditorScreen
      {...editPolicyProps({ policy: LONG_POLICIES[0] })}
      initialStep={2}
      initialDraft={{ shape: LONG_POLICY }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <PolicyEditorScreen
        {...editPolicyProps({ policy: LONG_POLICIES[0] })}
        initialStep={1}
        initialDraft={{ shape: LONG_POLICY }}
      />
    </div>
  ),
};
