import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { UserMenu } from "@/components/shell/UserMenu";

const meta: Meta = { title: "Shell/UserMenu" };
export default meta;
export const Expanded: StoryObj = {
  render: () => (
    <div className="w-56">
      <UserMenu fullName="Leo Mata" email="leo@example.com" onSignOut={() => {}} />
    </div>
  ),
};
export const Collapsed: StoryObj = {
  render: () => (
    <div className="w-12">
      <UserMenu fullName="Leo Mata" email="leo@example.com" onSignOut={() => {}} collapsed />
    </div>
  ),
};
