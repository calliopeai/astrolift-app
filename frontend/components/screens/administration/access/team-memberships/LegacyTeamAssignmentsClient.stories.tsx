import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { LegacyAssignmentsPanel } from "./LegacyTeamAssignmentsClient";
const meta = {
  title: "Screens/Administration/Access/LegacyAssignments",
  component: LegacyAssignmentsPanel,
  args: { slug: "engineering", loading: false, error: null, children: null },
} satisfies Meta<typeof LegacyAssignmentsPanel>;
export default meta;
export const Refused: StoryObj<typeof meta> = {
  args: { error: "Current organization/team authority could not be confirmed" },
};
export const Loading: StoryObj<typeof meta> = { args: { loading: true } };
