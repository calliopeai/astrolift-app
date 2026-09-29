import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { BrandMark } from "@/components/shell/BrandMark";

const meta: Meta = { title: "Shell/BrandMark" };
export default meta;
export const Expanded: StoryObj = { render: () => <BrandMark /> };
export const Collapsed: StoryObj = { render: () => <BrandMark collapsed /> };
