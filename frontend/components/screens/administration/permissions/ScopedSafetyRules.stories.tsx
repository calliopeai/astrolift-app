import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SCOPED_SAFETY_FIXTURE } from "./fixtures";
import { ScopedSafetyRules } from "./ScopedSafetyRules";

const meta: Meta<typeof ScopedSafetyRules> = {
  title: "Screens/Administration/Permissions/ScopedSafetyRules",
  component: ScopedSafetyRules,
  parameters: { layout: "padded" },
  args: SCOPED_SAFETY_FIXTURE,
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Rules: Story = {};
export const NoRules: Story = { args: { rules: [] } };
export const Saving: Story = { args: { saving: true } };
export const Refused: Story = {
  args: { error: "only the organization can turn a gate off; a narrower scope can only add to it" },
};
export const PeopleSearching: Story = {
  args: { people: { ...SCOPED_SAFETY_FIXTURE.people, query: "dan", loading: true } },
};
