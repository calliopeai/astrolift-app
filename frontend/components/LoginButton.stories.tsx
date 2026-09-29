import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import LoginButton from "@/components/LoginButton";

const meta: Meta = { title: "Patterns/Auth/LoginButton" };
export default meta;
export const Default: StoryObj = { render: () => <LoginButton /> };
