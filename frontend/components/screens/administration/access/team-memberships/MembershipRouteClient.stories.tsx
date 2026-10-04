import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Team, Person, ReadOnly } from "./TeamMembershipPanels.stories";
const meta = {
  title: "Screens/Administration/Access/MembershipRoutes",
  parameters: { layout: "padded" },
} satisfies Meta;
export default meta;
export const TeamPresentation: StoryObj<typeof meta> = { ...Team };
export const PersonPresentation: StoryObj<typeof meta> = { ...Person };
export const DerivedAccess: StoryObj<typeof meta> = { ...ReadOnly };
