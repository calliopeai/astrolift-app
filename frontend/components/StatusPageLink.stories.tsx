import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { StatusPageLink } from "@/components/StatusPageLink";

const meta: Meta = { title: "Patterns/Shell/StatusPageLink" };
export default meta;

export const Default: StoryObj = {
  render: () => <StatusPageLink url="https://status.example.com" />,
};
