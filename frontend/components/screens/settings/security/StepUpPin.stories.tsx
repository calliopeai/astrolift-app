import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { StepUpPinView, type StepUpPinViewProps } from "./StepUpPin";

/** The step-up PIN card (#2399) in each state a person can find it in. */
const meta: Meta = { title: "Screens/Settings/Security/StepUpPin" };
export default meta;

type Story = StoryObj;

function props(patch: Partial<StepUpPinViewProps> = {}): StepUpPinViewProps {
  return {
    status: { orgAllows: true, isSet: false, locked: false },
    loading: false,
    saving: false,
    onSet: async () => true,
    onClear: async () => {},
    ...patch,
  };
}

/** No PIN yet: the set form. */
export const Empty: Story = { render: () => <StepUpPinView {...props()} /> };

export const Loading: Story = {
  render: () => <StepUpPinView {...props({ status: null, loading: true })} />,
};

/** A PIN is set: change or clear it. */
export const Set: Story = {
  render: () => (
    <StepUpPinView {...props({ status: { orgAllows: true, isSet: true, locked: false } })} />
  ),
};

/** Too many wrong PINs: a new one unlocks it. */
export const Locked: Story = {
  render: () => (
    <StepUpPinView {...props({ status: { orgAllows: true, isSet: true, locked: true } })} />
  ),
};

/** The org turned PINs off after this one was set: it can only be cleared. */
export const OrgDisallows: Story = {
  render: () => (
    <StepUpPinView {...props({ status: { orgAllows: false, isSet: true, locked: false } })} />
  ),
};

/** A PIN that is not 6 to 8 digits is refused before it is sent. */
export const FormatError: Story = {
  render: () => <StepUpPinView {...props()} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("PIN"), "123");
    await userEvent.type(canvas.getByLabelText("Confirm PIN"), "123");
    await userEvent.click(canvas.getByRole("button", { name: "Set PIN" }));
    await expect(await canvas.findByRole("alert")).toHaveTextContent("6 to 8 digits");
  },
};
