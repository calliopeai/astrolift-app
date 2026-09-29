import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";

const meta: Meta = { title: "Patterns/Settings/LanguageSwitcher" };
export default meta;

export const Default: StoryObj = {
  render: () => <LanguageSwitcher locale="en" pending={false} onChange={() => {}} />,
};
export const Switching: StoryObj = {
  render: () => <LanguageSwitcher locale="en" pending onChange={() => {}} />,
};
